import pathlib

p = pathlib.Path("KILAVUZ.md")
s = p.read_text(encoding="utf-8")
s = s.replace("`--module tea` 23 satır / 818 B iken", "`--module bmm` 23 satır / 818 B iken")
s = s.replace("`--module tea` (23 satır / 818 B)", "`--module bmm` (23 satır / 818 B)")
p.write_text(s, encoding="utf-8")
print("ok")
