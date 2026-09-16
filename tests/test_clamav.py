from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multimodal_rag.security.clamav import ClamAVScanError, ClamAVScanner, ClamAVThreatDetected


class FakeConnection:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent: list[bytes] = []
        self.timeout: float | None = None

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        response, self.response = self.response, b""
        return response


class ClamAVScannerTests(unittest.TestCase):
    def test_disabled_scanner_does_not_open_or_send_the_file(self) -> None:
        scanner = ClamAVScanner(enabled=False)

        scanner.scan(Path("does-not-exist.pdf"))

    def test_clean_verdict_streams_raw_file_to_clamd(self) -> None:
        connection = FakeConnection(b"stream: OK\0")
        scanner = ClamAVScanner(enabled=True, timeout_seconds=12)
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "document.pdf"
            path.write_bytes(b"raw pdf bytes")
            with patch("multimodal_rag.security.clamav.socket.create_connection", return_value=connection):
                scanner.scan(path)

        self.assertEqual(connection.timeout, 12)
        self.assertEqual(connection.sent[0], b"zINSTREAM\0")
        self.assertEqual(connection.sent[1], struct.pack("!I", len(b"raw pdf bytes")))
        self.assertEqual(connection.sent[2], b"raw pdf bytes")
        self.assertEqual(connection.sent[3], struct.pack("!I", 0))

    def test_malware_verdict_is_reported(self) -> None:
        connection = FakeConnection(b"stream: Eicar-Test-Signature FOUND\0")
        scanner = ClamAVScanner(enabled=True)
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "document.pdf"
            path.write_bytes(b"raw pdf bytes")
            with patch("multimodal_rag.security.clamav.socket.create_connection", return_value=connection):
                with self.assertRaises(ClamAVThreatDetected):
                    scanner.scan(path)

    def test_connection_failure_blocks_an_enabled_scan(self) -> None:
        scanner = ClamAVScanner(enabled=True)
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "document.pdf"
            path.write_bytes(b"raw pdf bytes")
            with patch("multimodal_rag.security.clamav.socket.create_connection", side_effect=OSError("offline")):
                with self.assertRaises(ClamAVScanError):
                    scanner.scan(path)


if __name__ == "__main__":
    unittest.main()
