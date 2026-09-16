"""Minimal ClamAV ``clamd`` client for scanning uploaded files.

The scanner streams raw file bytes to the local ClamAV daemon. This keeps the
application independent of ClamAV's GPL library and avoids requiring the API
and scanner to share a filesystem path.
"""

from __future__ import annotations

import socket
import struct
from pathlib import Path


class ClamAVScanError(RuntimeError):
    """Raised when ClamAV cannot return a trustworthy scan verdict."""


class ClamAVThreatDetected(ClamAVScanError):
    """Raised when ClamAV identifies malware in an uploaded document."""


class ClamAVScanner:
    """Stream uploaded files to ``clamd`` using its INSTREAM protocol."""

    _CHUNK_SIZE = 1024 * 1024

    def __init__(
        self,
        *,
        enabled: bool = False,
        host: str = "127.0.0.1",
        port: int = 3310,
        timeout_seconds: float = 30.0,
        fail_closed: bool = True,
    ) -> None:
        self.enabled = enabled
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.fail_closed = fail_closed

    def scan(self, path: Path) -> None:
        """Raise on a malware verdict or an unavailable/invalid scanner response."""
        if not self.enabled:
            return

        try:
            with socket.create_connection((self.host, self.port), self.timeout_seconds) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.sendall(b"zINSTREAM\0")
                with path.open("rb") as uploaded_file:
                    while block := uploaded_file.read(self._CHUNK_SIZE):
                        connection.sendall(struct.pack("!I", len(block)))
                        connection.sendall(block)
                connection.sendall(struct.pack("!I", 0))
                response = self._read_response(connection)
        except OSError as exc:
            raise ClamAVScanError(f"ClamAV is unavailable: {exc}") from exc

        if response.endswith(" OK"):
            return
        if response.endswith(" FOUND"):
            raise ClamAVThreatDetected(response)
        raise ClamAVScanError(f"ClamAV returned an invalid scan response: {response}")

    @staticmethod
    def _read_response(connection: socket.socket) -> str:
        response = bytearray()
        while True:
            block = connection.recv(4096)
            if not block:
                break
            response.extend(block)
            if b"\0" in block:
                break
        if not response:
            raise ClamAVScanError("ClamAV closed the connection without a scan verdict.")
        return bytes(response).split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()
