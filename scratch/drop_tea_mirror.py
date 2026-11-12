"""Remove the TEA stage/feeder expectations from test_mirror.py."""
import pathlib

p = pathlib.Path("hooks/engine/tests/test_mirror.py")
s = p.read_text(encoding="utf-8")

start = s.index('    assert mir.stage_of("test.atdd.3-2-digest") == "test-atdd"')
end = s.index('    assert mir.stage_of("retro.epic-1") == "retrospective"')
s = s[:start] + s[end:]
print("stage asserts removed")

start = s.index("def test_mirror_tea_feeder_signals_the_quality_record(root):")
end = s.index("def test_mirror_alt_dev_branches_converge_on_the_review_gate(root):")
s = s[:start] + s[end:]
print("tea feeder test removed")

p.write_text(s, encoding="utf-8")
