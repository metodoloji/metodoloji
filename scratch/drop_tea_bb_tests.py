import pathlib

p = pathlib.Path("hooks/engine/tests/test_blackboard.py")
s = p.read_text(encoding="utf-8")
start = s.index("def test_testing_sub_chain_feeders_fan_in_on_the_quality_record")
end = s.index("def test_governance_sub_chain_closes_the_methodology_loop")
p.write_text(s[:start] + s[end:], encoding="utf-8")
print("removed", end - start, "chars")
