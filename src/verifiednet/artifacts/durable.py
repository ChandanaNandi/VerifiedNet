"""Shared durable-write primitives for the artifacts package.

Atomic canonical-file installation: bytes → temp sibling → flush → ``os.fsync``
→ ``os.replace`` → parent-dir fsync. Used by both the run-directory writer and
the run-index writer.
"""

from __future__ import annotations

import os
from pathlib import Path


def fsync_dir(path: Path) -> None:
    """Best-effort directory fsync (no-op on platforms without directory fds)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - platforms without dir fds
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically install *data* at *path* (temp → fsync → replace → dir fsync)."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)
