"""Repository-wide test defaults.

The shipped blackboard backend is SQLite (one WAL database). Most legacy tests
pin the JSON file layout explicitly (event-log lines, snapshot replaces), so the
suite keeps those tests on the JSON backend and lets the SQLite tests opt in via
monkeypatch. This is a TEST default, not a production default: the shipped
`config.toml` selects SQLite.
"""
import os

os.environ.setdefault("METODOLOJI_BLACKBOARD_BACKEND", "json")
