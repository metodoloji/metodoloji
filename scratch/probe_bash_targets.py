import sys
sys.path.insert(0, "hooks/engine")
from modules import bash_targets as bt

cases = [
    "python -c \"p='lib/x'; open(p+'.py','w')\"",
    "python -c \"open('out.txt','w').write('x')\"",
    "python -c \"import json; json.dump({'a':1}, open('data.json','w'))\"",
    "python -c \"print(1)\"",
    "python -c \"open('lib/x.py','w').write('y')\"",
    "python3 -c \"from pathlib import Path; Path('docs/x.md').write_text('a')\"",
    "python -c \"open('a.txt','w') if 0 else print('x')\"",
    "python -c \"import shutil; shutil.copy('a','b')\"",
    "python -c \"open('a.txt')\"",
]
for c in cases:
    print(repr(c), "->", bt.extract_bash_targets_ex(c))
