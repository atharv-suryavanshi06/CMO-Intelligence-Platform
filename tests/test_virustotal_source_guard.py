from __future__ import annotations

import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from multimodal_rag.web_search.models import SearchResult
from multimodal_rag.web_search.source_guard import (
    RateLimiter,
    SourceGuardService,
    TtlSafetyCache,
    UrlSafetyResult,
    UrlSafetyStatus,
    build_virustotal_url_id,
)


class Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return json.dumps(self.payload).encode()


def vt(malicious=0, suspicious=0):
    return {"data": {"attributes": {"last_analysis_stats": {"malicious": malicious, "suspicious": suspicious, "harmless": 1, "undetected": 1}}}}


class VirusTotalSourceGuardTests(unittest.TestCase):
    def setUp(self):
        # cache_ttl_seconds=0 disables the cross-call TTL cache so each
        # patched urlopen scenario observes a fresh network call, matching
        # what these tests assert on call counts.
        self.guard = SourceGuardService(virustotal_api_key="vt-key", lakera_guard_api_key="guard-key", cache_ttl_seconds=0)
        self.result = SearchResult(title="Result", url="https://example.test/a", content="external evidence")

    def test_url_id_uses_unpadded_urlsafe_base64(self):
        self.assertEqual(build_virustotal_url_id("https://example.test/a"), "aHR0cHM6Ly9leGFtcGxlLnRlc3QvYQ")

    def test_malicious_and_suspicious_thresholds_block_before_content(self):
        for payload in (vt(malicious=1), vt(suspicious=2)):
            with self.subTest(payload=payload), patch("multimodal_rag.web_search.source_guard.urlopen", return_value=Response(payload)) as request:
                self.assertFalse(self.guard.validate_result(self.result).allowed)
            self.assertEqual(request.call_count, 1)

    def test_clean_url_and_safe_guard_are_allowed(self):
        with patch("multimodal_rag.web_search.source_guard.urlopen", side_effect=[Response(vt()), Response({"flagged": False})]):
            self.assertTrue(self.guard.validate_result(self.result).allowed)

    def test_unknown_rate_limit_timeout_and_invalid_responses_fail_closed(self):
        cases = [
            (HTTPError("url", 404, "", {}, None), None),
            # A 429 must still fail closed, but is labeled RATE_LIMITED rather
            # than plain ERROR so a quota problem is distinguishable in logs.
            (HTTPError("url", 429, "", {}, None), UrlSafetyStatus.RATE_LIMITED),
            (URLError("timeout"), None),
            (Response({}), None),
        ]
        for failure, expected_status in cases:
            with self.subTest(failure=failure), patch("multimodal_rag.web_search.source_guard.urlopen", side_effect=failure):
                outcome = self.guard.validate_result(self.result)
                self.assertFalse(outcome.allowed)
                if expected_status is not None:
                    self.assertEqual(outcome.url_status, expected_status)

    def test_duplicate_urls_are_checked_once_per_filter_call(self):
        duplicate = self.result.model_copy(update={"title": "Duplicate"})
        # filter_results checks unique URLs and per-result content
        # concurrently, so this counts calls by destination rather than
        # relying on a fixed response order.
        lock = threading.Lock()
        call_counts = {"virustotal": 0, "lakera": 0}

        def fake_urlopen(request, timeout=None):
            with lock:
                if "virustotal.com" in request.full_url:
                    call_counts["virustotal"] += 1
                    return Response(vt())
                call_counts["lakera"] += 1
                return Response({"flagged": False})

        with patch("multimodal_rag.web_search.source_guard.urlopen", side_effect=fake_urlopen):
            self.assertEqual(self.guard.filter_results([self.result, duplicate]), [self.result, duplicate])
        self.assertEqual(call_counts["virustotal"], 1)
        self.assertEqual(call_counts["lakera"], 2)

    def test_same_host_different_paths_share_one_virustotal_lookup(self):
        # Default lookup_granularity="domain": two distinct URLs on the same
        # host must resolve to a single VirusTotal request.
        other_path = self.result.model_copy(update={"url": "https://example.test/b", "title": "Other path"})
        lock = threading.Lock()
        call_counts = {"virustotal": 0, "lakera": 0}

        def fake_urlopen(request, timeout=None):
            with lock:
                if "virustotal.com" in request.full_url:
                    call_counts["virustotal"] += 1
                    return Response(vt())
                call_counts["lakera"] += 1
                return Response({"flagged": False})

        with patch("multimodal_rag.web_search.source_guard.urlopen", side_effect=fake_urlopen):
            approved = self.guard.filter_results([self.result, other_path])
        self.assertEqual(approved, [self.result, other_path])
        self.assertEqual(call_counts["virustotal"], 1)
        self.assertEqual(call_counts["lakera"], 2)

    def test_rate_limited_lookup_is_reported_without_a_network_call(self):
        guard = SourceGuardService(virustotal_api_key="vt-key", lakera_guard_api_key="guard-key", cache_ttl_seconds=0, rate_limit_per_minute=0)
        with patch("multimodal_rag.web_search.source_guard.urlopen") as open_mock:
            latency: dict[str, float] = {}
            approved = guard.filter_results([self.result], latency=latency)
        open_mock.assert_not_called()
        self.assertEqual(approved, [])
        self.assertEqual(latency["virustotal_rate_limited"], 1)

    def test_cross_request_cache_avoids_a_repeat_virustotal_lookup(self):
        guard = SourceGuardService(virustotal_api_key="vt-key", lakera_guard_api_key="guard-key")
        with patch("multimodal_rag.web_search.source_guard.urlopen", side_effect=[Response(vt()), Response({"flagged": False})]):
            first_latency: dict[str, float] = {}
            self.assertTrue(guard.filter_results([self.result], latency=first_latency)[0])
        self.assertEqual(first_latency["virustotal_lookups"], 1)
        self.assertEqual(first_latency["virustotal_cache_hits"], 0)

        # A second question, same guard instance (as in production, where
        # SourceGuardService is a singleton): the domain verdict is cached,
        # so only the (per-result) Lakera content check hits the network.
        with patch("multimodal_rag.web_search.source_guard.urlopen", side_effect=[Response({"flagged": False})]):
            second_latency: dict[str, float] = {}
            self.assertTrue(guard.filter_results([self.result], latency=second_latency)[0])
        self.assertEqual(second_latency["virustotal_lookups"], 0)
        self.assertEqual(second_latency["virustotal_cache_hits"], 1)


class TtlSafetyCacheTests(unittest.TestCase):
    def test_hit_then_expiry(self):
        cache = TtlSafetyCache(ttl_seconds=100, negative_ttl_seconds=5)
        safe = UrlSafetyResult(url="example.test", status=UrlSafetyStatus.SAFE)
        cache.put("domain:example.test", safe)
        self.assertEqual(cache.get("domain:example.test"), safe)
        self.assertEqual(cache.stats()["hits"], 1)

        with patch("multimodal_rag.web_search.source_guard.time.monotonic", return_value=999999.0):
            self.assertIsNone(cache.get("domain:example.test"))

    def test_negative_results_use_the_short_ttl(self):
        cache = TtlSafetyCache(ttl_seconds=100, negative_ttl_seconds=0)
        error = UrlSafetyResult(url="example.test", status=UrlSafetyStatus.ERROR)
        cache.put("domain:example.test", error)
        # negative_ttl_seconds=0 means "don't cache transient failures at all"
        self.assertIsNone(cache.get("domain:example.test"))

    def test_ttl_seconds_zero_disables_caching_entirely(self):
        cache = TtlSafetyCache(ttl_seconds=0, negative_ttl_seconds=60)
        safe = UrlSafetyResult(url="example.test", status=UrlSafetyStatus.SAFE)
        cache.put("domain:example.test", safe)
        self.assertIsNone(cache.get("domain:example.test"))

    def test_lru_eviction_past_max_entries(self):
        cache = TtlSafetyCache(ttl_seconds=100, max_entries=2)
        for name in ("a", "b", "c"):
            cache.put(f"domain:{name}", UrlSafetyResult(url=name, status=UrlSafetyStatus.SAFE))
        self.assertIsNone(cache.get("domain:a"))  # evicted first
        self.assertIsNotNone(cache.get("domain:b"))
        self.assertIsNotNone(cache.get("domain:c"))


class RateLimiterTests(unittest.TestCase):
    def test_refuses_once_the_window_is_full(self):
        limiter = RateLimiter(max_calls=2, per_seconds=60.0)
        self.assertTrue(limiter.acquire())
        self.assertTrue(limiter.acquire())
        self.assertFalse(limiter.acquire())

    def test_zero_budget_always_refuses(self):
        self.assertFalse(RateLimiter(max_calls=0).acquire())

    def test_slots_free_up_after_the_window_elapses(self):
        limiter = RateLimiter(max_calls=1, per_seconds=60.0)
        self.assertTrue(limiter.acquire())
        self.assertFalse(limiter.acquire())
        with patch("multimodal_rag.web_search.source_guard.time.monotonic", return_value=time.monotonic() + 61):
            self.assertTrue(limiter.acquire())
