"""Chat-scoped presentation generation through the Presenton API.

The presentation path deliberately has no dependency on RAG or any agent.  It
only transforms already-persisted, user-visible assistant payloads into a
Presenton request and proxies the resulting PPTX back to the authenticated
caller.
"""

from __future__ import annotations

import json
import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx


class PresentationError(RuntimeError):
    """Base class for presentation-generation failures."""


class PresentationConfigurationError(PresentationError):
    """Presenton is not configured for this API process."""


class PresentationProviderError(PresentationError):
    """Presenton returned an unusable or unsuccessful response."""


class PresentationTimeoutError(PresentationProviderError):
    """Presenton exceeded the configured request timeout."""


class PresentationArtifactError(PresentationProviderError):
    """Presenton did not return a valid PPTX artifact."""


class NoMeetingPreparationError(PresentationError):
    """The chat does not contain a completed meeting preparation response."""


class InvalidPresentationTemplateError(PresentationError):
    """The requested template is not available from Presenton."""


@dataclass(frozen=True)
class PresentationTemplate:
    id: str
    name: str
    total_layouts: int | None = None
    preview_url: str | None = None


@dataclass(frozen=True)
class PresentationContext:
    title: str
    markdown: str
    source_message_ids: tuple[str, ...]


@dataclass(frozen=True)
class PresentonGeneration:
    presentation_id: str
    path: str
    edit_path: str | None = None


@dataclass(frozen=True)
class PresentonTask:
    id: str
    status: str
    message: str
    generation: PresentonGeneration | None = None


@dataclass(frozen=True)
class GeneratedPresentation:
    content: bytes
    filename: str
    media_type: str = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class PresentationProvider(Protocol):
    def list_templates(self) -> list[PresentationTemplate]:
        """Return templates available to the configured Presenton account."""

    def generate(
        self,
        *,
        content: str,
        template_id: str,
        slide_count: int | None,
        instructions: str,
    ) -> PresentonGeneration:
        """Generate a presentation and return the provider artifact location."""

    def download(self, path: str) -> bytes:
        """Download and validate the provider's generated artifact."""

    def start_async(self, *, content: str, template_id: str, slide_count: int | None, instructions: str) -> PresentonTask:
        """Start an asynchronous generation task."""

    def task_status(self, task_id: str) -> PresentonTask:
        """Return the current provider task state."""


_EXCLUDED_PAYLOAD_FIELDS = {
    "agent",
    "agent_name",
    "status",
    "task_id",
    "trace_id",
    "user_id",
    "project_id",
    "chat_id",
    "sources",
    "errors",
    "limitations",
    "error_code",
    "reused_from_message_id",
    "missing_context",
    "clarification_question",
    "market_intelligence_scope",
}
_STRATEGY_STATUSES = {"completed", "partial"}
_PRESENTATION_INSTRUCTIONS = (
    "You are a presentation composer, not a research agent. Use only the supplied "
    "conversation context for factual and business content. You may reorganize, "
    "summarize, shorten, group related material, choose slide titles, and create "
    "visually appropriate sections. Do not browse, use web search, retrieve from "
    "documents, add unsupported strategic facts, fabricate metrics, competitors, "
    "customers, or market statistics, or introduce any business content not present "
    "in the supplied context. Generic non-factual visuals and icons are permitted."
)


def _label(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ")).strip().title()


def _compact(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if key in _EXCLUDED_PAYLOAD_FIELDS or item in (None, "", []):
                continue
            parts.append(f"{_label(str(key))}: {_compact(item)}")
        return "; ".join(parts)
    if isinstance(value, list):
        return "; ".join(_compact(item) for item in value if item not in (None, "", []))
    return str(value)


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in _EXCLUDED_PAYLOAD_FIELDS and value not in (None, "", [])
    }


def _render_value(value: Any) -> list[str]:
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            if item in (None, "", []):
                continue
            lines.append(f"- {_compact(item)}")
        return lines
    if isinstance(value, dict):
        return [f"- {_compact(value)}"]
    return [str(value)]


class PPTContextBuilder:
    """Build deterministic presentation content from one trusted chat record."""

    def build(self, chat: dict[str, Any]) -> PresentationContext:
        messages = chat.get("messages") or []
        latest_meeting_index: int | None = None
        latest_meeting_payload: dict[str, Any] | None = None

        for index, message in enumerate(messages):
            payload = message.get("payload") if isinstance(message, dict) else None
            if (
                isinstance(message, dict)
                and message.get("role") == "assistant"
                and isinstance(payload, dict)
                and payload.get("agent") == "meeting_preparation"
                and payload.get("status") == "completed"
            ):
                latest_meeting_index = index
                latest_meeting_payload = payload

        if latest_meeting_index is None or latest_meeting_payload is None:
            raise NoMeetingPreparationError(
                "No completed meeting preparation is available to create a presentation."
            )

        selected: list[tuple[str, dict[str, Any]]] = []
        meeting_id = str(messages[latest_meeting_index].get("id") or "")
        selected.append((meeting_id, latest_meeting_payload))

        seen_strategy_payloads: set[str] = set()
        for message in messages[latest_meeting_index + 1 :]:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            payload = message.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("agent") != "market_strategy" or payload.get("status") not in _STRATEGY_STATUSES:
                continue
            canonical = json.dumps(_public_payload(payload), sort_keys=True, default=str)
            if canonical in seen_strategy_payloads:
                continue
            seen_strategy_payloads.add(canonical)
            selected.append((str(message.get("id") or ""), payload))

        sections = [
            f"# {latest_meeting_payload.get('meeting_title') or 'Meeting Preparation'}",
            "\n## Meeting Preparation",
        ]
        sections.extend(self._render_payload(latest_meeting_payload))
        for message_id, payload in selected[1:]:
            sections.extend(["\n## Market Strategy Follow-up", *self._render_payload(payload)])

        title = str(latest_meeting_payload.get("meeting_title") or chat.get("title") or "Meeting Preparation").strip()
        return PresentationContext(
            title=title,
            markdown="\n".join(sections).strip(),
            source_message_ids=tuple(message_id for message_id, _ in selected if message_id),
        )

    @staticmethod
    def _render_payload(payload: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        for key, value in _public_payload(payload).items():
            heading = _label(str(key))
            if isinstance(value, (list, dict)):
                lines.append(f"\n### {heading}")
                lines.extend(_render_value(value))
            else:
                lines.extend([f"\n### {heading}", str(value)])
        return lines


class PresentonClient:
    """HTTP adapter for Presenton's current `/api/v1` REST API."""

    def __init__(self, base_url: str | None, api_key: str | None, timeout_seconds: float = 120.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _ensure_configured(self) -> None:
        if not self.base_url or not self.api_key:
            raise PresentationConfigurationError(
                "Presenton is not configured. Set PRESENTON_BASE_URL and PRESENTON_API_KEY."
            )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def list_templates(self) -> list[PresentationTemplate]:
        self._ensure_configured()
        try:
            response = httpx.get(
                f"{self.base_url}/api/v1/ppt/template/all",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={"include_defaults": "true"},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PresentationTimeoutError("Presenton template lookup timed out.") from exc
        except httpx.HTTPError as exc:
            raise PresentationProviderError("Presenton template lookup failed.") from exc
        self._raise_for_provider(response, "template lookup")
        try:
            body = response.json()
            if isinstance(body, list):
                template_items = body
            elif isinstance(body, dict) and isinstance(body.get("items"), list):
                template_items = body["items"]
            else:
                raise TypeError("template response does not contain a template list")
            templates = [
                PresentationTemplate(
                    id=str(item["id"]),
                    name=str(item.get("name") or item["id"]),
                    total_layouts=(
                        int(item["total_layouts"])
                        if item.get("total_layouts") is not None
                        else int(item["layout_count"])
                        if item.get("layout_count") is not None
                        else None
                    ),
                    preview_url=next(
                        (str(item[key]) for key in ("preview_url", "thumbnail_url", "thumbnail") if item.get(key)),
                        None,
                    ),
                )
                for item in template_items
                if isinstance(item, dict) and item.get("id")
            ]
        except (TypeError, ValueError, KeyError) as exc:
            raise PresentationProviderError("Presenton returned an invalid template list.") from exc
        if not templates:
            raise PresentationProviderError("Presenton returned no presentation templates.")
        return templates

    def generate(
        self,
        *,
        content: str,
        template_id: str,
        slide_count: int | None,
        instructions: str,
    ) -> PresentonGeneration:
        self._ensure_configured()
        payload = self._generation_payload(content, template_id, slide_count, instructions)
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/ppt/presentation/generate",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PresentationTimeoutError("Presenton presentation generation timed out.") from exc
        except httpx.HTTPError as exc:
            raise PresentationProviderError("Presenton presentation generation failed.") from exc
        self._raise_for_provider(response, "presentation generation")
        return self._generation_from_body(response.json())

    def start_async(
        self, *, content: str, template_id: str, slide_count: int | None, instructions: str
    ) -> PresentonTask:
        self._ensure_configured()
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/ppt/presentation/generate/async",
                headers=self._headers(),
                json=self._generation_payload(content, template_id, slide_count, instructions),
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PresentationTimeoutError("Presenton presentation submission timed out.") from exc
        except httpx.HTTPError as exc:
            raise PresentationProviderError("Presenton presentation submission failed.") from exc
        self._raise_for_provider(response, "presentation submission")
        return self._task_from_body(response.json())

    def task_status(self, task_id: str) -> PresentonTask:
        self._ensure_configured()
        try:
            response = httpx.get(
                f"{self.base_url}/api/v1/ppt/presentation/status/{task_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PresentationTimeoutError("Presenton presentation status check timed out.") from exc
        except httpx.HTTPError as exc:
            raise PresentationProviderError("Presenton presentation status check failed.") from exc
        self._raise_for_provider(response, "presentation status check")
        return self._task_from_body(response.json())

    def _generation_payload(self, content: str, template_id: str, slide_count: int | None, instructions: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "content": content,
            "instructions": instructions,
            "tone": "professional",
            "verbosity": "standard",
            "content_generation": "preserve",
            "web_search": False,
            "language": "English",
            "template": template_id,
            "include_title_slide": True,
            "export_as": "pptx",
        }
        if slide_count is not None:
            payload["n_slides"] = slide_count
        return payload

    @staticmethod
    def _generation_from_body(body: Any) -> PresentonGeneration:
        try:
            presentation_id = str(body["presentation_id"])
            path = str(body["path"])
            if not presentation_id or not path:
                raise ValueError("missing presentation id or path")
            return PresentonGeneration(presentation_id, path, body.get("edit_path"))
        except (TypeError, ValueError, KeyError) as exc:
            raise PresentationProviderError("Presenton returned an invalid generation response.") from exc

    @staticmethod
    def _task_from_body(body: Any) -> PresentonTask:
        try:
            task_id = str(body["id"])
            task_status = str(body["status"])
            message = str(body.get("message") or "Presentation generation is in progress.")
            data = body.get("data") or {}
            generation = PresentonClient._generation_from_body(data) if task_status == "completed" else None
            if not task_id or task_status not in {"pending", "completed", "error"}:
                raise ValueError("missing task id or invalid status")
            return PresentonTask(task_id, task_status, message, generation)
        except (TypeError, ValueError, KeyError) as exc:
            raise PresentationProviderError("Presenton returned an invalid generation task response.") from exc

    def download(self, path: str) -> bytes:
        self._ensure_configured()
        artifact_url = self._safe_artifact_url(path)
        try:
            response = httpx.get(
                artifact_url,
                headers=self._artifact_headers(artifact_url),
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PresentationTimeoutError("Presenton PPTX download timed out.") from exc
        except httpx.HTTPError as exc:
            raise PresentationProviderError("Presenton PPTX download failed.") from exc
        self._raise_for_provider(response, "PPTX download")
        content = response.content
        content_type = response.headers.get("content-type", "").lower()
        if not content or content[:2] != b"PK" or "json" in content_type or "html" in content_type:
            raise PresentationArtifactError("Presenton did not return a valid PPTX artifact.")
        return content

    def export_pdf(self, presentation_id: str) -> PresentonGeneration:
        self._ensure_configured()
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/ppt/presentation/export",
                headers=self._headers(),
                json={"id": presentation_id, "export_as": "pdf"},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PresentationTimeoutError("Presenton PDF preview export timed out.") from exc
        except httpx.HTTPError as exc:
            raise PresentationProviderError("Presenton PDF preview export failed.") from exc
        self._raise_for_provider(response, "PDF preview export")
        return self._generation_from_body(response.json())

    def download_pdf(self, path: str) -> bytes:
        self._ensure_configured()
        artifact_url = self._safe_artifact_url(path)
        try:
            response = httpx.get(artifact_url, headers=self._artifact_headers(artifact_url), timeout=self.timeout_seconds)
        except httpx.TimeoutException as exc:
            raise PresentationTimeoutError("Presenton PDF preview download timed out.") from exc
        except httpx.HTTPError as exc:
            raise PresentationProviderError("Presenton PDF preview download failed.") from exc
        self._raise_for_provider(response, "PDF preview download")
        content = response.content
        content_type = response.headers.get("content-type", "").lower()
        if not content or not content.startswith(b"%PDF") or "json" in content_type or "html" in content_type:
            raise PresentationArtifactError("Presenton did not return a valid PDF preview.")
        return content

    def _safe_artifact_url(self, path: str) -> str:
        if not isinstance(path, str) or not path.strip():
            raise PresentationArtifactError("Presenton returned an empty PPTX path.")
        base = urlparse(f"{self.base_url}/")
        returned = urlparse(path)
        if returned.scheme and returned.scheme.lower() not in {"http", "https"}:
            raise self._unsafe_artifact_error(returned)
        if not returned.path:
            raise self._unsafe_artifact_error(returned)

        if not returned.scheme:
            if returned.netloc:
                raise self._unsafe_artifact_error(returned)
            return urljoin(f"{self.base_url}/", path)

        if self._same_origin(returned, base):
            return path

        if self._is_internal_host(returned.hostname) and self._is_internal_host(base.hostname):
            # Self-hosted Presenton can serialize a container-network origin
            # such as http://presenton:5001. Rebuild only those internal paths
            # against the explicitly configured server.
            return urljoin(f"{self.base_url}/", returned.path.lstrip("/"))

        if self._is_public_https_url(returned):
            return path

        raise self._unsafe_artifact_error(returned)

    @staticmethod
    def _unsafe_artifact_error(parsed_url) -> PresentationArtifactError:
        """Expose a safe origin hint without leaking signed artifact queries."""
        scheme = parsed_url.scheme.lower() or "relative"
        hostname = parsed_url.hostname or "none"
        return PresentationArtifactError(f"Presenton returned an unsafe PPTX path ({scheme}://{hostname}).")

    def _artifact_headers(self, artifact_url: str) -> dict[str, str]:
        """Forward credentials only to the configured Presenton service."""
        returned = urlparse(artifact_url)
        base = urlparse(f"{self.base_url}/")
        if self._same_origin(returned, base) or self._is_presenton_cloud_artifact(returned, base):
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    @staticmethod
    def _same_origin(returned, base) -> bool:
        return (
            returned.scheme.lower() == base.scheme.lower()
            and returned.hostname == base.hostname
            and PresentonClient._effective_port(returned) == PresentonClient._effective_port(base)
        )

    @staticmethod
    def _is_presenton_cloud_artifact(returned, base) -> bool:
        """Allow HTTPS artifact hosts in the Presenton Cloud DNS boundary."""
        base_host = (base.hostname or "").lower()
        returned_host = (returned.hostname or "").lower()
        return (
            base_host == "presenton.ai" or base_host.endswith(".presenton.ai")
        ) and (
            returned_host == "presenton.ai" or returned_host.endswith(".presenton.ai")
        ) and returned.scheme.lower() == "https" and PresentonClient._effective_port(returned) == 443

    @staticmethod
    def _is_public_https_url(parsed_url) -> bool:
        """Accept signed CDN/storage links without treating them as provider APIs."""
        if (
            parsed_url.scheme.lower() != "https"
            or parsed_url.username is not None
            or parsed_url.password is not None
            or PresentonClient._effective_port(parsed_url) != 443
        ):
            return False
        hostname = parsed_url.hostname
        if not hostname or PresentonClient._is_internal_host(hostname):
            return False
        try:
            return ipaddress.ip_address(hostname).is_global
        except ValueError:
            return not hostname.lower().endswith((".local", ".localhost"))

    @staticmethod
    def _is_internal_host(hostname: str | None) -> bool:
        return (hostname or "").lower() in {"localhost", "0.0.0.0", "127.0.0.1", "::1", "presenton"}

    @staticmethod
    def _effective_port(parsed_url) -> int | None:
        if parsed_url.port is not None:
            return parsed_url.port
        return {"http": 80, "https": 443}.get(parsed_url.scheme.lower())

    @staticmethod
    def _raise_for_provider(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        detail = PresentonClient._provider_error_detail(response)
        suffix = f": {detail}" if detail else "."
        raise PresentationProviderError(
            f"Presenton {operation} failed with HTTP {response.status_code}{suffix}"
        )

    @staticmethod
    def _provider_error_detail(response: httpx.Response) -> str:
        """Extract a short provider diagnostic without returning raw payloads."""
        detail: Any = None
        try:
            body = response.json()
        except (TypeError, ValueError):
            body = None
        if isinstance(body, dict):
            for key in ("detail", "message", "error"):
                if body.get(key):
                    detail = body[key]
                    break
            if detail is None:
                detail = body
        elif body not in (None, ""):
            detail = body
        if detail is None:
            detail = getattr(response, "text", "")
        if not detail:
            return ""
        if not isinstance(detail, str):
            detail = json.dumps(detail, separators=(",", ":"), default=str)
        detail = re.sub(r"\s+", " ", detail).strip()
        return detail[:500]


class PresentationGenerationService:
    """Application service joining trusted chat context to Presenton."""

    def __init__(self, client: PresentationProvider, context_builder: PPTContextBuilder | None = None) -> None:
        self.client = client
        self.context_builder = context_builder or PPTContextBuilder()

    def list_templates(self) -> list[PresentationTemplate]:
        return self.client.list_templates()

    def start_from_chat(self, chat: dict[str, Any], *, template_id: str, slide_count: int | None) -> PresentonTask:
        templates = {template.id for template in self.client.list_templates()}
        if template_id not in templates:
            raise InvalidPresentationTemplateError("The selected presentation template is not available.")
        context = self.context_builder.build(chat)
        return self.client.start_async(
            content=context.markdown,
            template_id=template_id,
            slide_count=slide_count,
            instructions=_PRESENTATION_INSTRUCTIONS,
        )

    def task_status(self, task_id: str) -> PresentonTask:
        return self.client.task_status(task_id)

    def download_task(self, task_id: str) -> GeneratedPresentation:
        task = self.task_status(task_id)
        if task.status != "completed" or task.generation is None:
            raise PresentationProviderError("Presenton has not completed this presentation yet.")
        content = self.client.download(task.generation.path)
        return GeneratedPresentation(content=content, filename=f"presentation-{task.generation.presentation_id}.pptx")

    def preview_task(self, task_id: str) -> bytes:
        task = self.task_status(task_id)
        if task.status != "completed" or task.generation is None:
            raise PresentationProviderError("Presenton has not completed this presentation yet.")
        pdf = self.client.export_pdf(task.generation.presentation_id)
        return self.client.download_pdf(pdf.path)

    def generate_from_chat(
        self,
        chat: dict[str, Any],
        *,
        template_id: str,
        slide_count: int | None,
    ) -> GeneratedPresentation:
        context = self.context_builder.build(chat)
        templates = self.client.list_templates()
        if not any(template.id == template_id for template in templates):
            raise InvalidPresentationTemplateError("The selected presentation template is not available.")
        generation = self.client.generate(
            content=context.markdown,
            template_id=template_id,
            slide_count=slide_count,
            instructions=_PRESENTATION_INSTRUCTIONS,
        )
        content = self.client.download(generation.path)
        safe_title = re.sub(r"[^A-Za-z0-9]+", "_", context.title).strip("_") or "Meeting_Preparation"
        return GeneratedPresentation(content=content, filename=f"{safe_title[:100]}.pptx")
