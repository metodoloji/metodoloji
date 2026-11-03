"""Training variables read from a ``.env`` file.

The live runner and proposer take their endpoint from the process
environment. Exporting those variables in a shell works, but it depends on how
the terminal was started and leaves the key in shell history. A ``.env`` file
keeps them in one place; it is already gitignored, so the key never lands in a
commit.

A variable that is already set wins over the file, so CI, a one-off override
or a deliberately empty value behave exactly as before. The parser is
deliberately small and stdlib-only (``KEY=VALUE``, optional ``export`` prefix,
``#`` comments, quoted values): this package has no dependencies to install.
"""
from __future__ import annotations

import os
import pathlib

def parse(text: str) -> dict[str, str]:
    """Parse ``.env`` text; a malformed line is an error, not a silent skip."""
    values: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            raise ValueError(f"line {lineno}: expected KEY=VALUE, got {raw!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"line {lineno}: empty variable name")
        if value[:1] in {"'", '"'}:
            # A quoted value owns everything up to its closing quote, so a
            # '#' inside the secret is kept rather than read as a comment.
            end = value.find(value[0], 1)
            if end == -1:
                raise ValueError(f"line {lineno}: unterminated quote")
            value = value[1:end]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def find(start: pathlib.Path) -> list[pathlib.Path]:
    """Every ``.env`` from ``start`` up to the filesystem root, nearest first."""
    found: list[pathlib.Path] = []
    for directory in (start, *start.parents):
        path = directory / ".env"
        if path.is_file():
            found.append(path)
    return found


def load_dotenv(*starts: pathlib.Path) -> list[pathlib.Path]:
    """Load ``.env`` files; the nearest file wins and real env is never replaced."""
    loaded: list[pathlib.Path] = []
    for start in starts:
        for path in find(pathlib.Path(start).resolve()):
            if path in loaded:
                continue
            for key, value in parse(path.read_text(encoding="utf-8")).items():
                os.environ.setdefault(key, value)
            loaded.append(path)
    return loaded
