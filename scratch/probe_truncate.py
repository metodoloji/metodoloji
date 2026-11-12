import os, pathlib, sys, tempfile
sys.path.insert(0, "hooks/engine")
os.environ["METODOLOJI_BLACKBOARD_BACKEND"] = "sqlite"
from modules import blackboard as bb

td = tempfile.mkdtemp()
paths = bb.board_paths(td)
# MAX_EVENTS
print("MAX_EVENTS", bb.MAX_EVENTS)

# create a big gap in seq via rolled-back transactions
for i in range(5):
    try:
        bb._mutate(td, lambda b: (b, {"ok": True}) if False else (_ for _ in ()).throw(RuntimeError("x")))
    except Exception:
        pass

for i in range(5):
    bb.write_key(td, f"k{i}", str(i))

conn = __import__("sqlite3").connect(paths["db"])
rows = conn.execute("SELECT seq FROM events ORDER BY seq").fetchall()
print("seqs after rollbacks:", [r[0] for r in rows])
print("count:", len(rows))

# Now simulate the truncation bound directly with a small MAX_EVENTS-like rule
seqs = [r[0] for r in rows]
mx = max(seqs)
cut = mx - 5
deleted = [s for s in seqs if s <= cut]
print("with MAX_EVENTS=5 -> cutoff", cut, "deletes", deleted, "keeps", len(seqs) - len(deleted))
conn.close()
