"""Fail-closed validation for untrusted external web-search results."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from multimodal_rag.web_search.models import SearchResult
from multimodal_rag.rag.observability import observed

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class SearchSecurityResult:
    """Internal security outcome for one provider-normalized search result."""

    url_safe: bool = False
    content_safe: bool = False
    allowed: bool = False
    url_security_provider: str = "virustotal"
    url_status: str | None = None
    threat_type: str | None = None
    malicious_count: int = 0
    suspicious_count: int = 0
    reputation: int | None = None
    categories: list[str] = field(default_factory=list)
    guard_flagged: bool = False
    guard_reasons: list[str] = field(default_factory=list)
    url_latency_ms: float = 0.0
    guard_latency_ms: float = 0.0


class SourceGuard(Protocol):
    def filter_results(self, results: list[SearchResult], *, latency: dict[str, float] | None = None) -> list[SearchResult]:
        """Return only external results that passed all security checks, in the same order as `results`.

        Callers (e.g. the market-intelligence agent) rely on this order to
        rebuild per-query ranks after a single combined security pass over
        results pooled from multiple searches. ``latency``, when given,
        accumulates ``virustotal_ms``/``lakera_guard_ms`` durations and related
        counters for the caller's end-of-run latency report.
        """


class UrlSafetyStatus(StrEnum):
    SAFE = "safe"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    ERROR = "error"
    # Our own request budget was exhausted (or VirusTotal returned 429) -
    # this is a LABEL for *why* no verdict was obtained, not a third allow/
    # deny branch. It must never be treated as safe: `validate_result` gates
    # on `is not UrlSafetyStatus.SAFE`, so RATE_LIMITED fails closed exactly
    # like ERROR. Kept distinct from ERROR purely so it's observable.
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class UrlSafetyResult:
    url: str
    status: UrlSafetyStatus
    malicious_count: int = 0
    suspicious_count: int = 0
    reputation: int | None = None
    categories: list[str] = field(default_factory=list)
    provider: str = "virustotal"
    checked_at: datetime | None = None
    latency_ms: float = 0.0


def build_virustotal_url_id(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


class TtlSafetyCache:
    """Process-lifetime, bounded, thread-safe cache of URL/domain safety verdicts.

    Positive verdicts (SAFE/BLOCKED) are cached for `ttl_seconds` - reputation
    moves on a scale of days, and a long TTL is what makes a small
    requests-per-minute VirusTotal budget workable across many questions.
    Transient outcomes (UNKNOWN/ERROR/RATE_LIMITED) are cached only for
    `negative_ttl_seconds`: long enough to absorb a burst of duplicate
    lookups within one request, short enough that the next question always
    retries rather than being blackholed by a brief outage or a stale
    rate-limit. Passing `ttl_seconds<=0` disables caching entirely (used by
    tests that must observe a fresh network call every time).
    """

    def __init__(self, *, ttl_seconds: float = 86400.0, negative_ttl_seconds: float = 60.0, max_entries: int = 4096) -> None:
        self.ttl_seconds = ttl_seconds
        self.negative_ttl_seconds = negative_ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[UrlSafetyResult, float]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> UrlSafetyResult | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            result, expires_at = entry
            if now >= expires_at:
                del self._entries[key]
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return result

    def put(self, key: str, result: UrlSafetyResult) -> None:
        if self.ttl_seconds <= 0:
            return  # caching disabled entirely
        ttl = self.ttl_seconds if result.status in (UrlSafetyStatus.SAFE, UrlSafetyStatus.BLOCKED) else self.negative_ttl_seconds
        if ttl <= 0:
            return
        expires_at = time.monotonic() + ttl
        with self._lock:
            self._entries[key] = (result, expires_at)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"size": len(self._entries), "hits": self._hits, "misses": self._misses}


class RateLimiter:
    """Non-blocking sliding-window call budget, shared across threads.

    Keeps VirusTotal usage under a free-tier quota (e.g. 4/minute) without
    ever retrying a 429 with a sleep - `acquire()` either reserves a slot
    immediately or refuses, so a busy window costs zero extra latency and
    zero wasted quota. The caller treats a refusal exactly like a failed
    lookup (fail closed).
    """

    def __init__(self, *, max_calls: int, per_seconds: float = 60.0) -> None:
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()

    def acquire(self) -> bool:
        if self.max_calls <= 0:
            return False
        with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= self.per_seconds:
                self._calls.popleft()
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                return True
            return False


class SourceGuardService:
    """Validate URLs and content before external data enters agent context.

    Missing credentials, malformed responses, and network failures deny the
    individual result.  This deliberately prevents a security outage from
    becoming an implicit allow decision.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        virustotal_api_key: str | None = None,
        lakera_guard_api_key: str | None = None,
        lakera_project_id: str | None = None,
        timeout_seconds: float = 10.0,
        max_malicious: int = 0,
        max_suspicious: int = 1,
        max_workers: int = 8,
        url_max_workers: int = 4,
        lookup_granularity: str = "domain",
        cache_ttl_seconds: float = 86400.0,
        cache_negative_ttl_seconds: float = 60.0,
        cache_max_entries: int = 4096,
        rate_limit_per_minute: int = 4,
    ) -> None:
        self.enabled = enabled
        self.virustotal_api_key = virustotal_api_key
        self.lakera_guard_api_key = lakera_guard_api_key
        self.lakera_project_id = lakera_project_id
        self.timeout_seconds = timeout_seconds
        self.max_workers = max_workers
        self.url_max_workers = url_max_workers
        self.lookup_granularity = lookup_granularity if lookup_granularity in {"domain", "url"} else "domain"
        self.max_malicious = max_malicious
        self.max_suspicious = max_suspicious
        self._safety_cache = TtlSafetyCache(ttl_seconds=cache_ttl_seconds, negative_ttl_seconds=cache_negative_ttl_seconds, max_entries=cache_max_entries)
        self._rate_limiter = RateLimiter(max_calls=rate_limit_per_minute, per_seconds=60.0)

    def clear_safety_cache(self) -> None:
        """Drop every cached VirusTotal verdict. Mainly for test isolation."""
        self._safety_cache.clear()

    def validate_result(self, result: SearchResult, url_cache: dict[str, UrlSafetyResult] | None = None) -> SearchSecurityResult:
        if not self.enabled:
            # Explicitly disabled deployments are outside this security path.
            return SearchSecurityResult(url_safe=True, content_safe=True, allowed=True)
        url_cache_hit = result.url in (url_cache or {})
        if url_cache_hit:
            url_result = url_cache[result.url]
        else:
            key = self._safety_key(result.url)
            url_result = self._lookup_safety(key)[0] if key is not None else UrlSafetyResult(url=result.url, status=UrlSafetyStatus.ERROR)
        if url_cache is not None:
            url_cache[result.url] = url_result
        # A cached URL result was already timed on the first lookup; do not
        # double-count its latency against this (duplicate-URL) result.
        url_latency_ms = 0.0 if url_cache_hit else url_result.latency_ms
        if url_result.status is not UrlSafetyStatus.SAFE:
            outcome = SearchSecurityResult(url_status=url_result.status, threat_type="url_reputation", malicious_count=url_result.malicious_count, suspicious_count=url_result.suspicious_count, reputation=url_result.reputation, categories=url_result.categories, url_latency_ms=url_latency_ms)
            self._log(result, outcome)
            return outcome
        content_safe, flagged, reasons, guard_latency_ms = self.validate_content(result.content, result.url)
        outcome = SearchSecurityResult(
            url_safe=True,
            content_safe=content_safe,
            allowed=content_safe,
            url_status=url_result.status,
            malicious_count=url_result.malicious_count,
            suspicious_count=url_result.suspicious_count,
            reputation=url_result.reputation,
            categories=url_result.categories,
            guard_flagged=flagged,
            guard_reasons=reasons,
            url_latency_ms=url_latency_ms,
            guard_latency_ms=guard_latency_ms,
        )
        self._log(result, outcome)
        return outcome

    @observed("source_guard.filter_results", run_type="tool")
    def filter_results(self, results: list[SearchResult], *, latency: dict[str, float] | None = None) -> list[SearchResult]:
        if not results:
            return []
        if not self.enabled:
            # No network calls in this path, so no concurrency is needed.
            return [result for result in results if self.validate_result(result).allowed]

        # VirusTotal and Lakera Guard are both slow, blocking HTTP round-trips
        # (commonly 0.5-2s+ each, more under VirusTotal's free-tier rate
        # limits). Checking every result one at a time made per-question
        # latency scale linearly with result count. Resolve every *unique*
        # safety key (a URL or its domain - see `lookup_granularity`)
        # concurrently first, consulting the cross-request TTL cache before
        # any network call, then run the remaining per-result content
        # screening concurrently too.
        urls = [result.url for result in results]
        wall_start = time.perf_counter()
        url_cache, lookups, cache_hits = self._resolve_safety(urls)
        virustotal_wall_ms = (time.perf_counter() - wall_start) * 1000

        guard_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max(1, min(len(results), self.max_workers))) as pool:
            outcomes = list(pool.map(lambda result: self.validate_result(result, url_cache), results))
        lakera_guard_wall_ms = (time.perf_counter() - guard_start) * 1000

        rate_limited = sum(1 for url_result in url_cache.values() if url_result.status is UrlSafetyStatus.RATE_LIMITED)
        if rate_limited:
            # One aggregated warning per batch, not one per URL - a 20-result
            # burst under a busy budget would otherwise emit 20 lines.
            logger.warning(
                "Source Guard VirusTotal rate limited: %d of %d lookup(s) refused (budget=%d/min); those results were blocked without a reputation verdict",
                rate_limited,
                len(url_cache),
                self._rate_limiter.max_calls,
            )

        if latency is not None:
            latency["virustotal_ms"] = latency.get("virustotal_ms", 0.0) + sum(url_result.latency_ms for url_result in url_cache.values())
            latency["lakera_guard_ms"] = latency.get("lakera_guard_ms", 0.0) + sum(outcome.guard_latency_ms for outcome in outcomes)
            latency["virustotal_wall_ms"] = latency.get("virustotal_wall_ms", 0.0) + virustotal_wall_ms
            latency["lakera_guard_wall_ms"] = latency.get("lakera_guard_wall_ms", 0.0) + lakera_guard_wall_ms
            latency["virustotal_lookups"] = latency.get("virustotal_lookups", 0.0) + lookups
            latency["virustotal_cache_hits"] = latency.get("virustotal_cache_hits", 0.0) + cache_hits
            latency["virustotal_rate_limited"] = latency.get("virustotal_rate_limited", 0.0) + rate_limited

        return [result for result, outcome in zip(results, outcomes) if outcome.allowed]

    def _safety_key(self, url: str) -> str | None:
        """Cache/lookup key for one result URL: "domain:<host>" or "url:<url>".

        None when the URL is not a valid external URL (validate_result then
        fails closed without any network call).
        """
        if not self._valid_external_url(url):
            return None
        if self.lookup_granularity == "url":
            return f"url:{url.strip()}"
        hostname = (urlsplit(url.strip()).hostname or "").lower().rstrip(".")
        if hostname.startswith("www."):
            hostname = hostname[4:]
        if not hostname:
            return None
        return f"domain:{hostname}"

    def _lookup_safety(self, key: str) -> tuple[UrlSafetyResult, bool]:
        """Resolve one safety key, consulting the cache first. Returns (result, was_cache_hit)."""
        cached = self._safety_cache.get(key)
        if cached is not None:
            return cached, True
        granularity, _, target = key.partition(":")
        result = self.validate_domain(target) if granularity == "domain" else self.validate_url(target)
        self._safety_cache.put(key, result)
        return result, False

    def _resolve_safety(self, urls: list[str]) -> tuple[dict[str, UrlSafetyResult], int, int]:
        """Map each result URL to a safety verdict.

        Issues at most one network lookup per distinct safety key across this
        call (so a URL repeated across queries/results is checked once), and
        zero when the shared TTL cache already holds a fresh verdict from an
        earlier question. Returns (url -> verdict, lookups_issued, cache_hits).
        """
        key_by_url = {url: self._safety_key(url) for url in urls}
        unique_keys = list(dict.fromkeys(key for key in key_by_url.values() if key is not None))

        resolved: dict[str, UrlSafetyResult] = {}
        lookups = 0
        cache_hits = 0
        if unique_keys:
            with ThreadPoolExecutor(max_workers=max(1, min(len(unique_keys), self.url_max_workers))) as pool:
                for key, (result, was_cache_hit) in zip(unique_keys, pool.map(self._lookup_safety, unique_keys)):
                    resolved[key] = result
                    if was_cache_hit:
                        cache_hits += 1
                    else:
                        lookups += 1

        error_result = UrlSafetyResult(url="", status=UrlSafetyStatus.ERROR)
        by_url = {url: (resolved.get(key, error_result) if key is not None else error_result) for url, key in key_by_url.items()}
        return by_url, lookups, cache_hits

    def validate_url(self, url: str) -> UrlSafetyResult:
        checked_at = datetime.now(timezone.utc)
        if not self._valid_external_url(url) or not self.virustotal_api_key:
            return UrlSafetyResult(url=url, status=UrlSafetyStatus.ERROR, checked_at=checked_at)
        if not self._rate_limiter.acquire():
            logger.warning("Source Guard VirusTotal request budget exhausted (max=%d/min); blocking without a reputation verdict host=%s", self._rate_limiter.max_calls, urlsplit(url).hostname)
            return UrlSafetyResult(url=url, status=UrlSafetyStatus.RATE_LIMITED, checked_at=checked_at)
        request = Request(
            f"https://www.virustotal.com/api/v3/urls/{build_virustotal_url_id(url)}",
            headers={"x-apikey": self.virustotal_api_key, "Accept": "application/json"},
            method="GET",
        )
        return self._request_virustotal(url, request, checked_at)

    def validate_domain(self, domain: str) -> UrlSafetyResult:
        checked_at = datetime.now(timezone.utc)
        if not domain or not self.virustotal_api_key:
            return UrlSafetyResult(url=domain, status=UrlSafetyStatus.ERROR, checked_at=checked_at)
        if not self._rate_limiter.acquire():
            logger.warning("Source Guard VirusTotal request budget exhausted (max=%d/min); blocking without a reputation verdict domain=%s", self._rate_limiter.max_calls, domain)
            return UrlSafetyResult(url=domain, status=UrlSafetyStatus.RATE_LIMITED, checked_at=checked_at)
        request = Request(
            f"https://www.virustotal.com/api/v3/domains/{domain}",
            headers={"x-apikey": self.virustotal_api_key, "Accept": "application/json"},
            method="GET",
        )
        return self._request_virustotal(domain, request, checked_at)

    def _request_virustotal(self, target: str, request: Request, checked_at: datetime) -> UrlSafetyResult:
        request_start = time.perf_counter()
        try:
            payload = self._request_json(request)
        except HTTPError as exc:
            latency_ms = (time.perf_counter() - request_start) * 1000
            if exc.code == 429:
                status = UrlSafetyStatus.RATE_LIMITED
            elif exc.code == 404:
                status = UrlSafetyStatus.UNKNOWN
            else:
                status = UrlSafetyStatus.ERROR
            if exc.code in (401, 403):
                logger.warning("Source Guard VirusTotal credential rejected (HTTP %d) - check the configured API key", exc.code)
            return UrlSafetyResult(url=target, status=status, checked_at=checked_at, latency_ms=latency_ms)
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            latency_ms = (time.perf_counter() - request_start) * 1000
            logger.warning("Source Guard VirusTotal validation failed for target=%s", target)
            return UrlSafetyResult(url=target, status=UrlSafetyStatus.ERROR, checked_at=checked_at, latency_ms=latency_ms)
        latency_ms = (time.perf_counter() - request_start) * 1000
        attributes = payload.get("data", {}).get("attributes") if isinstance(payload.get("data"), dict) else None
        return self._safety_from_attributes(target, attributes, checked_at=checked_at, latency_ms=latency_ms)

    def _status_for(self, malicious: int, suspicious: int) -> UrlSafetyStatus:
        return UrlSafetyStatus.BLOCKED if malicious > self.max_malicious or suspicious > self.max_suspicious else UrlSafetyStatus.SAFE

    def _safety_from_attributes(self, target: str, attributes: Any, *, checked_at: datetime, latency_ms: float) -> UrlSafetyResult:
        """Map a VirusTotal `data.attributes` object (URL or domain shape - both share
        `last_analysis_stats`/`reputation`/`categories`) to a UrlSafetyResult."""
        stats = attributes.get("last_analysis_stats") if isinstance(attributes, dict) else None
        if not isinstance(stats, dict):
            return UrlSafetyResult(url=target, status=UrlSafetyStatus.ERROR, checked_at=checked_at, latency_ms=latency_ms)
        malicious, suspicious = stats.get("malicious"), stats.get("suspicious")
        if isinstance(malicious, bool) or not isinstance(malicious, int) or isinstance(suspicious, bool) or not isinstance(suspicious, int):
            return UrlSafetyResult(url=target, status=UrlSafetyStatus.ERROR, checked_at=checked_at, latency_ms=latency_ms)
        reputation = attributes.get("reputation")
        categories = attributes.get("categories")
        normalized_categories = list(categories.values()) if isinstance(categories, dict) and all(isinstance(value, str) for value in categories.values()) else []
        status = self._status_for(malicious, suspicious)
        return UrlSafetyResult(url=target, status=status, malicious_count=malicious, suspicious_count=suspicious, reputation=reputation if isinstance(reputation, int) else None, categories=normalized_categories, checked_at=checked_at, latency_ms=latency_ms)

    def validate_content(self, content: str, url: str) -> tuple[bool, bool, list[str], float]:
        if not isinstance(content, str) or not content.strip() or not self.lakera_guard_api_key:
            return False, False, [], 0.0
        tool_call_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        payload: dict[str, Any] = {
            "breakdown": True,
            "messages": [{"role": "tool", "tool_call_id": tool_call_id, "content": content}],
        }
        if self.lakera_project_id:
            payload["project_id"] = self.lakera_project_id
        request = Request(
            "https://api.lakera.ai/v2/guard",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.lakera_guard_api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        request_start = time.perf_counter()
        try:
            response = self._request_json(request)
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            latency_ms = (time.perf_counter() - request_start) * 1000
            logger.warning("Source Guard AI Guardrails validation failed for URL host=%s", urlsplit(url).hostname)
            return False, False, [], latency_ms
        latency_ms = (time.perf_counter() - request_start) * 1000
        flagged = response.get("flagged")
        if not isinstance(flagged, bool):
            return False, False, [], latency_ms
        reasons = self._guard_reasons(response.get("breakdown"))
        return not flagged, flagged, reasons, latency_ms

    def _request_json(self, request: Request) -> dict[str, Any]:
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("security service returned a non-object response")
        return payload

    @staticmethod
    def _valid_external_url(url: str) -> bool:
        if not isinstance(url, str):
            return False
        parsed = urlsplit(url.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password

    @staticmethod
    def _guard_reasons(breakdown: Any) -> list[str]:
        if not isinstance(breakdown, (dict, list)):
            return []
        reasons: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key in ("detector", "detector_type", "reason", "category", "type"):
                    item = value.get(key)
                    if isinstance(item, str) and item not in reasons:
                        reasons.append(item)
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(breakdown)
        return reasons

    @staticmethod
    def _log(result: SearchResult, outcome: SearchSecurityResult) -> None:
        logger.info(
            "Source Guard provider=%s url=%s url_security_provider=%s url_status=%s malicious=%s suspicious=%s ai_guardrails=%s reasons=%s allowed=%s virustotal_ms=%.1f lakera_guard_ms=%.1f",
            result.source,
            result.url,
            outcome.url_security_provider,
            outcome.url_status,
            outcome.malicious_count,
            outcome.suspicious_count,
            "flagged" if outcome.guard_flagged else ("safe" if outcome.content_safe else "blocked"),
            outcome.guard_reasons,
            outcome.allowed,
            outcome.url_latency_ms,
            outcome.guard_latency_ms,
        )
