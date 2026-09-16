"""Deepgram-backed MP3/MP4 transcription and transcript artifact writing."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class DeepgramTranscriptionError(RuntimeError):
    """Raised when Deepgram cannot return a usable media transcript."""


@dataclass
class MediaOutputPaths:
    """The subset of document output paths consumed by the API job worker."""

    document_dir: Path
    chunks_json: Path
    metadata_json: Path
    validation_report_json: Path
    audit_markdown: Path
    tables_dir: Path | None = None


@dataclass(frozen=True)
class DeepgramTranscriber:
    """Small HTTP client for Deepgram's pre-recorded media endpoint."""

    api_key: str | None
    model: str = "nova-3"
    timeout_seconds: float = 120.0

    def transcribe(self, media_path: Path) -> dict:
        if not self.api_key:
            raise DeepgramTranscriptionError("DEEPGRAM_API_KEY is not configured.")
        content_type = _content_type(media_path)
        query = urlencode({
            "model": self.model,
            "smart_format": "true",
            "punctuate": "true",
            "utterances": "true",
            "diarize": "true",
        })
        request = Request(
            f"https://api.deepgram.com/v1/listen?{query}",
            data=media_path.read_bytes(),
            headers={"Authorization": f"Token {self.api_key}", "Content-Type": content_type},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise DeepgramTranscriptionError(f"Deepgram rejected the media ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DeepgramTranscriptionError(f"Deepgram transcription request failed: {exc}") from exc
        if not _transcript_text(payload):
            raise DeepgramTranscriptionError("Deepgram returned no speech transcript.")
        return payload


def ingest_media(
    media_path: str | Path,
    output_dir: str | Path,
    transcriber: DeepgramTranscriber,
    source_sha256: str | None = None,
) -> MediaOutputPaths:
    """Transcribe one scanned MP3/MP4 and write the existing chunks.json contract."""
    media_path = Path(media_path)
    started = time.perf_counter()
    payload = transcriber.transcribe(media_path)
    document_id = f"media_{uuid.uuid4().hex[:12]}"
    utterances = payload.get("results", {}).get("utterances") or []
    chunks = [_chunk_from_utterance(document_id, media_path.name, utterance, index) for index, utterance in enumerate(utterances)]
    chunks = [chunk for chunk in chunks if chunk["chunk_text"]]
    if not chunks:
        chunks = [_chunk_from_text(document_id, media_path.name, _transcript_text(payload))]

    document_dir = Path(output_dir) / document_id
    document_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = document_dir / "chunks.json"
    metadata_path = document_dir / "metadata.json"
    validation_path = document_dir / "validation_report.json"
    audit_path = document_dir / "extracted_text_audit.md"
    chunks_path.write_text(json.dumps(chunks, indent=2), encoding="utf-8")
    metadata_path.write_text(json.dumps({
        "document_id": document_id,
        "source_file": media_path.name,
        "media_type": media_path.suffix.lower().lstrip("."),
        "source_sha256": source_sha256,
        "language": payload.get("results", {}).get("channels", [{}])[0].get("detected_language"),
        "total_chunks": len(chunks),
        "processing_time_seconds": round(time.perf_counter() - started, 3),
    }, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps({"provider": "deepgram", "status": "ok", "utterance_count": len(utterances)}, indent=2), encoding="utf-8")
    audit_path.write_text("# Media Transcript: " + media_path.name + "\n\n" + "\n\n".join(chunk["chunk_text"] for chunk in chunks), encoding="utf-8")
    logger.info("Transcribed '%s' as %s with %d chunks", media_path.name, document_id, len(chunks))
    return MediaOutputPaths(document_dir, chunks_path, metadata_path, validation_path, audit_path)


def _chunk_from_utterance(document_id: str, source_file: str, utterance: dict, index: int) -> dict:
    text = str(utterance.get("transcript") or "").strip()
    speaker = utterance.get("speaker")
    start = float(utterance.get("start") or 0)
    end = float(utterance.get("end") or start)
    return _chunk(document_id, source_file, text, index, start, end, speaker)


def _chunk_from_text(document_id: str, source_file: str, text: str) -> dict:
    return _chunk(document_id, source_file, text.strip(), 0, 0.0, 0.0, None)


def _chunk(document_id: str, source_file: str, text: str, index: int, start: float, end: float, speaker: int | None) -> dict:
    prefix = f"Speaker {speaker} ({start:.2f}s-{end:.2f}s): " if speaker is not None else ""
    return {"chunk_text": prefix + text, "metadata": {
        "chunk_id": f"{document_id}_transcript_{index:04d}", "document_id": document_id,
        "source_file": source_file, "page_numbers": [], "section_title": "Transcript",
        "layout_type": "transcript", "extraction_method": "deepgram", "ocr_confidence": None,
        "validation_status": "ok", "ingestion_timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "media-deepgram-v1", "source_region_ids": [],
        "timestamp_start_seconds": start, "timestamp_end_seconds": end, "speaker": speaker,
    }}


def _transcript_text(payload: dict) -> str:
    channels = payload.get("results", {}).get("channels") or []
    alternatives = channels[0].get("alternatives") if channels else []
    return str((alternatives or [{}])[0].get("transcript") or "").strip()


def _content_type(media_path: Path) -> str:
    return {".mp3": "audio/mpeg", ".mp4": "video/mp4"}.get(media_path.suffix.lower(), "application/octet-stream")
