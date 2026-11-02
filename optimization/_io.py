"""Small IO primitives the optimizer depends on.

Every durable write in this package goes through :func:`atomic_write_text`.
A candidate that is half-written is worse than a candidate that was never
written: the loop resumes from state that points at a version file, and a
truncated version file would make the state lie. The pattern is the one the
registry code uses (E-089/E-096): same-directory temporary file, fsync, then
``os.replace`` — atomic on POSIX and on NTFS.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
from typing import Any


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Write ``text`` to ``path`` so a reader sees the old or the new file."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: pathlib.Path) -> Any:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_jsonl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_text(
        path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    path = pathlib.Path(path)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: not JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{lineno}: row must be a JSON object")
        rows.append(row)
    return rows


def rel(root: pathlib.Path, path: pathlib.Path) -> str:
    """Project-relative POSIX path, for records and reports."""
    try:
        return pathlib.Path(os.path.relpath(path, root)).as_posix()
    except ValueError:  # different drives on Windows
        return pathlib.Path(path).as_posix()
