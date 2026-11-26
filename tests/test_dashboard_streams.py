#!/usr/bin/env python3
"""Dashboard: single-stream vs multi-stream throughput for every engine
(per-stream decode tok/s, aggregate tok/s, streams active), and the alerts
the 2026-11-25 spark-1 slowdown should have raised: D-state processes, a
load anomaly, a kernel oops (taint bit D), a falling MemAvailable trend,
and per-stream speed far below the model's baseline
(docs/reports/SPARK1_SLOWDOWN-2026-11-25.md).
"""
from __future__ import annotations

import json

from test_dashboard_v2 import GB10, load
from test_dashboard_v3 import T0, _node_snap


# ───────────────────────── per-stream vs aggregate ─────────────────────────

def _slots(*decoded, task0=100):
    return json.dumps([{"id": i, "id_task": task0 + i, "is_processing": n is not None,
                        "next_token": [{"n_decoded": n or 0}]} for i, n in enumerate(decoded)])


def test_llama_slot_rates_are_per_slot_and_median(monkeypatch):
    d = load(monkeypatch)
    a = d.llama_slot_decoded(200, _slots(100, 50, None, 0))
    assert a == {(0, 100): 100, (1, 101): 50}           # idle and still-prefilling slots are not streams
    b = d.llama_slot_decoded(200, _slots(160, 70, None, 30))
    rates = sorted(d.slot_rates(a, b, 2.0))
    assert rates == [10.0, 30.0]                          # slot 3 is new: no rate yet
    # A new request on the same slot (new task id) is a new stream, not a negative rate.
    c = d.llama_slot_decoded(200, _slots(5, task0=200))
    assert d.slot_rates(b, c, 2.0) == []
    assert d.llama_slot_decoded(404, "") is None and d.llama_slot_decoded(200, "nope") is None
    v = d.streams_view("llama", 40.0, 3, decoding=3, prefilling=0, slot_tok_s=[10.0, 30.0, 20.0])
    assert v["per_stream_tok_s"] == 20.0 and v["per_stream_source"] == "slots"
    assert v["aggregate_tok_s"] == 40.0 and v["active"] == 3 and v["per_stream_min_tok_s"] == 10.0


def test_tensorfold_per_stream_is_the_batch_rate(monkeypatch):
    d = load(monkeypatch)
    h = d._tf_health(json.dumps({"requests_running": 4, "streams": {"decoding": 3, "prefilling": 1, "max": 8}}))
    assert h["busy"] == 4 and h["decoding"] == 3 and h["prefilling"] == 1 and h["streams"] == 8
    v = d.streams_view("tensorfold", 60.0, 4, decoding=3, prefilling=1, agg_60s=55.0)
    assert v["per_stream_tok_s"] == 20.0 and v["per_stream_source"] == "batch"
    assert v["aggregate_60s_tok_s"] == 55.0 and v["decoding"] == 3 and v["prefilling"] == 1
    # Only prefilling: no decode rate to divide; the finished requests' rate stands in.
    v = d.streams_view("tensorfold", 0.0, 1, decoding=0, prefilling=1, finished_decode_tok_s=31.0)
    assert v["per_stream_tok_s"] == 31.0 and v["per_stream_source"] == "finished"


def test_vllm_per_stream_is_an_estimate(monkeypatch):
    d = load(monkeypatch)
    v = d.streams_view("vllm", 80.0, 4)
    assert v["per_stream_tok_s"] == 20.0 and v["per_stream_source"] == "estimate"
    idle = d.streams_view("vllm", 0.0, 0)
    assert idle["per_stream_tok_s"] is None and idle["active"] == 0


def test_status_and_serving_doc_carry_streams(monkeypatch):
    d = load(monkeypatch)
    assert "serve:per_stream_tok_s" in d.SERVE_SERIES and "serve:streams" in d.SERVE_SERIES
    src = open(d.__file__).read()
    assert '"streams": live.get("streams")' in src and '"healthy", "streams")' in src


def test_page_shows_both_rates(monkeypatch):
    d = load(monkeypatch)
    import os
    static = os.path.join(os.path.dirname(d.__file__), "static")
    html = open(os.path.join(static, "index.html")).read()
    js = open(os.path.join(static, "app.js")).read()
    for i in ("sig-toks-sub", "sv-per-stream", "sv-streams"):
        assert f'id="{i}"' in html and f"'{i}'" in js
    assert "tok/s · all streams" in html and "generation · per stream" in js


# ───────────────────────── baselines and the slow-stream alert ─────────────────────────

def test_stream_baseline_curve(monkeypatch):
    d = load(monkeypatch)
    m = "DeepSeek-V4.1-Flash-1017"
    assert d.stream_baseline(m, 1) == 67.9 and d.stream_baseline(m, 4) == 30.9
    assert abs(d.stream_baseline(m, 5) - 27.5) < 0.01          # between 4 and 6
    assert abs(d.stream_baseline(m, 16) - 9.95) < 0.01         # saturated past 8
    assert d.stream_baseline("GLM-5.3-Flash-EXL3", 4) is None
    assert d.parse_stream_baseline("1:68, 4:31,bad,8:20") == ((1, 68.0), (4, 31.0), (8, 20.0))
    assert d.parse_stream_baseline("") is None
    assert d.stream_baseline("anything", 4, ((1, 10.0), (4, 5.0))) == 5.0
    th = d.alert_thresholds(conf={"DASHBOARD_STREAM_BASELINE": "1:50,2:40"})
    assert th["STREAM_BASELINE"] == ((1, 50.0), (2, 40.0))


def test_slow_stream_alert(monkeypatch):
    d = load(monkeypatch)
    th = d.alert_thresholds(conf={})
    serve = {"healthy": True, "model_id": "DeepSeek-V4.1-Flash-1017", "gate": {"enabled": False},
             "streams": {"per_stream_tok_s": 1.8, "per_stream_source": "batch", "decoding": 4,
                         "active": 4, "aggregate_tok_s": 7.4, "prefilling": 0}}
    c = d.alert_conditions(th, [], serve, {}, T0)
    assert c["serve:slow"][0] == "slow" and "baseline 30.9" in c["serve:slow"][3]
    ok = dict(serve, streams=dict(serve["streams"], per_stream_tok_s=25.0))
    assert "serve:slow" not in d.alert_conditions(th, [], ok, {}, T0)
    # A finished-request fallback is history, not now: no alert from it.
    fin = dict(serve, streams=dict(serve["streams"], per_stream_source="finished"))
    assert "serve:slow" not in d.alert_conditions(th, [], fin, {}, T0)
    # No baseline for the model: no alert.
    assert "serve:slow" not in d.alert_conditions(th, [], dict(serve, model_id="GLM-5.3"), {}, T0)


# ───────────────────────── node: D state, load, kernel oops, memory trend ─────────────────────────

def test_node_script_and_parse_dstate_and_taint(monkeypatch):
    d = load(monkeypatch)
    sc = d.node_script("~")
    assert "/proc/sys/kernel/tainted" in sc and "dstate|" in sc
    raw = d.parse_node(GB10 + "\ntaint|4224\ndstate|1640|1640|mstflint\n")
    v = d.node_view(raw)
    assert v["procs"] == {"d_state": 1640, "d_top": "mstflint", "d_top_n": 1640}
    assert v["kernel"] == {"tainted": 4224, "oops": True, "warn": False}       # 4096 (O) + 128 (D)
    clean = d.node_view(d.parse_node(GB10 + "\ntaint|4096\ndstate|0|0|\n"))
    assert clean["kernel"]["oops"] is False and clean["procs"]["d_state"] == 0 and clean["procs"]["d_top"] is None
    s = d.HistoryStore()
    d.record_node(s, "spark-1", v, T0)
    names = s.names()
    assert "node:spark-1:dstate" in names and "node:spark-1:mem_avail" in names


def test_dstate_load_and_oops_alerts(monkeypatch):
    d = load(monkeypatch)
    th = d.alert_thresholds(conf={})
    n = _node_snap("spark-1", "head", procs={"d_state": 1640, "d_top": "mstflint", "d_top_n": 1640},
                   cpu={"load": [1642.7, 1642.6, 1639.1], "ncpu": 20},
                   kernel={"tainted": 4224, "oops": True, "warn": False})
    c = d.alert_conditions(th, [n], {}, {}, T0)
    assert c["dstate:spark-1"][1] == "crit" and "1640 are mstflint" in c["dstate:spark-1"][3]
    assert c["load:spark-1"][0] == "load" and c["kernel:spark-1"][1] == "crit"
    calm = _node_snap("spark-2", procs={"d_state": 1, "d_top": "jbd2", "d_top_n": 1},
                      cpu={"load": [2.9, 2.8, 2.8], "ncpu": 20}, kernel={"tainted": 4096, "oops": False})
    assert d.alert_conditions(th, [calm], {}, {}, T0) == {}
    off = d.alert_thresholds(conf={"DASHBOARD_ALERTS_OFF": "dstate,load,kernel"})
    assert d.alert_conditions(off, [n], {}, {}, T0) == {}


def test_mem_trend_and_alert(monkeypatch):
    d = load(monkeypatch)
    # spark-1, 2026-11-24/25: ~5.0 GiB falling ~70 MiB/h (sar -r).
    vals = [5120 - 70 * (i / 60) for i in range(360)]
    tr = d.mem_trend({"t0": T0, "step": 60, "values": vals}, floor_gib=2.5)
    assert abs(tr["mib_per_h"] + 70) < 0.5 and tr["span_h"] == 6.0 and 30 < tr["hours_to_floor"] < 32
    assert d.mem_trend({"t0": T0, "step": 60, "values": vals[:120]}) is None       # under 3 h
    assert d.mem_trend(None) is None
    flat = d.mem_trend({"t0": T0, "step": 60, "values": [5120.0] * 360}, floor_gib=2.5)
    assert flat["mib_per_h"] == 0 and flat["hours_to_floor"] is None
    th = d.alert_thresholds(conf={})
    c = d.alert_conditions(th, [_node_snap("spark-1", "head", mem_trend=tr)], {}, {}, T0)
    assert c["memtrend:spark-1"][0] == "memtrend"
    assert d.alert_conditions(th, [_node_snap("spark-1", "head", mem_trend=flat)], {}, {}, T0) == {}


# ───────────────────────── the sampler end to end ─────────────────────────

def _sampler(d, answers):
    from test_dashboard_v2 import _canned
    d._get = _canned(answers)
    d.inference_unit = lambda units=None: {"name": "u", "active": True, "uptime_s": 60}
    return d.ServeSampler()


def test_sampler_llama_measures_each_slot(monkeypatch):
    d = load(monkeypatch)
    met = "llamacpp:tokens_predicted_total {}\nllamacpp:prompt_tokens_total 10\nllamacpp:requests_processing 2\n"
    s = _sampler(d, {"/health": (200, '{"status":"ok"}'), "/slots": (200, _slots(100, 40)), "/metrics": (200, met.format(0))})
    s.sample(now=1000.0)
    d._get = __import__("test_dashboard_v2")._canned(
        {"/health": (200, '{"status":"ok"}'), "/slots": (200, _slots(200, 60)), "/metrics": (200, met.format(0))})
    s.sample(now=1010.0)
    st = s.snapshot()["streams"]
    assert st["per_stream_source"] == "slots" and st["per_stream_tok_s"] == 6.0       # median of 10 and 2
    assert st["aggregate_tok_s"] == 12.0 and st["decoding"] == 2 and st["per_stream_min_tok_s"] == 2.0


def test_sampler_tensorfold_reports_batch_rate(monkeypatch):
    d = load(monkeypatch, OPENBEAST_INFERENCE_BACKEND="tensorfold", OPENBEAST_INFERENCE_URL="http://127.0.0.1:8000")
    h = json.dumps({"ok": True, "requests_running": 4, "streams": {"max": 8, "decoding": 3, "prefilling": 1},
                    "pool_tokens": 1000, "pool_free_tokens": 500, "iteration_s": 0.4})
    met = "tensorfold:generation_tokens_total {}\ntensorfold:generation_tokens_running {}\n"
    s = _sampler(d, {"/health": (200, h), "/metrics": (200, met.format(1000, 0))})
    s.sample(now=1000.0)
    d._get = __import__("test_dashboard_v2")._canned({"/health": (200, h), "/metrics": (200, met.format(1000, 90))})
    s.sample(now=1010.0)
    st = s.snapshot()["streams"]
    assert st["aggregate_tok_s"] == 9.0 and st["per_stream_tok_s"] == 3.0 and st["per_stream_source"] == "batch"
    assert st["active"] == 4 and st["decoding"] == 3 and st["prefilling"] == 1
