"""Archive handling for BMAD hooks engine (tar, zip)."""

import io
import os
import re
import tarfile
import zipfile

from .config import (
    ARCHIVE_MAX_COMPRESSED,
    ARCHIVE_MAX_FILE,
    ARCHIVE_MAX_MEMBER,
    ARCHIVE_MAX_MEMBERS,
    ARCHIVE_MAX_UNCOMPRESSED,
    TAR_ARG_OPTS,
)


class ArchiveLimitError(OSError):
    """Archive exceeded a limit — fall back to conservative behavior (fail-closed)."""


class LimitReader(io.RawIOBase):
    """Reader that rejects reads beyond a byte limit."""

    def __init__(self, raw: io.RawIOBase, limit: int):
        super().__init__()
        self._raw = raw
        self._left = limit

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._raw.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        cur = self._raw.tell()
        if whence == 0:
            target = offset
        elif whence == 1:
            target = cur + offset
        else:
            target = self._raw.seek(0, 2)
            self._raw.seek(cur, 0)
            target += offset
        if target > cur:
            if target - cur > self._left:
                raise ArchiveLimitError("archive forward-seek limit exceeded")
            self._left -= target - cur
        self._raw.seek(offset, whence)
        return self._raw.tell()

    def readinto(self, b) -> int:
        if self._left <= 0:
            raise ArchiveLimitError("archive read limit exceeded")
        view = memoryview(b)
        chunk = view[: self._left]
        m = self._raw.readinto(chunk)
        self._left -= m
        return m

    def read(self, n: int = -1) -> bytes:
        if self._left <= 0:
            raise ArchiveLimitError("archive read limit exceeded")
        if n < 0 or n > self._left:
            n = self._left
        data = self._raw.read(n)
        self._left -= len(data)
        return data

    def close(self) -> None:
        if not self.closed:
            try:
                self._raw.close()
            finally:
                super().close()

    def __del__(self) -> None:
        # Safety net: if caller forgets to close, ensure the underlying file is released
        if not self.closed:
            try:
                self._raw.close()
            except Exception:
                pass


def conservative_dest(dest: str) -> list[str]:
    """Conservative target for unreadable/limit-exceeded archive — DIRECTORY with trailing /."""
    if dest:
        return [dest.rstrip("/\\") + "/"]
    return []


def archive_fileobj(path: str) -> io.RawIOBase:
    """Open archive from disk; reject if too large, limit reads."""
    if os.path.getsize(path) > ARCHIVE_MAX_FILE:
        raise ArchiveLimitError("archive file too large")
    fh = open(path, "rb")
    return LimitReader(fh, ARCHIVE_MAX_COMPRESSED)  # type: ignore[arg-type]


def _safe_member(dest: str, name: str) -> str | None:
    """Join dest + member name; None when the member escapes (../, absolute)."""
    if not name or name.startswith("/") or name.startswith("\\"):
        return None
    parts = [seg for seg in name.replace("\\", "/").split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        return None
    clean = "/".join(parts)
    return dest.rstrip("/\\") + "/" + clean if dest else clean


def targets_from_tar(args: list[str]) -> list[str]:
    """tar -x[f...] <archive> [-C <dir>] — extract write targets using stdlib."""
    archive = None
    dest = ""
    i = 0
    seen_bare = False
    while i < len(args):
        t = args[i]
        if t.startswith("-"):
            if t == "-C" and i + 1 < len(args):
                dest = args[i + 1]
                i += 2
                continue
            if t.startswith("-C"):
                dest = t[2:]
                i += 1
                continue
            if t == "--directory" and i + 1 < len(args):
                dest = args[i + 1]
                i += 2
                continue
            if t.startswith("--directory="):
                dest = t[len("--directory="):]
                i += 1
                continue
            if t in TAR_ARG_OPTS and t not in ("-f", "--file") and i + 1 < len(args):
                i += 2
                continue
            i += 1
            continue
        if not seen_bare and re.match(r"^x[a-z]*$", t):
            i += 1
            continue
        seen_bare = True
        if archive is None:
            archive = t
        i += 1
    if not archive:
        return []
    out: list[str] = []
    total = 0
    try:
        with tarfile.open(fileobj=archive_fileobj(archive), mode="r:*") as tf:
            for i, member in enumerate(tf):
                if i >= ARCHIVE_MAX_MEMBERS:
                    raise ArchiveLimitError("member limit exceeded")
                if member.isfile():
                    if member.size > ARCHIVE_MAX_MEMBER:
                        raise ArchiveLimitError("member size limit exceeded")
                    total += member.size
                    if total > ARCHIVE_MAX_UNCOMPRESSED:
                        raise ArchiveLimitError("total size limit exceeded")
                    safe = _safe_member(dest, member.name)
                    if safe is not None:
                        out.append(safe)
    except (OSError, tarfile.TarError, ValueError):
        return conservative_dest(dest)
    return out


def targets_from_unzip(args: list[str]) -> list[str]:
    """unzip <archive> [targets...] | unzip -d <dir> <archive> — write targets."""
    dest = ""
    rest: list[str] = []
    i = 0
    while i < len(args):
        t = args[i]
        if t == "-d" and i + 1 < len(args):
            dest = args[i + 1]
            i += 2
            continue
        if t.startswith("-d"):
            dest = t[2:]
            i += 1
            continue
        rest.append(t)
        i += 1
    nonflag = [t for t in rest if not t.startswith("-")]
    if not nonflag:
        return []
    archive, files = nonflag[0], nonflag[1:]
    if files:
        return [s for s in (_safe_member(dest, f) for f in files) if s is not None]
    out: list[str] = []
    try:
        if os.path.getsize(archive) > ARCHIVE_MAX_FILE:
            raise ArchiveLimitError("archive file too large")
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > ARCHIVE_MAX_MEMBERS:
                raise ArchiveLimitError("member limit exceeded")
            total = 0
            for info in infos:
                if info.is_dir():
                    continue
                if info.file_size > ARCHIVE_MAX_MEMBER:
                    raise ArchiveLimitError("member size limit exceeded")
                total += info.file_size
                if total > ARCHIVE_MAX_UNCOMPRESSED:
                    raise ArchiveLimitError("total size limit exceeded")
                safe = _safe_member(dest, info.filename)
                if safe is not None:
                    out.append(safe)
    except (OSError, zipfile.BadZipFile, NotImplementedError):
        return conservative_dest(dest)
    return out
