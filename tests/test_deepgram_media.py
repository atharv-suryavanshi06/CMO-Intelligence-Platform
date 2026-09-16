from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multimodal_rag.ingestion.media.deepgram import DeepgramTranscriber, DeepgramTranscriptionError, ingest_media


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class DeepgramMediaTests(unittest.TestCase):
    def test_missing_api_key_fails_before_uploading_media(self) -> None:
        with self.assertRaisesRegex(DeepgramTranscriptionError, "not configured"):
            DeepgramTranscriber(api_key=None).transcribe(Path("clip.mp3"))

    def test_mp4_transcript_writes_embedding_compatible_timestamped_chunks(self) -> None:
        payload = {"results": {"channels": [{"detected_language": "en", "alternatives": [{"transcript": "Fallback transcript."}]}], "utterances": [{"start": 1.5, "end": 4.25, "speaker": 2, "transcript": "A useful marketing insight."}]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "meeting.mp4"
            media.write_bytes(b"video bytes")
            with patch("multimodal_rag.ingestion.media.deepgram.urlopen", return_value=_Response(payload)) as open_mock:
                paths = ingest_media(media, root / "artifacts", DeepgramTranscriber(api_key="test-key"), "source-hash")

            request = open_mock.call_args.args[0]
            self.assertIn("Token test-key", request.headers["Authorization"])
            self.assertEqual(request.headers["Content-type"], "video/mp4")
            chunks = json.loads(paths.chunks_json.read_text(encoding="utf-8"))
            self.assertEqual(chunks[0]["metadata"]["extraction_method"], "deepgram")
            self.assertEqual(chunks[0]["metadata"]["timestamp_start_seconds"], 1.5)
            self.assertIn("Speaker 2", chunks[0]["chunk_text"])

