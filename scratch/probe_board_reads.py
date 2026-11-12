import os, pathlib, sys, tempfile
sys.path.insert(0, "hooks/engine")

def fresh(env=None):
    td = tempfile.mkdtemp()
    for k in ("METODOLOJI_BLACKBOARD_BACKEND",):
        os.environ.pop(k, None)
    if env:
        os.environ[k] = env
    return td

from modules import blackboard as bb
from modules import blackboard_diagnostics as diag

# 1) read_board on a fresh project: does it create state?
for backend in ("json", "sqlite"):
    td = fresh(backend)
    os.environ["METODOLOJI_BLACKBOARD_BACKEND"] = backend
    before = sorted(p.name for p in pathlib.Path(td).rglob("*"))
    bb.read_board(td)
    after = sorted(str(p.relative_to(td)) for p in pathlib.Path(td).rglob("*"))
    print(f"read_board[{backend}] before={before} after={after}")

# 2) doctor on a fresh project
for backend in ("json", "sqlite"):
    td = fresh(backend)
    os.environ["METODOLOJI_BLACKBOARD_BACKEND"] = backend
    d = diag.doctor(td)
    files = sorted(str(p.relative_to(td)) for p in pathlib.Path(td).rglob("*"))
    print(f"doctor[{backend}] verdict={d['verdict']} files={files}")
    print("   snapshot check:", d["checks"].get("snapshot"))

# 3) event_stats on a fresh project (no db)
for backend in ("json", "sqlite"):
    td = fresh(backend)
    os.environ["METODOLOJI_BLACKBOARD_BACKEND"] = backend
    paths = bb.board_paths(td)
    out = bb.event_stats(paths)
    files = sorted(str(p.relative_to(td)) for p in pathlib.Path(td).rglob("*"))
    print(f"event_stats[{backend}] -> {out} files={files}")
