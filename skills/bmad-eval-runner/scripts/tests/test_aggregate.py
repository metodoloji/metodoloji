#!/usr/bin/env python3
"""Tests for aggregate_benchmark.py statistics + loading."""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import aggregate_benchmark as agg  # noqa: E402


# --- sample_stddev / summarize_metric ---------------------------------------

def test_sample_stddev_known():
    assert abs(agg.sample_stddev([10.0, 12.0, 14.0]) - 2.0) < 1e-9


def test_sample_stddev_single_is_zero():
    assert agg.sample_stddev([5.0]) == 0.0
    assert agg.sample_stddev([]) == 0.0


def test_summarize_metric_empty():
    s = agg.summarize_metric([])
    assert s["n"] == 0 and s["mean"] == 0.0 and s["stddev"] == 0.0


# --- collect_numeric_metrics -------------------------------------------------

def test_collect_numeric_excludes_bool():
    recs = [{"ok": True, "x": 1}, {"ok": False, "x": 3}]
    by = agg.collect_numeric_metrics(recs)
    assert set(by) == {"x"}
    assert by["x"] == [1.0, 3.0]


def test_collect_numeric_skips_non_dict():
    assert agg.collect_numeric_metrics([1, "x", None]) == {}


# --- summarize_config / delta_configs ---------------------------------------

def test_summarize_config_groups_metrics():
    recs = [{"elapsed_s": 1.0}, {"elapsed_s": 3.0}]
    s = agg.summarize_config(recs)
    assert s["runs"] == 2
    assert abs(s["metrics"]["elapsed_s"]["mean"] - 2.0) < 1e-9


def test_delta_configs_shared_metrics_only():
    base = {"metrics": {"a": {"mean": 10.0, "stddev": 1.0},
                        "only_base": {"mean": 5.0, "stddev": 0.0}}}
    var = {"metrics": {"a": {"mean": 12.0, "stddev": 2.0},
                       "only_var": {"mean": 7.0, "stddev": 0.0}}}
    d = agg.delta_configs(base, var)
    assert "a" in d
    assert abs(d["a"]["delta"] - 2.0) < 1e-9
    assert abs(d["a"]["delta_pct"] - 20.0) < 1e-9
    assert "only_base" not in d and "only_var" not in d


def test_delta_configs_zero_mean_pct_none():
    base = {"metrics": {"x": {"mean": 0.0, "stddev": 0.0}}}
    var = {"metrics": {"x": {"mean": 5.0, "stddev": 0.0}}}
    d = agg.delta_configs(base, var)
    assert d["x"]["delta_pct"] is None


# --- load_records -----------------------------------------------------------

def test_load_records_json_list(tmp_path):
    f = tmp_path / "r.json"
    f.write_text('[{"a": 1}, {"a": 2}]', encoding="utf-8")
    assert len(agg.load_records(f)) == 2


def test_load_records_wrapped(tmp_path):
    f = tmp_path / "r.json"
    f.write_text('{"runs": [{"a": 1}]}', encoding="utf-8")
    assert len(agg.load_records(f)) == 1


def test_load_records_dir_timing(tmp_path):
    d = tmp_path / "runs"
    (d / "run1").mkdir(parents=True)
    (d / "run2").mkdir(parents=True)
    (d / "run1/timing.json").write_text('{"elapsed_s": 1.0}', encoding="utf-8")
    (d / "run2/timing.json").write_text('{"elapsed_s": 2.0}', encoding="utf-8")
    (d / "run2/junk.json").write_text('{"not": "timing"}', encoding="utf-8")
    recs = agg.load_records(d)
    assert len(recs) == 2
    assert {r["elapsed_s"] for r in recs} == {1.0, 2.0}


def test_load_records_dir_skips_bad_json(tmp_path):
    d = tmp_path / "runs"
    (d / "run1").mkdir(parents=True)
    (d / "run1/timing.json").write_text("{bad json", encoding="utf-8")
    assert agg.load_records(d) == []


def test_load_records_not_list_raises(tmp_path):
    f = tmp_path / "r.json"
    f.write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        agg.load_records(f)


# --- self-test wiring -------------------------------------------------------

def test_self_test_passes():
    assert agg.run_self_test() == 0
