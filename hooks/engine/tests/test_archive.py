"""Tests for hooks/engine/modules/archive.py — tar/zip target extraction + limits."""

import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

from modules.archive import (  # noqa: E402
    ArchiveLimitError,
    LimitReader,
    archive_fileobj,
    conservative_dest,
    targets_from_tar,
    targets_from_unzip,
)
from modules.config import (  # noqa: E402
    ARCHIVE_MAX_COMPRESSED,
    ARCHIVE_MAX_FILE,
    ARCHIVE_MAX_MEMBERS,
)


# --- LimitReader ------------------------------------------------------------

class _Raw(io.RawIOBase):
    """Minimal in-memory reader backing LimitReader."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        else:
            self._pos = len(self._data)
        return self._pos

    def readinto(self, b) -> int:
        chunk = self._data[self._pos:self._pos + len(b)]
        self._pos += len(chunk)
        b[:len(chunk)] = chunk
        return len(chunk)

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = len(self._data) - self._pos
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


def test_limit_reader_allows_within_limit():
    r = LimitReader(_Raw(b"hello world"), 11)
    assert r.read(11) == b"hello world"


def test_limit_reader_read_beyond_raises():
    r = LimitReader(_Raw(b"hello"), 3)
    assert r.read(3) == b"hel"
    with pytest.raises(ArchiveLimitError):
        r.read(1)


def test_limit_reader_readinto():
    r = LimitReader(_Raw(b"abcdef"), 4)
    buf = bytearray(4)
    assert r.readinto(buf) == 4
    assert bytes(buf) == b"abcd"
    with pytest.raises(ArchiveLimitError):
        r.readinto(bytearray(1))


def test_limit_reader_seek_forward_limited():
    r = LimitReader(_Raw(b"0123456789"), 5)
    with pytest.raises(ArchiveLimitError):
        r.seek(10, 0)
    # Backward seek is fine.
    r.seek(0, 0)
    assert r.read(5) == b"01234"


def test_limit_reader_close_delegates():
    raw = _Raw(b"x")
    r = LimitReader(raw, 10)
    r.close()
    assert r.closed


def test_limit_reader_readable_seekable():
    r = LimitReader(_Raw(b"x"), 10)
    assert r.readable() is True
    assert r.seekable() is True


# --- conservative_dest ------------------------------------------------------

def test_conservative_dest():
    assert conservative_dest("") == []
    assert conservative_dest("out") == ["out/"]
    assert conservative_dest("out/") == ["out/"]
    assert conservative_dest("a/b/") == ["a/b/"]


# --- archive_fileobj --------------------------------------------------------

def test_archive_fileobj_rejects_too_large(tmp_path):
    big = tmp_path / "big.tar"
    big.write_bytes(b"x" * (ARCHIVE_MAX_FILE + 1))
    with pytest.raises(ArchiveLimitError):
        archive_fileobj(str(big))


def test_archive_fileobj_ok(tmp_path):
    small = tmp_path / "small.tar"
    small.write_bytes(b"data")
    fh = archive_fileobj(str(small))
    assert fh.read(4) == b"data"
    fh.close()


# --- targets_from_tar -------------------------------------------------------

def _make_tar(tmp_path, members):
    """members: dict of name -> bytes. Returns (archive_path, extracted_names)."""
    archive = tmp_path / "a.tar"
    names = []
    with tarfile.open(archive, "w") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            names.append(name)
    return archive, names


def test_tar_simple_files(tmp_path):
    archive, names = _make_tar(tmp_path, {"src/a.py": b"x", "src/b.py": b"y"})
    res = targets_from_tar(["-xf", str(archive)])
    assert set(res) == {"src/a.py", "src/b.py"}


def test_tar_with_dest_dir(tmp_path):
    archive, _ = _make_tar(tmp_path, {"a.py": b"x"})
    res = targets_from_tar(["-xf", str(archive), "-C", "out"])
    assert res == ["out/a.py"]


def test_tar_bare_x_flag(tmp_path):
    archive, _ = _make_tar(tmp_path, {"f.py": b"x"})
    res = targets_from_tar(["x", str(archive)])
    assert res == ["f.py"]


def test_tar_missing_archive_conservative():
    res = targets_from_tar(["-xf", "/nonexistent/a.tar"])
    assert res == [] or res == ["/"]


def test_tar_member_limit(tmp_path):
    archive = tmp_path / "many.tar"
    with tarfile.open(archive, "w") as tf:
        for i in range(ARCHIVE_MAX_MEMBERS + 5):
            info = tarfile.TarInfo(f"f{i}.py")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
    res = targets_from_tar(["-xf", str(archive)])
    # Conservative fallback (limit exceeded) → dest dir, or empty.
    assert res == [] or all(x.endswith("/") for x in res)


def test_tar_no_archive_arg():
    assert targets_from_tar(["-xf"]) == []


def test_tar_traversal_members_dropped(tmp_path):
    archive, _ = _make_tar(tmp_path, {"../evil.py": b"x", "src/ok.py": b"y",
                                      "/abs.py": b"z"})
    res = targets_from_tar(["-xf", str(archive)])
    assert res == ["src/ok.py"]


def test_unzip_traversal_members_dropped(tmp_path):
    archive = _make_zip(tmp_path, {"../evil.py": b"x", "src/ok.py": b"y"})
    res = targets_from_unzip([str(archive)])
    assert res == ["src/ok.py"]


def test_member_size_limit(tmp_path, monkeypatch):
    from modules import archive as archive_mod
    monkeypatch.setattr(archive_mod, "ARCHIVE_MAX_MEMBER", 2)
    archive = _make_zip(tmp_path, {"big.py": b"xxx"})
    res = targets_from_unzip([str(archive)])
    assert res == []  # limit exceeded → conservative fallback


# --- targets_from_unzip -----------------------------------------------------

def _make_zip(tmp_path, members):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return archive


def test_unzip_simple(tmp_path):
    archive = _make_zip(tmp_path, {"src/a.py": b"x", "src/b.py": b"y"})
    res = targets_from_unzip([str(archive)])
    assert set(res) == {"src/a.py", "src/b.py"}


def test_unzip_with_dest_dir(tmp_path):
    archive = _make_zip(tmp_path, {"a.py": b"x"})
    res = targets_from_unzip(["-d", "out", str(archive)])
    assert res == ["out/a.py"]


def test_unzip_named_files(tmp_path):
    archive = _make_zip(tmp_path, {"a.py": b"x", "b.py": b"y"})
    res = targets_from_unzip([str(archive), "b.py"])
    assert res == ["b.py"]


def test_unzip_missing_conservative():
    res = targets_from_unzip(["/nonexistent/a.zip"])
    assert res == []


def test_unzip_no_args():
    assert targets_from_unzip([]) == []


def test_unzip_skips_dirs(tmp_path):
    archive = _make_zip(tmp_path, {"dir/": b"", "dir/f.py": b"x"})
    res = targets_from_unzip([str(archive)])
    assert "dir/f.py" in res
    assert all("dir/" != r for r in res)
