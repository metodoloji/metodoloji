import os, pathlib, sys, tempfile, sqlite3
sys.path.insert(0, "hooks/engine")
os.environ["METODOLOJI_BLACKBOARD_BACKEND"] = "sqlite"
from modules import blackboard as bb

# Prove the seq-arithmetic bound: sparse seqs make it drop the wrong number.
td = tempfile.mkdtemp()
paths = bb.board_paths(td)
# Simulate a board whose seq space has gaps (rollbacks consume ids).
conn = bb._sqlite_conn(paths)
conn.execute("INSERT INTO board (id, data, updated) VALUES (1, ?, 0)", ('{"version":4}',))
for i in range(20):
    conn.execute("INSERT INTO events (ts, data) VALUES (?, ?)",
                 (float(i), '{"event":"write","key":"k%d"}' % i))
conn.close()
# push seq to a sparse region: delete + reinsert is what a rollback does
conn = sqlite3.connect(paths["db"])
conn.execute("DELETE FROM events WHERE seq <= 15")
conn.execute("UPDATE sqlite_sequence SET seq = 1000000 WHERE name='events'")
conn.commit()
for i in range(5):
    conn.execute("INSERT INTO events (ts, data) VALUES (?, ?)", (0.0, '{"event":"write","key":"z%d"}' % i))
conn.commit()
rows = conn.execute("SELECT seq FROM events ORDER BY seq").fetchall()
print("rows before truncate:", [r[0] for r in rows])
conn.close()

old = bb.MAX_EVENTS
bb.MAX_EVENTS = 4
# run the truncation the mutation path uses, inside a transaction like _mutate
with bb._board_lock(paths):
    bb._truncate_events_if_needed(paths)
bb.MAX_EVENTS = old
conn = sqlite3.connect(paths["db"])
rows = conn.execute("SELECT seq FROM events ORDER BY seq").fetchall()
print("rows after truncate (want newest 4):", [r[0] for r in rows])
conn.close()
