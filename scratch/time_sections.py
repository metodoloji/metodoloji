"""Time each check-plugin.sh section by timestamping its output lines."""
import subprocess
import sys
import time

start = time.time()
prev = start
prev_label = "(start)"
proc = subprocess.Popen(["sh", "scripts/check-plugin.sh"], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, bufsize=1)
for line in proc.stdout:
    if line.startswith("== ") or line.startswith("STATUS"):
        now = time.time()
        print(f"{now - prev:7.2f}s  {prev_label[:80]}")
        prev = now
        prev_label = line.rstrip()
proc.wait()
print(f"{time.time() - prev:7.2f}s  {prev_label[:80]}")
print(f"TOTAL {time.time() - start:.2f}s  exit={proc.returncode}")
