"""Fail-open LangSmith observability for backend execution paths."""

from __future__ import annotations

import inspect
import logging
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, is_dataclass
from functools import wraps
from typing import Any, Callable, Iterator, Mapping, TypeVar, get_type_hints

try:
    from langsmith import Client, trace as langsmith_trace, tracing_context
    from langsmith.run_helpers import get_current_run_tree
except ImportError:  # pragma: no cover - only used in minimal installs.
    Client = None  # type: ignore[assignment]
    langsmith_trace = None  # type: ignore[assignment]
    tracing_context = None  # type: ignore[assignment]
    get_current_run_tree = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])

SUPPORTED_TRACE_PATHS = frozenset(
    {
        "/retrieve",
        "/answer",
        "/agents/market-intelligence",
        "/agents/market-strategy",
        "/agents/meeting-preparation",
        "/agents/meeting-follow-up",
        "/web-search/research",
    }
)
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api[_-]?key|authorization|cookie|database[_-]?url|password|secret|session|token)(?:$|_)",
    re.IGNORECASE,
)
_SKIP_TYPES = {"Request", "Response", "BackgroundTasks", "UploadFile"}
_active_observability: ContextVar["LangSmithObservability | None"] = ContextVar(
    "active_langsmith_observability", default=None
)


def _redacted_key(key: Any) -> bool:
    return bool(_SECRET_KEY.search(str(key)))


def serialize_for_trace(value: Any, *, key: str | None = None) -> Any:
    """Convert application values to JSON-safe trace data without secrets."""
    if key is not None and _redacted_key(key):
        return "[REDACTED]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return "[BINARY_DATA_OMITTED]"
    if type(value).__name__ in _SKIP_TYPES:
        return "[FRAMEWORK_OBJECT_OMITTED]"
    if hasattr(value, "model_dump"):
        try:
            return serialize_for_trace(value.model_dump(mode="json"))
        except Exception:
            return "[MODEL_SERIALIZATION_FAILED]"
    if is_dataclass(value) and not isinstance(value, type):
        return serialize_for_trace(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(item_key): serialize_for_trace(item_value, key=str(item_key))
            for item_key, item_value in value.items()
            if not _redacted_key(item_key)
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [serialize_for_trace(item) for item in value]
    return str(value)


@dataclass(frozen=True)
class LangSmithObservability:
    """Runtime LangSmith configuration shared by one API application."""

    enabled: bool
    project_name: str
    environment: str
    sampling_rate: float
    client: Any = None

    @classmethod
    def create(
        cls,
        *,
        enabled: bool,
        api_key: str | None,
        endpoint: str | None,
        project_name: str,
        environment: str,
        sampling_rate: float,
    ) -> "LangSmithObservability":
        if not enabled:
            return cls(False, project_name, environment, sampling_rate)
        if Client is None:
            logger.warning("LangSmith tracing requested but the package is unavailable; tracing disabled")
            return cls(False, project_name, environment, sampling_rate)
        if not api_key:
            logger.warning("LANGSMITH_TRACING is enabled but LANGSMITH_API_KEY is not set; tracing disabled")
            return cls(False, project_name, environment, sampling_rate)
        try:
            client = Client(
                api_url=endpoint or None,
                api_key=api_key,
                tracing_sampling_rate=sampling_rate,
            )
        except Exception:
            logger.exception("Could not initialize LangSmith client; tracing disabled")
            return cls(False, project_name, environment, sampling_rate)
        return cls(True, project_name, environment, sampling_rate, client)

    @contextmanager
    def request_context(self, *, path: str, method: str) -> Iterator[None]:
        if not self.enabled or path not in SUPPORTED_TRACE_PATHS or tracing_context is None:
            yield
            return
        metadata = {"environment": self.environment, "http_method": method, "http_path": path}
        tags = ["cmo-intelligence", "api", method.lower(), path.strip("/").replace("/", ".")]
        try:
            context = tracing_context(
                project_name=self.project_name,
                client=self.client,
                enabled=True,
                tags=tags,
                metadata=metadata,
            )
            context.__enter__()
        except Exception:
            logger.exception("Could not start LangSmith request context for %s %s; continuing without tracing", method, path)
            yield
            return

        try:
            yield
        except BaseException:
            exc_type, exc_value, traceback = sys.exc_info()
            try:
                context.__exit__(exc_type, exc_value, traceback)
            except Exception:
                logger.exception("Could not close failed LangSmith request context")
            raise
        else:
            try:
                context.__exit__(None, None, None)
            except Exception:
                logger.exception("Could not close LangSmith request context")


def _trace_inputs(function: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        bound = inspect.signature(function).bind_partial(*args, **kwargs)
        values = dict(bound.arguments)
    except (TypeError, ValueError):
        values = {"args": args, "kwargs": kwargs}
    values.pop("self", None)
    return serialize_for_trace(values)


def _public_signature(function: Callable[..., Any]) -> inspect.Signature:
    signature = inspect.signature(function)
    try:
        hints = get_type_hints(function)
    except (NameError, TypeError):
        return signature
    parameters = [
        parameter.replace(annotation=hints.get(parameter.name, parameter.annotation))
        for parameter in signature.parameters.values()
    ]
    return signature.replace(parameters=parameters, return_annotation=hints.get("return", signature.return_annotation))

def observed(name: str, *, run_type: str = "chain") -> Callable[[F], F]:
    """Trace a function only while an enabled request context is active."""

    def decorator(function: F) -> F:
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await _async_call_with_trace(function, name, run_type, args, kwargs)

            async_wrapper.__signature__ = _public_signature(function)
            return async_wrapper  # type: ignore[return-value]

        @wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return _sync_call_with_trace(function, name, run_type, args, kwargs)

        wrapper.__signature__ = _public_signature(function)
        return wrapper  # type: ignore[return-value]

    return decorator


def _start_trace(name: str, run_type: str, inputs: dict[str, Any]):
    active = _active_observability.get()
    if active is None or not active.enabled or langsmith_trace is None:
        return None, None
    try:
        context = langsmith_trace(
            name,
            run_type,
            inputs=inputs,
            project_name=active.project_name,
            client=active.client,
            tags=["cmo-intelligence", run_type],
            metadata={"environment": active.environment},
        )
        return context, context.__enter__()
    except Exception:
        logger.exception("Could not start LangSmith span %s; continuing without tracing", name)
        return None, None


def _finish_trace(context: Any, run: Any, result: Any = None, error: BaseException | None = None) -> None:
    if context is None:
        return
    if error is None and run is not None:
        try:
            run.end(outputs={"result": serialize_for_trace(result)})
        except Exception:
            logger.exception("Could not record LangSmith span output")
        try:
            context.__exit__(None, None, None)
        except Exception:
            logger.exception("Could not close LangSmith span")
    else:
        try:
            context.__exit__(type(error), error, error.__traceback__ if error else None)
        except Exception:
            logger.exception("Could not close failed LangSmith span")


def _sync_call_with_trace(function: Callable[..., Any], name: str, run_type: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    context, run = _start_trace(name, run_type, _trace_inputs(function, args, kwargs))
    if context is None:
        return function(*args, **kwargs)
    try:
        result = function(*args, **kwargs)
    except BaseException as exc:
        _finish_trace(context, run, error=exc)
        raise
    _finish_trace(context, run, result=result)
    return result


async def _async_call_with_trace(function: Callable[..., Any], name: str, run_type: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    context, run = _start_trace(name, run_type, _trace_inputs(function, args, kwargs))
    if context is None:
        return await function(*args, **kwargs)
    try:
        result = await function(*args, **kwargs)
    except BaseException as exc:
        _finish_trace(context, run, error=exc)
        raise
    _finish_trace(context, run, result=result)
    return result


def activate(observability: LangSmithObservability) -> Any:
    return _active_observability.set(observability)


def reset(token: Any) -> None:
    _active_observability.reset(token)


@contextmanager
def active_request_context(observability: LangSmithObservability, *, path: str, method: str) -> Iterator[None]:
    token = activate(observability)
    try:
        with observability.request_context(path=path, method=method):
            yield
    finally:
        reset(token)


def annotate_current_span(*, metadata: Mapping[str, Any] | None = None, tags: list[str] | None = None) -> None:
    """Add late-bound trace IDs, latency, and branch metadata to the active span."""
    if get_current_run_tree is None:
        return
    try:
        run = get_current_run_tree()
        if run is None:
            return
        if metadata:
            run.add_metadata(serialize_for_trace(dict(metadata)))
        if tags:
            run.add_tags(tags)
    except Exception:
        logger.exception("Could not annotate the active LangSmith span")
