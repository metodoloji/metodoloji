import json, os, pathlib, sys, tempfile
sys.path.insert(0, "hooks/engine")
os.environ["METODOLOJI_BLACKBOARD_BACKEND"] = "sqlite"
from modules import blackboard as bb
from modules import blackboard_diagnostics as diag

td = tempfile.mkdtemp()
bb.write_key(td, "k1", "v1")
bb.post_alert(td, "session", "info", "hello")
d = diag.doctor(td)
print("verdict:", d["verdict"])
print("drift:", d["checks"]["drift"])
print("warnings:", d["warnings"])
print("snapshot:", d["checks"]["snapshot"])
print("events:", d["checks"]["events"])
