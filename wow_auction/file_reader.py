"""Read-only stable snapshots; no automatic WoW reload or file writes.

Two complete reads separated by a bounded settling interval must agree in bytes
and metadata. This detects observed concurrent writes; it cannot prove that a
writer will not begin a new save after the function returns. The caller still
must parse/validate before its own atomic database commit.
"""

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import stat
import time


class StableReadError(OSError):
    """Safe diagnostic code without file paths or data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StableFile:
    data: bytes
    sha256: str
    file_written_at_ns: int
    size_bytes: int


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    # Windows Python can report different ctime semantics through path stat and
    # handle fstat. Identity, length, mtime and the two complete byte reads are
    # compared; ctime is deliberately not used as a portable modification clock.
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns)


def _regular(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISREG(metadata.st_mode) and not attributes & reparse


def _read_once(path: Path, max_bytes: int) -> tuple[bytes, os.stat_result]:
    before = path.lstat()
    if not _regular(before):
        raise StableReadError("not_regular_file")
    if before.st_size > max_bytes:
        raise StableReadError("size_limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not _regular(opened) or _identity(opened) != _identity(before):
            raise StableReadError("file_changed")
        data = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    if len(data) > max_bytes:
        raise StableReadError("size_limit")
    if len(data) != before.st_size or _identity(before) != _identity(after) or _identity(after) != _identity(current):
        raise StableReadError("file_changed")
    return data, after


def read_stable(path: str | Path, *, settle_seconds: float = 0.1,
                max_bytes: int = 32 * 1024 * 1024) -> StableFile:
    """Read a main SavedVariables file twice; `.bak` is never a new source.

    Recovery from a backup requires a separate explicit recovery workflow. Files
    are opened only with read permissions. No retries hide a continuing writer;
    the importer can record the failure and retry on a later poll.
    """
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("reader_size_limit_must_be_positive")
    if not math.isfinite(settle_seconds) or not 0 <= settle_seconds <= 10:
        raise ValueError("reader_settle_interval_out_of_range")
    path = Path(path)
    if path.suffix.lower() == ".bak":
        raise StableReadError("backup_requires_explicit_recovery")
    try:
        first, first_meta = _read_once(path, max_bytes)
        time.sleep(settle_seconds)
        second, second_meta = _read_once(path, max_bytes)
    except StableReadError:
        raise
    except OSError:
        raise StableReadError("read_failed") from None
    if _identity(first_meta) != _identity(second_meta) or first != second:
        raise StableReadError("file_changed")
    return StableFile(second, hashlib.sha256(second).hexdigest(),
                      second_meta.st_mtime_ns, len(second))
