"""Write-ahead log: append-only segment files of CRC-framed records.

Frame: ``<u32 length><u32 crc32c><payload>``, little endian. Segments roll
at ``segment_mb``; a background fsync runs every ``fsync_interval_ms``.
"""

from __future__ import annotations

import json
import os
import struct
import threading
import time
from pathlib import Path
from typing import Iterator, List, Optional

from ferrule import _native
from ferrule.jobs.model import Job
from ferrule.storage.base import Store

_HEADER = struct.Struct("<II")


class WalWriter:
    def __init__(self, cfg) -> None:
        self.dir = Path(cfg["dir"])
        self.segment_bytes = int(cfg["segment_mb"]) * 1024 * 1024
        self.fsync_s = cfg["fsync_interval_ms"] / 1000.0
        self._fh = None
        self._seg = 0
        self._written = 0
        self._last_sync = 0.0
        self._lock = threading.Lock()

    def _open(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.dir.glob("seg-*.wal"))
        self._seg = int(existing[-1].stem[4:]) + 1 if existing else 0
        self._fh = open(self.dir / f"seg-{self._seg:08d}.wal", "ab")
        self._written = 0

    def append(self, payload: bytes) -> None:
        with self._lock:
            if self._fh is None or self._written >= self.segment_bytes:
                if self._fh is not None:
                    self._fh.close()
                self._open()
            frame = _HEADER.pack(len(payload), _native.crc32c(payload)) + payload
            self._fh.write(frame)
            self._written += len(frame)
            now = time.monotonic()
            if now - self._last_sync >= self.fsync_s:
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._last_sync = now

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def read_segment(path: Path) -> Iterator[bytes]:
    data = Path(path).read_bytes()
    off = 0
    while off + _HEADER.size <= len(data):
        length, crc = _HEADER.unpack_from(data, off)
        off += _HEADER.size
        payload = data[off:off + length]
        if len(payload) < length or _native.crc32c(payload) != crc:
            break  # torn tail
        yield payload
        off += length


class WalStore(Store):
    """A store that *is* the log: state is rebuilt by replaying segments."""

    name = "wal"

    def __init__(self, cfg) -> None:
        self.writer = WalWriter(cfg["wal"])
        self._jobs = {}
        self._replay()

    def _replay(self) -> None:
        segs: List[Path] = sorted(self.writer.dir.glob("seg-*.wal")) if self.writer.dir.exists() else []
        for seg in segs:
            for payload in read_segment(seg):
                doc = json.loads(payload)
                if doc.get("_deleted"):
                    self._jobs.pop(doc["id"], None)
                else:
                    self._jobs[doc["id"]] = Job.from_dict(doc)

    def put(self, job: Job) -> None:
        self.writer.append(json.dumps(job.to_dict()).encode())
        self._jobs[job.id] = job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        if job_id not in self._jobs:
            return False
        self.writer.append(json.dumps({"id": job_id, "_deleted": True}).encode())
        del self._jobs[job_id]
        return True

    def scan(self) -> Iterator[Job]:
        return iter(list(self._jobs.values()))

    def close(self) -> None:
        self.writer.close()
