#!/usr/bin/env python3
"""Dashboard v3 (extensions/dashboard): the history store (tiers, persistence,
size cap, corrupt-file recovery), the engine detail (Prometheus histograms
of vLLM, llama.cpp's counters, speculative decoding), alerts and their
thresholds, daily rollups, the model-swap log, the event log, the new
read-only routes (validation, Host pin, GET only), and the page's router
and renderers. /api/slot stays byte-identical (tests/test_dashboard_v2.py
holds the golden; one case here re-checks it with the v3 state populated).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
import urllib.parse

import pytest

from test_dashboard_v2 import (DASH, GB10, Live, _SLOT_ANS, _SLOT_ENV, _SLOT_GOLDEN, _canned,
                               _no_upstreams, load)

T0 = 1_800_000_000 - (1_800_000_000 % 900)       # a slot boundary of every tier

# ───────────────────────── real /metrics samples (read 2026-11-22, read-only) ─────────────────────────

VLLM = '''vllm:num_requests_running{engine="0",model_name="Hy4-flash-NVFP4"} 4.0
vllm:num_requests_waiting{engine="0",model_name="Hy4-flash-NVFP4"} 0.0
vllm:kv_cache_usage_perc{engine="0",model_name="Hy4-flash-NVFP4"} 0.142018779342723
vllm:prefix_cache_queries_total{engine="0",model_name="Hy4-flash-NVFP4"} 7.9886619e+07
vllm:prefix_cache_hits_total{engine="0",model_name="Hy4-flash-NVFP4"} 7.6793344e+07
vllm:num_preemptions_total{engine="0",model_name="Hy4-flash-NVFP4"} 0.0
vllm:prompt_tokens_total{engine="0",model_name="Hy4-flash-NVFP4"} 7.984854e+07
vllm:generation_tokens_total{engine="0",model_name="Hy4-flash-NVFP4"} 3.267023e+06
vllm:request_success_total{engine="0",finished_reason="stop",model_name="Hy4-flash-NVFP4"} 3594.0
vllm:request_success_total{engine="0",finished_reason="length",model_name="Hy4-flash-NVFP4"} 79.0
vllm:request_success_total{engine="0",finished_reason="abort",model_name="Hy4-flash-NVFP4"} 0.0
vllm:spec_decode_num_drafts_total{engine="0",model_name="Hy4-flash-NVFP4"} 1.00313e+06
vllm:spec_decode_num_draft_tokens_total{engine="0",model_name="Hy4-flash-NVFP4"} 5.01565e+06
vllm:spec_decode_num_accepted_tokens_total{engine="0",model_name="Hy4-flash-NVFP4"} 2.265597e+06
vllm:spec_decode_num_accepted_tokens_per_pos_total{engine="0",model_name="Hy4-flash-NVFP4",position="0"} 746156.0
vllm:spec_decode_num_accepted_tokens_per_pos_total{engine="0",model_name="Hy4-flash-NVFP4",position="1"} 547909.0
vllm:spec_decode_num_accepted_tokens_per_pos_created{engine="0",model_name="Hy4-flash-NVFP4",position="0"} 1.795e+09
'''


def _hist(name, buckets, labels='engine="0",model_name="m"'):
    out = []
    for le, n in buckets:
        out.append(f'{name}_bucket{{{labels},le="{le}"}} {n}')
    out.append(f'{name}_count{{{labels}}} {buckets[-1][1]}')
    out.append(f'{name}_sum{{{labels}}} 1.0')
    return "\n".join(out) + "\n"


TTFT = [("0.1", 0), ("0.25", 10), ("0.5", 50), ("1.0", 90), ("2.5", 100), ("+Inf", 100)]
VLLM_FULL = VLLM + _hist("vllm:time_to_first_token_seconds", TTFT) + \
    _hist("vllm:e2e_request_latency_seconds", [("1.0", 0), ("10.0", 40), ("60.0", 100), ("+Inf", 100)])

LLAMA = '''# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 2.25108e+06
llamacpp:prompt_tokens_cached_total 3.00751e+07
llamacpp:prompt_seconds_total 954.017
llamacpp:tokens_predicted_total 6.37432e+06
llamacpp:tokens_predicted_seconds_total 44382.7
llamacpp:spec_decode_num_draft_tokens_total 7.35817e+06
llamacpp:spec_decode_num_accepted_tokens_total 4.53293e+06
llamacpp:spec_decode_num_drafts_total 1.83963e+06
llamacpp:requests_processing 1
llamacpp:requests_deferred 0
llamacpp:spec_decode_num_accepted_tokens_per_pos_total{position="0"} 1516842
llamacpp:spec_decode_num_accepted_tokens_per_pos_total{position="1"} 1222047
'''


# ═════════════════════════ the history store ═════════════════════════

def test_store_tiers_gauge_means_and_gaps(monkeypatch):
    d = load(monkeypatch)
    s = d.HistoryStore()
    for k in range(60):                                  # 5 min at 5 s, then a gap, then 2 readings
        s.add("m", 10.0 + (k % 2) * 10, T0 + k * 5)
    s.add("m", 100.0, T0 + 600)
    s.add("m", 200.0, T0 + 602)
    fine = s.series("m", 900, now=T0 + 604)
    assert fine["step"] == 5 and len(fine["values"]) == 180 and fine["t0"] == T0 + 600 - 179 * 5
    vals = fine["values"]
    assert vals[-1] == 150.0                              # two readings in one 5 s slot: the mean
    assert vals[-2] is None and vals[-60] is None         # the gap stays a gap, never 0
    coarse = s.series("m", 6 * 3600, now=T0 + 604)
    assert coarse["step"] == 60 and coarse["values"][-11] == 15.0      # a minute of 10/20 alternating
    week = s.series("m", 7 * 86400, now=T0 + 604)
    assert week["step"] == 900 and len(week["values"]) == 672
    st = s.stats("m", 3600, now=T0 + 604)
    assert st["min"] == 10.0 and st["max"] == 200.0 and abs(st["avg"] - (900 + 300) / 62) < 1e-3


def test_store_sum_kind_totals_and_buckets(monkeypatch):
    d = load(monkeypatch)
    s = d.HistoryStore()
    for k in range(3600 // 5 * 3):                       # 3 h of 50 tokens per 5 s
        s.add("tok", 50, T0 + k * 5, kind="sum")
    assert s.total("tok", 3600, now=T0 + 3 * 3600 - 1) == 36000
    b = s.buckets("tok", 86400, 3600, now=T0 + 3 * 3600 - 1)
    assert len(b) == 24 and [x[1] for x in b[-3:]] == [36000, 36000, 36000] and b[0][1] is None
    assert s.add("tok", 1.0, T0, kind="gauge") is False   # a metric's kind is fixed


def test_store_is_bounded_and_rejects_junk(monkeypatch):
    d = load(monkeypatch)
    s = d.HistoryStore(max_metrics=3)
    assert s.add("a", 1, T0) and s.add("b", 1, T0) and s.add("c", 1, T0)
    assert s.add("d", 1, T0) is False and len(s.names()) == 3
    for bad in (float("nan"), float("inf"), "x", None, 1e30):
        assert s.add("a", bad, T0) is False
    assert s.add("bad name with spaces", 1, T0) is False
    # the ring: a reading 2 h later in the 5 s tier reuses the slot, never grows it
    s.add("a", 5, T0 + 7200)
    assert all(len(t.ts) == cap for t, (_, cap) in zip(s._m["a"][1], s.tiers))
    # a clock stepped back never overwrites a newer slot that owns the index
    s.add("a", 999, T0 + 7200 - 3600)
    assert s.series("a", 900, now=T0 + 7201)["values"][-1] == 5


def test_history_persistence_round_trip_cap_and_corruption(monkeypatch, tmp_path):
    d = load(monkeypatch)
    s = d.HistoryStore()
    now = time.time()
    for k in range(720):
        s.add("node:spark-1:gpu_util", k % 100, now - 3600 + k * 5)
        s.add("serve:gen_tokens", 12, now - 3600 + k * 5, kind="sum")
    path = tmp_path / "dashboard-history.json"
    assert d.save_history(s, path=str(path), now=now) > 0
    assert not list(tmp_path.glob("*.tmp.*")), "temp file renamed over, never left"
    r = d.HistoryStore()
    assert d.load_history(r, path=str(path), now=now) == 2
    assert r.series("node:spark-1:gpu_util", 3600, now)["values"] == s.series("node:spark-1:gpu_util", 3600, now)["values"]
    assert r.total("serve:gen_tokens", 3600, now) == s.total("serve:gen_tokens", 3600, now)
    # size cap: over it, the 5 s tier is left out; still over, nothing is written
    monkeypatch.setattr(d, "HIST_FILE_MAX", len(path.read_bytes()) - 1)
    assert d.save_history(s, path=str(path), now=now) > 0
    r2 = d.HistoryStore()
    d.load_history(r2, path=str(path), now=now)
    assert all(v is None for v in r2.series("node:spark-1:gpu_util", 900, now)["values"])
    assert r2.series("node:spark-1:gpu_util", 6 * 3600, now)["values"][-2] is not None
    monkeypatch.setattr(d, "HIST_FILE_MAX", 100)
    before = path.read_bytes()
    assert d.save_history(s, path=str(path), now=now) == 0 and path.read_bytes() == before
    monkeypatch.setattr(d, "HIST_FILE_MAX", 12 << 20)
    # corrupt, truncated, another layout, hostile rows: nothing (or only the sane rows) restored
    for junk in (b"{not json", before[: len(before) // 2], b"[]",
                 json.dumps({"v": 1, "tiers": [[5, 720]], "metrics": {}}).encode()):
        path.write_bytes(junk)
        assert d.load_history(d.HistoryStore(), path=str(path), now=now) == 0
    path.write_text(json.dumps({"v": 1, "tiers": [list(t) for t in d.HIST_TIERS], "metrics": {
        "ok": {"k": "gauge", "t": {"5": [[int(now // 5 * 5), 3.0, 1, 3.0, 3.0], [7, 1, 1, 1, 1],
                                         [int(now // 5 * 5) - 5, 1, 1, 9, 2], "x", [1e300, 1, 1, 1, 1]]}},
        "bad kind": {"k": "evil", "t": {}}, "a b": {"k": "gauge", "t": {}}}}))
    r3 = d.HistoryStore()
    assert d.load_history(r3, path=str(path), now=now) == 1
    assert [v for v in r3.series("ok", 900, now)["values"] if v is not None] == [3.0]
    # a store that cannot be saved (no .run dir) is simply not saved
    assert d.save_history(s, path=None) == 0


# ═════════════════════════ engine detail: histograms and counters ═════════════════════════

def test_prometheus_histogram_quantiles_match_histogram_quantile(monkeypatch):
    d = load(monkeypatch)
    h = d.prom_histogram(VLLM_FULL, "vllm:time_to_first_token_seconds")
    assert h == {0.1: 0, 0.25: 10, 0.5: 50, 1.0: 90, 2.5: 100, math.inf: 100}
    # p50: rank 50 falls at the top of (0.25, 0.5]; p95: rank 95 in (1.0, 2.5] -> 1.0 + 1.5 * 5/10
    assert d.hist_quantile(0.5, h) == pytest.approx(0.5)
    assert d.hist_quantile(0.95, h) == pytest.approx(1.75)
    assert d.hist_quantile(0.05, h) == pytest.approx(0.1 + 0.15 * 5 / 10)
    # the rank in +Inf answers the highest finite bound; no +Inf or no count is no answer
    assert d.hist_quantile(0.99, {1.0: 1, math.inf: 100}) == 1.0
    assert d.hist_quantile(0.5, {1.0: 5}) is None and d.hist_quantile(0.5, {math.inf: 0}) is None
    # two engines are summed; a longer metric name that shares the prefix is not this one
    two = _hist("vllm:e2e_request_latency_seconds", [("1", 2), ("+Inf", 4)]) + \
        _hist("vllm:e2e_request_latency_seconds", [("1", 3), ("+Inf", 5)], labels='engine="1",model_name="m"') + \
        'vllm:e2e_request_latency_seconds_bucket_extra{le="1"} 99\n'
    assert d.prom_histogram(two, "vllm:e2e_request_latency_seconds") == {1.0: 5, math.inf: 9}
    assert d.prom_histogram("garbage\n{}\nvllm:x_bucket{le=\"nan\"} 1\n", "vllm:x") is None


def test_latency_window_is_the_bucket_delta_and_survives_a_reset(monkeypatch):
    d = load(monkeypatch)
    old = d.engine_detail("vllm", VLLM_FULL)
    later = VLLM_FULL.replace('le="0.25"} 10', 'le="0.25"} 30').replace('le="0.5"} 50', 'le="0.5"} 70') \
        .replace('le="1.0"} 90', 'le="1.0"} 110').replace('le="2.5"} 100', 'le="2.5"} 120') \
        .replace('le="+Inf"} 100\nvllm:time_to_first_token_seconds_count{engine="0",model_name="m"} 100',
                 'le="+Inf"} 120\nvllm:time_to_first_token_seconds_count{engine="0",model_name="m"} 120')
    cur = d.engine_detail("vllm", later)
    w = d.detail_window(cur, old, 300)
    tt = w["latency"]["ttft"]
    # all 20 new observations fell in (0.1, 0.25]: p50 is the middle of that bucket
    assert tt["count"] == 20 and tt["p50"] == pytest.approx(0.1 + 0.15 * 10 / 20) and tt["life_count"] == 120
    assert w["window_s"] == 300 and w["latency"]["e2e"]["count"] == 0 and w["latency"]["e2e"]["p50"] is None
    # an engine restart (counters back to small numbers): the window is everything since it
    fresh = d.engine_detail("vllm", _hist("vllm:time_to_first_token_seconds", [("0.5", 3), ("+Inf", 4)]))
    assert d.detail_window(fresh, cur, 300)["latency"]["ttft"]["count"] == 4


def test_spec_decode_prefix_cache_and_outcomes_vllm(monkeypatch):
    d = load(monkeypatch)
    det = d.engine_detail("vllm", VLLM)
    assert det["g"] == {"running": 4.0, "waiting": 0.0, "kv": pytest.approx(0.142018779342723)}
    w = d.detail_window(det, None, 0)                       # no window yet: lifetime numbers
    assert w["spec"]["life_accept_pct"] == pytest.approx(45.2, abs=0.05)
    assert w["spec"]["life_mean_len"] == pytest.approx(1 + 2.265597e6 / 1.00313e6, abs=0.01)
    assert w["life_prefix_hit_pct"] == pytest.approx(96.1, abs=0.05)
    assert w["finished"] == {"abort": 0, "length": 79, "stop": 3594} and w["preemptions"] == 0
    assert w["spec"]["per_pos_pct"][0] == pytest.approx(74.4, abs=0.05) and len(w["spec"]["per_pos_pct"]) == 2
    nxt = d.engine_detail("vllm", VLLM.replace("2.265597e+06", "2.266597e+06").replace("5.01565e+06", "5.01765e+06")
                          .replace("1.00313e+06", "1.00353e+06").replace("3594.0", "3654.0"))
    w2 = d.detail_window(nxt, det, 60)
    assert w2["spec"]["accept_pct"] == 50.0 and w2["spec"]["mean_len"] == 3.5 and w2["requests_per_min"] == 60.0


def test_llamacpp_has_no_histograms_but_per_request_speeds_and_spec(monkeypatch):
    d = load(monkeypatch)
    det = d.engine_detail("llama", LLAMA)
    assert det["h"] == {} and det["g"] == {"running": 1.0, "waiting": 0.0}
    w = d.detail_window(det, None, 0)
    assert w["latency"] is None
    assert w["per_request"]["life_decode_tok_s"] == pytest.approx(6.37432e6 / 44382.7, abs=0.1)
    assert w["spec"]["life_accept_pct"] == pytest.approx(61.6, abs=0.05)
    # llama's prefix reuse: cached / (cached + processed)
    assert w["life_prefix_hit_pct"] == pytest.approx(100 * 3.00751e7 / (3.00751e7 + 2.25108e6), abs=0.05)
    later = d.engine_detail("llama", LLAMA.replace("6.37432e+06", "6.38712e+06").replace("44382.7", "44482.7"))
    assert d.detail_window(later, det, 100)["per_request"]["decode_tok_s"] == 128.0
    # TensorFold: either its own prefix or vLLM's names
    assert d.engine_detail("tensorfold", "tensorfold:num_requests_running 2\n")["g"]["running"] == 2.0
    assert d.engine_detail("tensorfold", VLLM)["g"]["waiting"] == 0.0


def test_llama_ctx_fill_from_slots(monkeypatch):
    d = load(monkeypatch)
    slots = json.dumps([{"id": 0, "n_ctx": 262144, "is_processing": True, "n_prompt_tokens": 17521,
                         "next_token": [{"n_decoded": 1995}]}, {"id": 1, "n_ctx": 262144, "is_processing": False}])
    assert d.llama_ctx_fill(200, slots) == pytest.approx((17521 + 1995) / 262144)
    assert d.llama_ctx_fill(200, "[]") is None and d.llama_ctx_fill(501, slots) is None
    assert d.llama_ctx_fill(200, "{bad") is None


# ═════════════════════════ node probe: clocks, throttle, fabric history ═════════════════════════

def test_nvml_clocks_and_throttle_reasons(monkeypatch):
    d = load(monkeypatch)
    v = d.node_view(d.parse_node(GB10 + "clk|0|2405|3003||0\n"))
    assert v["gpu"]["sm_clock_mhz"] == 2405 and v["gpu"]["sm_clock_max_mhz"] == 3003
    assert v["gpu"]["mem_clock_mhz"] is None and v["gpu"]["throttle"]["reasons"] == []
    rtx = d.node_view(d.parse_node("gpu|NVIDIA GeForce RTX 5090, 27596, 32607, 95, 62, 512.8\nclk|0|2932|3135|13801|4\n"))
    th = rtx["gpu"]["throttle"]
    assert th["reasons"] == ["power cap"] and th["power_capped"] and not th["slowdown"]
    hot = d.gpu_clocks([{"sm": 1650, "sm_max": 3003, "mem": None, "reasons": 0x40 | 0x1 | 0x800}])
    assert hot["throttle"]["slowdown"] and "hw thermal" in hot["throttle"]["reasons"] and "other 0x800" in hot["throttle"]["reasons"]
    assert d.gpu_clocks([]) == {"sm_clock_mhz": None, "sm_clock_max_mhz": None, "mem_clock_mhz": None, "throttle": None}
    # the probe's NVML read is still one fixed python, with no single quote to break the sh quoting
    assert "'" not in d._NVML_PY and "nvmlDeviceGetCurrentClocksThrottleReasons" in d._NVML_PY


def test_record_node_keeps_only_linked_ports(monkeypatch):
    d = load(monkeypatch)
    s = d.HistoryStore()
    later = GB10.replace("|1000|2000|enp1s0f0np0", "|126000|252000|enp1s0f0np0")
    v0 = d.node_view(d.parse_node(GB10))
    v = d.node_view(d.parse_node(later), d.parse_node(GB10), 5.0)
    d.record_node(s, "spark-2", v, T0)
    d.record_node(s, "spark-3", None, T0)
    names = s.names()
    assert "fab:spark-2:rocep1s0f0/1:tx" in names and not any("rocep1s0f1" in n for n in names)
    assert "node:spark-2:gpu_util" in names and names["node:spark-3:up"] == "gauge"
    assert s.stats("node:spark-3:up", 900, T0)["avg"] == 0.0 and v0["fabric"][0]["tx_bps"] is None


# ═════════════════════════ alerts ═════════════════════════

def _node_snap(name="spark-2", role="peer", **kw):
    n = {"name": name, "role": role, "state": "up",
         "gpu": {"temp_c": 60, "throttle": {"slowdown": False, "reasons": []}},
         "ram": {"used_mib": 100 * 1024, "total_mib": 120 * 1024},
         "disk": {"total_b": 4e12, "free_b": 2e12}}
    n.update(kw)
    return n


def test_alert_conditions_and_thresholds(monkeypatch):
    d = load(monkeypatch)
    th = d.alert_thresholds(conf={})
    assert th["GPU_TEMP_C"] == 85 and th["MEM_FLOOR_GIB"] == 2.5 and th["STALL_S"] == 300 and th["off"] == []
    ok = d.alert_conditions(th, [_node_snap()], {"healthy": True, "gate": {"enabled": False}}, {}, T0)
    assert ok == {}
    nodes = [_node_snap(gpu={"temp_c": 93, "throttle": {"slowdown": True, "reasons": ["hw thermal", "power cap"]}},
                        ram={"used_mib": 118 * 1024, "total_mib": 120 * 1024},
                        disk={"total_b": 4e12, "free_b": 0.1e12}),
             {"name": "spark-3", "role": "peer", "state": "unreachable", "error": "ssh failed"},
             {"name": "spark-1", "role": "head", "state": "unreachable", "error": "probe error"}]
    serve = {"healthy": True, "stalled_s": 400, "running": 2, "kv_pct": 99.0, "waiting": 3,
             "gate": {"enabled": True, "up": False}, "gate_window": {"errors_5m": 30, "requests_5m": 60}}
    c = d.alert_conditions(th, nodes, serve, {"search": {"state": "down", "label": "SearXNG", "since": T0}}, T0)
    lv = {k: v[1] for k, v in c.items()}
    assert lv == {"temp:spark-2": "crit", "throttle:spark-2": "warn", "mem:spark-2": "crit", "disk:spark-2": "crit",
                  "peer:spark-3": "crit", "peer:spark-1": "warn", "engine:stalled": "crit", "kv:full": "warn",
                  "gate:down": "crit", "gate:errors": "warn", "svc:search": "warn"}
    assert "hw thermal" in c["throttle:spark-2"][3] and "power cap" not in c["throttle:spark-2"][3]
    assert "2.0 GiB available" in c["mem:spark-2"][3]
    down = d.alert_conditions(th, [], {"healthy": False, "stalled_s": 999}, {}, T0)
    assert set(down) == {"engine:down"}, "a down engine is not also 'stalled'"
    # memory: warn between the floor and floor + margin
    warn = d.alert_conditions(th, [_node_snap(ram={"used_mib": 116 * 1024, "total_mib": 120 * 1024})], {}, {}, T0)
    assert warn["mem:spark-2"][1] == "warn"
    # a small gate error count, or a small share, is no spike
    assert "gate:errors" not in d.alert_conditions(th, [], {"gate_window": {"errors_5m": 9, "requests_5m": 10}, "gate": {}}, {}, T0)
    assert "gate:errors" not in d.alert_conditions(th, [], {"gate_window": {"errors_5m": 50, "requests_5m": 1000}, "gate": {}}, {}, T0)


def test_thresholds_are_overridable_and_validated(monkeypatch, tmp_path):
    (tmp_path / "openbeast.conf").write_text("DASHBOARD_ALERT_GPU_TEMP_C=70\nHY4_MEMGUARD_GIB=4\n"
                                             "DASHBOARD_ALERT_DISK_FREE_PCT=-5\nDASHBOARD_ALERTS_OFF=temp, disk,bogus\n")
    d = load(monkeypatch, repo=tmp_path, OPENBEAST_DASHBOARD_ALERT_STALL_S="nan", DASHBOARD_ALERT_PEER_S="30")
    th = d.alert_thresholds()
    assert th["GPU_TEMP_C"] == 70 and th["MEM_FLOOR_GIB"] == 4 and th["PEER_S"] == 30
    assert th["DISK_FREE_PCT"] == 10 and th["STALL_S"] == 300        # out of range / not a number: the default
    assert th["off"] == ["disk", "temp"]
    hot = _node_snap(gpu={"temp_c": 99}, disk={"total_b": 100, "free_b": 1})
    assert d.alert_conditions(th, [hot], {}, {}, T0) == {}


def test_alerts_hold_raise_clear_and_count_incidents(monkeypatch):
    d = load(monkeypatch)
    a = d.Alerts()
    cond = {"peer:spark-2": ("peer", "crit", "spark-2 unreachable", "ssh failed", 10.0)}
    a.evaluate(cond, T0)
    assert a.snapshot()["active"] == []                    # not yet held for 10 s
    a.evaluate(cond, T0 + 11)
    snap = a.snapshot()
    assert [x["key"] for x in snap["active"]] == ["peer:spark-2"] and snap["counts"]["crit"] == 1
    a.evaluate({}, T0 + 12)
    assert a.snapshot()["active"], "a condition gone for less than ALERT_CLEAR_S stays raised"
    a.evaluate({}, T0 + 25)
    assert a.snapshot()["active"] == []
    ev = d.events(10, kind="alert")
    assert [e.get("tag") for e in ev] == ["clear", "raise"] and ev[1]["level"] == "crit"
    assert d._daily.rows(1)[0]["incidents"] == 1
    # a flapping condition (down every other probe) keeps its hold timer through the
    # short gaps and raises (review 2026-11-22: it used to reset on every clear tick)
    b = d.Alerts()
    for k in range(5):
        b.evaluate(cond if k % 2 == 0 else {}, T0 + 100 + k * 4)
    assert [x["key"] for x in b.snapshot()["active"]] == ["peer:spark-2"]
    # ...while a single blip, or one blip every 30 s, raises nothing
    c2 = d.Alerts()
    for k in range(10):
        c2.evaluate(cond if k % 6 == 0 else {}, T0 + 200 + k * 5)
    assert c2.snapshot()["active"] == [] and c2._first == {} and c2._gone == {}
    hv = d.health_verdict(True, snap)
    assert hv == {"state": "critical", "reason": "spark-2 unreachable"}
    assert d.health_verdict(False, snap)["state"] == "down" and d.health_verdict(True, {"active": []})["state"] == "ok"


# ═════════════════════════ rollups, swaps, the event log ═════════════════════════

def test_daily_rollups_bounded_and_persisted(monkeypatch, tmp_path):
    d = load(monkeypatch, repo=tmp_path)
    (tmp_path / ".run").mkdir()
    day = d.Daily()
    for k in range(100):
        t = T0 - k * 86400
        day.add(t, "observed_s", 86400)
        day.add(t, "up_s", 86400 - (864 if k == 1 else 0))
        day.add(t, "energy_wh", 760, node="spark-1")
        day.add(t, "gen_tokens", 1e6)
    day.add(T0, "bogus", 1)
    day.add(T0, "gen_tokens", -5)
    day.add(T0, "gen_tokens", float("nan"))
    rows = day.rows()
    assert len(rows) == d.DAILY_KEEP and rows[0]["day"] == d._day(T0)
    assert rows[1]["uptime_pct"] == 99.0 and rows[0]["energy_by_node"] == {"spark-1": 760.0}
    assert rows[0]["gen_tokens"] == 1_000_000
    d._daily = day
    d.event("spark-2 unreachable", "warn", kind="node")
    d._swaps.seen(T0, "Hy4-flash-NVFP4", "hy4-vllm-inference", "vLLM")
    assert d.save_log() > 0
    d2 = load(monkeypatch, repo=tmp_path)
    assert d2.load_log() == {}
    assert d2._daily.rows(1)[0]["gen_tokens"] == 1_000_000
    assert d2.events(5)[0]["text"] == "spark-2 unreachable" and d2.events(5)[0]["kind"] == "node"
    assert d2._swaps.rows()[0]["model"] == "Hy4-flash-NVFP4"
    (tmp_path / ".run" / "dashboard-log.json").write_text('{"v": 1, "events": [{"t": "x"}, 5], "daily": {"../x": {}},'
                                                         ' "swaps": [{"model": 3}], "services": []}')
    d3 = load(monkeypatch, repo=tmp_path)
    assert d3.load_log() == {} and d3.events() == [] and d3._daily.rows() == [] and d3._swaps.rows() == []


def test_model_swaps(monkeypatch):
    d = load(monkeypatch)
    s = d.Swaps()
    assert s.seen(T0, None, "u", "vLLM") is None and s.rows() == []
    assert s.seen(T0, "DeepSeek", "deepseek-v41-inference", "TensorFold") is None
    assert s.seen(T0 + 60, "DeepSeek", None, "TensorFold") is None          # unit unknown: the same stretch
    prev = s.seen(T0 + 120, "Hy4", "hy4-vllm-inference", "vLLM")
    assert prev["model"] == "DeepSeek"
    rows = s.rows()
    assert [r["model"] for r in rows] == ["Hy4", "DeepSeek"] and rows[1]["to"] == T0 + 60 and rows[0]["to"] is None
    for k in range(d.SWAPS_MAX + 10):
        s.seen(T0 + 200 + k, f"m{k}", "u", "e")
    assert len(s.rows()) == d.SWAPS_MAX


def test_event_log_is_bounded_and_filtered(monkeypatch):
    d = load(monkeypatch)
    for k in range(d.EVENTS_MAX + 20):
        d.event(f"e{k}", "warn" if k % 2 else "info", kind="node" if k % 3 else "service")
    d.event("x", level="nonsense", kind="nonsense")
    assert len(d.events(10_000)) == d.EVENTS_MAX and d.events(10_000)[0] == {**d.events(1)[0]}
    assert d.events(1)[0]["kind"] == "dashboard" and d.events(1)[0]["level"] == "info"
    assert len(d.events()) == d.EVENTS_STATUS
    assert all(e["kind"] == "service" for e in d.events(50, kind="service"))
    ids = [e["id"] for e in d.events(100)]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 100


# ═════════════════════════ the serve sampler, end to end ═════════════════════════

def test_serve_sampler_records_history_rollups_detail_and_stalls(monkeypatch, tmp_path):
    d = load(monkeypatch, repo=tmp_path, **_SLOT_ENV["vllm"])
    gen = {"v": 3_000_000.0, "prm": 1000.0}

    def get(url, timeout=2, auth=False):
        if url.endswith(":8000/metrics"):
            return 200, VLLM_FULL.replace("3.267023e+06", repr(gen["v"])).replace("7.984854e+07", repr(gen["prm"]))
        if url.endswith(":8000/v1/models"):
            return 200, '{"data":[{"id":"Hy4-flash-NVFP4"}]}'
        return _canned(_SLOT_ANS["vllm"])(url)
    d._get = get
    d._gate_get = lambda path: (None, "")
    d.inference_unit = lambda units=None: {"name": "hy4-vllm-inference", "active": True, "uptime_s": 60}
    s = d.ServeSampler()
    now = time.time()
    for k in range(4):
        s.sample(now=now + k * 3)
        gen["v"] += 300
        gen["prm"] += 900
    snap = s.snapshot()
    assert snap["tok_s"] == 100.0 and snap["running"] == 4 and snap["kv_pct"] == pytest.approx(14.2, abs=0.01)
    assert snap["detail"]["latency"]["ttft"]["life_p50"] == pytest.approx(0.5) and snap["stalled_s"] == 0.0
    assert snap["model_id"] == "Hy4-flash-NVFP4"
    assert d._store.total("serve:gen_tokens", 900, now + 10) == 900
    assert d._store.series("serve:tok_s", 900, now + 9)["values"][-1] == 100.0     # the slot of the last sample
    assert d._daily.rows(1)[0]["gen_tokens"] == 900 and d._daily.rows(1)[0]["observed_s"] == 9
    assert d._swaps.rows()[0]["model"] == "Hy4-flash-NVFP4"
    w = s.watch.get()
    assert w["model"]["state"] == "up" and w["tools"]["state"] == "up"
    # four requests running and no counter moving: the stall clock runs
    for k in range(4, 8):
        s.sample(now=now + k * 30)
    assert s.snapshot()["stalled_s"] >= 60
    c = d.alert_conditions(d.alert_thresholds(conf={}), [], dict(s.snapshot(), stalled_s=301), {}, now)
    assert "engine:stalled" in c


# ═════════════════════════ HTTP: the v3 routes ═════════════════════════

def _populated(monkeypatch, **env):
    d = load(monkeypatch, **env)
    _no_upstreams(d, monkeypatch)
    head = d.NodeSampler("spark-1", None, "head", 0)
    head.sample(run=lambda t: (GB10, None))
    peer = d.NodeSampler("spark-2", "spark-2", "peer", 1)
    peer.sample(run=lambda t: (GB10, None))
    d._head, d._peers = head, [peer]
    d._serve = d.ServeSampler()
    d._serve.sample()
    now = time.time()
    for k in range(50):
        d._store.add("gate:rpm", k, now - 250 + k * 5)
        d._store.add("gate:errors", 1, now - 250 + k * 5, kind="sum")
    d.event("hello <b>", kind="node")
    return d


def test_v3_routes_answer_and_validate(monkeypatch):
    d = _populated(monkeypatch)
    live = Live(d)
    try:
        def get(path, host="127.0.0.1"):
            st, hdr, body = live.req("GET", path, host)
            return st, hdr, (json.loads(body) if body and hdr.get("Content-Type") == "application/json" else body)

        st, hdr, doc = get("/api/serving?window=6h")
        assert st == 200 and doc["window"] == "6h" and doc["step"] == 60 and hdr["Cache-Control"] == "no-store"
        assert get("/api/serving?window=../../etc")[2]["window"] == "1h"
        st, _, doc = get("/api/node/spark-2?window=24h")
        assert st == 200 and doc["node"]["name"] == "spark-2" and doc["step"] == 60 and "gpu_util" in doc["series"]
        assert "fabric_series" in doc and doc["power_windows"]["1h"]["coverage"] >= 0
        for bad in ("/api/node/nope", "/api/node/..%2f..%2fetc", "/api/node/", "/api/node/spark-2%00"):
            st, _, doc = get(bad)
            assert st == 404 and doc["error"] == "unknown", bad
        st, _, doc = get("/api/history?metric=gate:rpm,gate:errors,nope&window=1h")
        assert st == 200 and set(doc["series"]) == {"gate:rpm", "gate:errors"} and doc["unknown"] == ["nope"]
        assert doc["kinds"] == {"gate:rpm": "gauge", "gate:errors": "sum"}
        assert doc["stats"]["gate:errors"]["total"] == 50
        cat = get("/api/history")[2]
        assert {"name": "gate:rpm", "kind": "gauge"} in cat["metrics"] and "7d" in cat["windows"]
        many = ",".join(f"m{k}" for k in range(50))
        assert len(get("/api/history?metric=" + many)[2]["unknown"]) == d.HISTORY_MAX_METRICS
        junk = "&".join(f"a{k}=1" for k in range(20))
        assert get("/api/history?" + junk)[0] == 200                   # too many fields: no query
        assert get("/api/history?metric=" + "x" * 5000)[0] == 200
        st, _, doc = get("/api/events?kind=node&limit=999999")
        assert st == 200 and doc["kind"] == "node" and all(e["kind"] == "node" for e in doc["events"])
        assert doc["events"][0]["text"] == "hello <b>"                  # data, as text
        assert get("/api/events?kind=<script>&limit=-4")[2]["kind"] is None
        assert len(get("/api/events?limit=abc")[2]["events"]) <= 200
        st, _, doc = get("/api/alerts")
        assert st == 200 and "active" in doc and any(t["name"] == "DASHBOARD_ALERT_GPU_TEMP_C" for t in doc["thresholds"])
        st, _, doc = get("/api/daily")
        assert st == 200 and "last_24h" in doc and isinstance(doc["days"], list) and isinstance(doc["swaps"], list)
        st, _, doc = get("/api/status")
        assert {"alerts", "health", "summary"} <= set(doc) and doc["health"]["state"] in ("ok", "down", "degraded", "critical")
        assert get("/api/nothing")[0] == 404 and get("/api/servingx")[0] == 404
    finally:
        live.close()


@pytest.mark.parametrize("path", ["/api/serving", "/api/node/spark-1", "/api/history?metric=gate:rpm",
                                  "/api/events", "/api/alerts", "/api/daily"])
def test_v3_routes_are_host_pinned_and_get_only(monkeypatch, path):
    d = _populated(monkeypatch)
    live = Live(d)
    try:
        assert live.req("GET", path, "evil.example:3002")[0] == 400
        assert live.req("GET", path, "attacker.spark-1.tail4109f9.ts.net.evil.com")[0] == 400
        assert live.req("GET", path, "spark-1.tail4109f9.ts.net:8447")[0] == 200
        for m in ("POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"):
            st, hdr, _ = live.req(m, path)
            assert st == 405 and hdr.get("Allow") == "GET", m
    finally:
        live.close()


def test_no_request_runs_a_probe_on_the_v3_routes(monkeypatch):
    d = _populated(monkeypatch)
    calls = []
    d._get = lambda url, timeout=2, auth=False: calls.append(url) or (None, "")
    d._gate_get = lambda path: calls.append(path) or (None, "")
    live = Live(d)
    try:
        for p in ("/api/serving", "/api/node/spark-1", "/api/history?metric=gate:rpm", "/api/events",
                  "/api/alerts", "/api/daily"):
            assert live.req("GET", p)[0] == 200
        assert calls == [], calls
    finally:
        live.close()


@pytest.mark.parametrize("backend", ["llama", "vllm", "tensorfold"])
def test_api_slot_is_still_byte_identical_with_the_v3_state_populated(monkeypatch, backend):
    d = load(monkeypatch, **_SLOT_ENV[backend])
    d._get = _canned(_SLOT_ANS[backend])
    d._store.add("serve:tok_s", 5, time.time())
    d._alerts.evaluate({"engine:down": ("engine", "crit", "Model server down", "x", 0.0)}, time.time())
    d.event("x", kind="alert")
    live = Live(d)
    try:
        st, hdr, body = live.req("GET", "/api/slot", "client.lan:8444")
        assert st == 200 and body.decode() == _SLOT_GOLDEN[backend]
    finally:
        live.close()


# ═════════════════════════ the page ═════════════════════════

def test_the_page_has_every_route_and_stays_csp_clean():
    html = open(os.path.join(DASH, "static", "index.html")).read()
    for page in ("home", "serving", "nodes", "node", "power", "gate", "services", "tools", "events", "history"):
        assert f'data-page="{page}"' in html, page
    for route in ("#/", "#/serving", "#/nodes", "#/power", "#/gate", "#/services", "#/tools", "#/events", "#/history"):
        assert f'href="{route}"' in html, route
    assert "<script>" not in html and " style=" not in html and "<style" not in html
    assert html.count("<script") == 1 and 'src="/static/app.js"' in html
    # the power detail lives on its own page, not the overview
    home = html.split('data-page="home"', 1)[1].split('data-page="serving"', 1)[0]
    assert 'id="pw-windows"' not in home and 'id="pw-svg"' not in home and 'href="#/power"' in home


def _node(script, tmp_path, data):
    from test_dashboard_v2 import _node as run
    return run(script, tmp_path, data)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_router_and_chart_model(tmp_path):
    out = _node("const r = ['', '#/', '#/power', '#/nodes', '#/nodes/spark-2', '#/nodes/a%2Fb', '#/nodes/%E0%A4%A',"
                " '#/bogus', '#/node', '#/events?x=1', '#/nodes/' + 'x'.repeat(200)].map(h => __obdash.parseRoute(h));"
                "const doc = {window: '1h', window_s: 3600, t0: 0, step: 5, series: {a: D.a, b: D.a, s: D.s}};"
                "const m = __obdash.tsModel(doc, {series: [['a', 'A'], ['b', 'B'], ['zz', 'Z']], dedupe: true, max: 100});"
                "const p = __obdash.tsModel(doc, {series: [['s', 'per min']], perMin: true});"
                "process.stdout.write(JSON.stringify({r, m: {n: m.series.length, top: m.top, last: m.last},"
                " p: {vals: p.series[0].values.slice(0, 3), step: p.stepS, n: p.n}, b: __obdash.bucketSum([1, null, 2, null, null, null], 3)}));",
                tmp_path, {"a": [1, None, 50, None], "s": [1] * 24})
    o = json.loads(out)
    pages = [x["page"] for x in o["r"]]
    assert pages == ["home", "home", "power", "nodes", "node", "node", "node", "home", "home", "home", "node"]
    assert o["r"][4]["arg"] == "spark-2" and o["r"][5]["arg"] == "a/b" and o["r"][6]["arg"] == "%E0%A4%A"
    assert len(o["r"][10]["arg"]) == 64
    assert o["m"] == {"n": 1, "top": 100, "last": [50]}, "an identical series is dropped; a missing one is not drawn"
    assert o["p"] == {"vals": [12, 12], "step": 60, "n": 2}, "a counter per 5 s slot, shown per minute"
    assert o["b"] == [3, None]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_v3_renderers_write_hostile_strings_as_text(tmp_path):
    x = "<img src=x onerror=alert(1)>"
    data = {
        "status": {"model": {"healthy": True, "id": x}, "serving": {"detail": {"latency": {"ttft": {"p50": 0.5, "p95": 1.2}},
                                                                             "finished": {x: 3}}},
                   "alerts": {"active": [{"key": x, "level": x, "title": x, "detail": x, "since": 1}], "counts": {"crit": 0, "warn": 0}},
                   "health": {"state": "degraded", "reason": x}, "summary": {"gen_tokens": 5},
                   "service_list": [{"name": "s", "label": x, "state": "up", "since": 1, "last_change": 2}],
                   "gate": {"enabled": True, "denied_by_reason": {x: 4}}, "events": [{"t": 1, "text": x, "kind": x, "id": 1}]},
        "events": {"events": [{"t": 1, "text": x, "kind": x, "level": x, "id": 1}], "by_kind": {}, "max": 500},
        "history": {"last_24h": {"nodes": [{"name": x}], "incident_list": [{"t": 1, "text": x, "id": 2}]},
                    "days": [{"day": x}], "swaps": [{"model": x, "unit": x, "engine": x, "from": 1}]},
        "serving": {"window": "1h", "window_s": 3600, "t0": 0, "step": 5, "series": {}, "now": {},
                    "detail": {"finished": {x: 3}, "spec": {"per_pos_pct": [50]}}, "tokens": {"daily": [{"day": x, "gen_tokens": 1}]}},
    }
    js = ("__obdash.renderStatus(D.status); __obdash.renderStatus(D.status); __obdash.renderEvents(D.events);"
          "__obdash.renderHistory(D.history); __obdash.renderServing(D.serving);"
          "const t = id => document.getElementById(id).textContent;"
          "const o = __obdash.overall(D.status, {nodes: []});"
          "process.stdout.write(JSON.stringify({al: t('alert-list'), n: document.getElementById('alert-list').children.length,"
          " tl: t('ev-timeline'), sw: t('swaps'), inc: t('hs-incidents'), ref: t('refused'), svc: t('services'), o}));")
    o = json.loads(_node(js, tmp_path, data))
    assert x in o["al"] and o["n"] == 1 and x in o["tl"] and x in o["inc"] and x in o["ref"] and x in o["svc"]
    assert o["o"] == {"state": "degraded", "text": x}


def test_llama_window_with_no_finished_request_falls_back_to_lifetime_positions(monkeypatch):
    # llama.cpp counts a request's drafts only when it finishes: a quiet window has
    # no acceptance of its own, and the per-position bars are the lifetime ones, not 0 %.
    d = load(monkeypatch)
    a = d.engine_detail("llama", LLAMA)
    sp = d.detail_window(d.engine_detail("llama", LLAMA), a, 20)["spec"]
    assert sp["accept_pct"] is None and sp["life_accept_pct"] == pytest.approx(61.6, abs=0.05)
    assert sp["per_pos_pct"] == [pytest.approx(82.5, abs=0.05), pytest.approx(66.4, abs=0.05)]


# ═════════════════════════ review 2026-11-22 (fix round) ═════════════════════════

def test_verdict_counts_every_alert_it_does_not_name(monkeypatch):
    d = load(monkeypatch)
    a = lambda t, lv: {"title": t, "level": lv}
    v = lambda *al: d.health_verdict(True, {"active": list(al)})["reason"]
    assert v(a("c", "crit"), a("w", "warn")) == "c; w"
    assert v(a("c", "crit"), *[a(f"w{i}", "warn") for i in range(5)]) == "c; w0 (+4 more)"
    assert v(a("c1", "crit"), a("c2", "crit"), a("w", "warn")) == "c1; c2 (+1 more)"
    assert v(a("c", "crit")) == "c"


def test_alerts_survive_a_restart_without_a_second_raise(monkeypatch, tmp_path):
    (tmp_path / ".run").mkdir()
    cond = {"peer:spark-2": ("peer", "crit", "spark-2 unreachable", "ssh failed", 10.0)}
    d = load(monkeypatch, repo=tmp_path)
    d._alerts.evaluate(cond, T0)
    d._alerts.evaluate(cond, T0 + 15)
    assert d.save_log(now=T0 + 16) > 0
    d2 = load(monkeypatch, repo=tmp_path)
    d2.load_log()
    d2._alerts.evaluate(cond, T0 + 100)
    act = d2._alerts.snapshot()["active"]
    assert [x["key"] for x in act] == ["peer:spark-2"] and act[0]["since"] == T0
    assert [e.get("tag") for e in d2.events(10, kind="alert")] == ["raise"]
    assert d2._daily.rows(1)[0]["incidents"] == 1
    d2._alerts.evaluate({}, T0 + 101)
    d2._alerts.evaluate({}, T0 + 120)
    assert d2._alerts.snapshot()["active"] == []
    assert [e.get("tag") for e in d2.events(10, kind="alert")] == ["clear", "raise"]
    # junk alert rows restore nothing
    assert d2._alerts.restore([{"key": 5}, {"key": "k", "rule": "r", "level": "bogus", "since": 1}, "x"]) == 0


def test_escalation_logs_once_and_raises_outlive_event_churn(monkeypatch):
    d = load(monkeypatch)
    a = d.Alerts()
    hot = lambda lv: {"temp:spark-1": ("temp", lv, "GPU hot on spark-1", "x", 0.0)}
    for k in range(10):
        a.evaluate(hot("warn" if k % 2 == 0 else "crit"), T0 + k * 5)
    assert len(d.events(50, kind="alert")) == 2          # the raise and one "now crit"
    for k in range(600):                                  # a flapping node floods the ring
        d.event(f"spark-2 {'down' if k % 2 else 'up'}", kind="node", now=T0 + 60 + k)
    assert d.summary_24h(now=T0 + 700)["incidents"] == 1
    assert len(d.daily_status()["last_24h"]["incident_list"]) <= 1


def test_history_dead_names_expire_and_free_the_cap(monkeypatch, tmp_path):
    d = load(monkeypatch)
    s = d.HistoryStore()
    old = T0 - 30 * 86400
    for k in range(s.max_metrics):
        assert s.add(f"fab:spark-1:old{k}/1:tx", 1.0, old)
    doc = s.dump(now=T0)
    assert doc == {}, "a metric with no live row is not saved"
    s2 = d.HistoryStore()
    assert s2.load({f"x{k}": {"k": "gauge", "t": {"5": [[old, 1, 1, 1, 1]]}} for k in range(200)}, now=T0) == 0
    assert s2._m == {}, "a name with no valid row takes no slot"
    assert s.add("serve:tok_s", 5.0, T0), "a dead name is evicted at the cap"
    full = d.HistoryStore(max_metrics=2)
    assert full.add("a", 1, T0) and full.add("b", 1, T0) and not full.add("c", 1, T0)


def test_log_with_numbers_as_strings_loads(monkeypatch, tmp_path):
    (tmp_path / ".run").mkdir()
    (tmp_path / ".run" / "dashboard-log.json").write_text(json.dumps(
        {"v": 1, "events": [{"t": "1.5", "text": "x"}, {"t": "abc", "text": "y"}],
         "swaps": [{"model": "m", "from": "1e3"}, {"model": "n", "from": "zz"}]}))
    d = load(monkeypatch, repo=tmp_path)
    d.load_log()
    assert [(e["t"], e["text"]) for e in d.events()] == [(1, "x")]
    assert [(r["from"], r["model"]) for r in d._swaps.rows()] == [(1000, "m")]


def test_swap_unit_learned_later_is_not_a_swap(monkeypatch):
    d = load(monkeypatch)
    assert d._swaps.seen(T0, "m", None, "vLLM") is None
    assert d._swaps.seen(T0 + 15, "m", "hy4-vllm-inference", "vLLM") is None
    rows = d._swaps.rows()
    assert len(rows) == 1 and rows[0]["unit"] == "hy4-vllm-inference"


def test_vllm_prompt_counts_computed_prefill_not_cache_hits(monkeypatch):
    d = load(monkeypatch)
    by = ('vllm:prompt_tokens_total{engine="0"} 100\nvllm:prompt_tokens_cached_total{engine="0"} 90\n'
          'vllm:prompt_tokens_by_source_total{engine="0",source="local_compute"} 10\n'
          'vllm:prompt_tokens_by_source_total{engine="0",source="local_cache_hit"} 90\n')
    assert d.engine_counters("vllm", by)[1] == 10
    assert d.engine_counters("vllm", by.split("vllm:prompt_tokens_by_source")[0])[1] == 10
    assert d.engine_counters("vllm", 'vllm:prompt_tokens_total{engine="0"} 100\n')[1] == 100


def test_vllm_decode_per_request_from_request_decode_time_under_mtp(monkeypatch):
    d = load(monkeypatch)
    itl = [("0.05", 0), ("0.1", 0), ("0.2", 1000), ("+Inf", 1000)]       # p50 ~0.15 s per STEP

    def body(gen, dec, steps):
        return (_hist("vllm:inter_token_latency_seconds", [(le, n * steps // 1000) for le, n in itl])
                + f'vllm:spec_decode_num_drafts_total{{engine="0"}} {steps}\n'
                + f'vllm:spec_decode_num_draft_tokens_total{{engine="0"}} {steps * 3}\n'
                + f'vllm:spec_decode_num_accepted_tokens_total{{engine="0"}} {int(steps * 2.2)}\n'
                + f'vllm:request_generation_tokens_sum{{engine="0"}} {gen}\n'
                + f'vllm:request_decode_time_seconds_sum{{engine="0"}} {dec}\n')
    a, b = d.engine_detail("vllm", body(3200, 160, 1000)), d.engine_detail("vllm", body(9600, 480, 3000))
    pr = d.detail_window(b, a, 300)["per_request"]
    assert pr["decode_tok_s"] == 20.0 and pr["life_decode_tok_s"] == 20.0 and pr["from"] == "request decode time"
    # without the request histograms, 1/ITL is scaled by the tokens each step emits
    a2 = d.engine_detail("vllm", body(0, 0, 1000).split("vllm:request_generation")[0])
    b2 = d.engine_detail("vllm", body(0, 0, 3000).split("vllm:request_generation")[0])
    pr2 = d.detail_window(b2, a2, 300)["per_request"]
    assert pr2["decode_tok_s"] > 1.5 / d.detail_window(b2, a2, 300)["latency"]["itl"]["p50"]


def test_llama_prompt_rate_is_live_during_prefill(monkeypatch):
    d = load(monkeypatch)
    slot = lambda n, dec: json.dumps([{"id": 0, "is_processing": True, "n_prompt_tokens_processed": n,
                                       "next_token": [{"n_decoded": dec}]}])
    assert d.llama_inflight_prompt(200, slot(4096, 0)) == 4096
    assert d.llama_inflight_prompt(200, slot(4096, 3)) == 0, "past prefill the counter has it"
    assert d.llama_inflight_prompt(500, "") is None and d.llama_inflight_prompt(200, "{}") is None
    d = load(monkeypatch, **_SLOT_ENV["llama"])
    st = {"prm": 1000, "n": 0, "dec": 0}

    def get(url, timeout=2, auth=False):
        if url.endswith("/metrics"):
            return 200, f"llamacpp:tokens_predicted_total 5000\nllamacpp:prompt_tokens_total {st['prm']}\n"
        if url.endswith("/slots"):
            return 200, slot(st["n"], st["dec"])
        return _canned(_SLOT_ANS["llama"])(url)
    d._get = get
    d._gate_get = lambda path: (None, "")
    d.inference_unit = lambda units=None: {"name": "llama-server", "active": True, "uptime_s": 60}
    s = d.ServeSampler()
    rates = []
    for k in range(8):
        st["n"] = 2000 * k
        s.sample(now=T0 + k * 3)
        rates.append(s.snapshot()["prompt_tok_s"])
    assert rates[-1] and rates[-1] > 500, rates
    st.update(prm=1000 + 14000, n=14000, dec=1)            # prefill ends: counter +N, live -N
    s.sample(now=T0 + 24)
    assert s.snapshot()["prompt_tok_s"] < 1000, "no spike at the first token"
    assert s.snapshot()["prompt_tok_s_source"] == "engine"


def test_no_progress_counter_is_no_stall(monkeypatch):
    d = load(monkeypatch, **_SLOT_ENV["tensorfold"])
    d._get = _canned({"/health": (200, json.dumps({"ok": True, "status": "ok", "requests_running": 2, "iteration_s": 0.05})),
                      "/metrics": (404, "")})
    d._gate_get = lambda path: (None, "")
    d.inference_unit = lambda units=None: {"name": "x", "active": True, "uptime_s": 60}
    s = d.ServeSampler()
    for k in range(120):
        s.sample(now=T0 + k * 3)
    snap = s.snapshot()
    assert snap["stalled_s"] is None
    assert "engine:stalled" not in d.alert_conditions(d.alert_thresholds(conf={}), [], snap, {}, T0 + 400)


def test_gate_refusals_and_disconnects_raise_no_error_spike(monkeypatch):
    d = load(monkeypatch)
    by = {"ok": 28, "client_disconnect": 6, "rate_limited": 4, "fanout": 2, "gone": 1}
    assert sum(v for k, v in by.items() if k in d._GATE_ERROR_OUTCOMES) == 0
    assert {"upstream_error", "upstream_timeout", "upstream_status"} == set(d._GATE_ERROR_OUTCOMES)


def test_detail_empty_states_start_hidden_and_tables_are_focusable():
    html = open(os.path.join(DASH, "static", "index.html")).read()
    for i in ("spec-empty", "finished-empty", "hs-incidents-empty", "days-empty", "swaps-empty"):
        assert f'id="{i}" hidden' in html, i
    assert html.count('class="table-wrap" tabindex="0" role="region"') == 3
    assert 'id="page-stale"' in html and 'class="brand" href="#/">' in html


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_alert_links_stale_detail_and_unreachable_node(tmp_path):
    js = r"""
    const rules = D.rules.map(r => [r, __obdash.alertHref({rule: r, key: r + ':spark-2'})]);
    const hostile = __obdash.alertHref({rule: 'peer', key: 'peer:a"b<c>/d'});
    __obdash.renderAlerts({active: [{key: 'peer:spark-2', rule: 'peer', level: 'crit', title: 't', since: 1}], counts: {crit: 1}});
    const a = document.getElementById('alert-list').children[0].children[0];
    // a detail feed that stopped answering marks its page stale
    const sec = new El('section'); sec.dataset.page = 'serving';
    document.querySelectorAll = () => [sec];
    __obdash._setRoute({page: 'serving', arg: null});
    const url = __obdash.pageWants()[0][0];
    __obdash._pageOk.set(url, Date.now() - 3 * __obdash.pageWants()[0][2]);
    __obdash.renderPageStale();
    const stale = {sec: sec.dataset.stale, note: document.getElementById('page-stale').textContent,
                   hidden: document.getElementById('page-stale').hidden};
    __obdash._pageOk.set(url, Date.now()); __obdash.renderPageStale();
    stale.fresh = sec.dataset.stale;
    // an unreachable node's last readings are marked and dimmed
    __obdash._setRoute({page: 'node', arg: 'spark-2'});
    __obdash.renderNodeHead({name: 'spark-2', state: 'unreachable', last_ok_age_s: 20, gpu: {name: 'NVIDIA GB10', util_pct: 97, temp_c: 95}});
    const nt = document.getElementById('nd-tiles');
    process.stdout.write(JSON.stringify({rules, hostile, href: a.attrs.href, cls: a.className, lvl: a.dataset.level, stale,
      nd: nt.dataset.stale, tiles: nt.textContent, warn: nt.children.map(c => c.dataset.state).filter(Boolean)}));
    """
    o = json.loads(_node(js, tmp_path, {"rules": ["peer", "temp", "throttle", "mem", "disk", "engine", "stall", "kv",
                                                  "gate", "service", "other"]}))
    r = dict(o["rules"])
    assert r["peer"] == r["temp"] == r["disk"] == "#/nodes/spark-2"
    assert r["engine"] == r["stall"] == r["kv"] == "#/serving" and r["gate"] == "#/gate"
    assert r["service"] == "#/services" and r["other"] == "#/events"
    assert o["hostile"] == "#/nodes/a%22b%3Cc%3E%2Fd"
    assert o["href"] == "#/nodes/spark-2" and o["cls"] == "alert" and o["lvl"] == "crit"
    assert o["stale"]["sec"] == "1" and "detail stale" in o["stale"]["note"] and not o["stale"]["hidden"]
    assert o["stale"]["fresh"] == ""
    assert o["nd"] == "1" and "last reading 20s ago" in o["tiles"] and o["warn"] == [], "an old reading is no live alarm"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_serving_labels_lifetime_fallbacks_as_such(tmp_path):
    doc = {"window": "1h", "window_s": 3600, "t0": 0, "step": 5, "series": {}, "now": {},
           "detail": {"window_s": 300, "latency": {"ttft": {"p50": None, "p95": None, "count": 0, "life_p50": 0.8,
                                                            "life_p95": 2.3, "life_count": 90},
                                                   "e2e": {"p50": 5.0, "p95": 9.0, "count": 3}},
                      "spec": {"accept_pct": None, "life_accept_pct": 44.9, "life_mean_len": 3.2}}}
    js = ("__obdash.renderServing(D);"
          "const t = document.getElementById('sv-tiles').children.map(c => [c.children[1].textContent, c.children[2].textContent]);"
          "process.stdout.write(JSON.stringify({t, note: document.getElementById('spec-note').textContent}));")
    o = json.loads(_node(js, tmp_path, doc))
    t = dict(o["t"])
    assert t["TTFT p50 · p95"] == "since engine start · 90 req"
    assert t["end-to-end p50 · p95"] == "last 5m"
    assert t["spec. acceptance"].startswith("since engine start") and "3.20" in t["spec. acceptance"]
    assert o["note"] == "since engine start"


def test_a_flapping_node_does_not_flood_the_event_log(monkeypatch):
    d = load(monkeypatch)
    n = d.NodeSampler("spark-2", "spark-2", "peer", 1)
    n.state = "up"
    for k in range(520):
        n._set_state("ssh failed" if k % 2 == 0 else None, view={} if k % 2 else None)
    ev = d.events(d.EVENTS_MAX, kind="node")
    assert len(ev) < 20 and any("flapping" in e["text"] for e in ev)
