#!/usr/bin/env python3
"""beast-slot /api/slot contract tests (docs/BEAST_SLOT.md).

The dashboard extension's slot_status() is the discovery surface clients
depend on: real loaded model, slots busy/total, context, capacity, health,
auth mode. These tests pin the JSON shape against canned upstream bodies so a
llama-server or dashboard refactor can't silently break remote clients.

Contract v2 is additive over v1 — the v1 compatibility test below is the
guard that keeps it that way.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "dashboard", os.path.join(REPO, "extensions", "dashboard", "dashboard.py"))
dashboard = importlib.util.module_from_spec(_SPEC)
sys.modules["dashboard"] = dashboard
_SPEC.loader.exec_module(dashboard)


# /props carries the per-slot context as default_generation_settings.n_ctx
# (server-context.cpp: meta->slot_n_ctx) — the same number /slots reports as
# n_ctx, and the only source left when the server runs --no-slots.
_PROPS = {"total_slots": 1, "model_alias": "heretic-v2-27b-mtp-q6",
          "model_path": "/weights/x.gguf",
          "default_generation_settings": {"n_ctx": 212992}}
# Current llama-server slot shape (is_processing); older builds used state.
_SLOTS_CURRENT = [{"id": 0, "n_ctx": 212992, "is_processing": True}]
_SLOTS_LEGACY = [{"id": 0, "n_ctx": 65536, "state": 1}]
_MODELS = {"data": [{"id": "heretic-v2-27b-mtp-q6"}]}
# llama-server's Prometheus text (--metrics); 501 without it.
_METRICS = ("# HELP llamacpp:requests_processing Number of requests processing.\n"
            "# TYPE llamacpp:requests_processing gauge\n"
            "llamacpp:requests_processing 1\n"
            "# HELP llamacpp:requests_deferred Number of requests deferred.\n"
            "# TYPE llamacpp:requests_deferred gauge\n"
            "llamacpp:requests_deferred 4\n")

# Every key a v1 client parses, with the nested shape it expects. v2 must keep
# all of them, identically named.
_V1_KEYS = {"beast_slot", "healthy", "model", "slots", "services", "auth"}


def _fake_get(responses):
    """responses: dict of url-suffix -> (status, body-dict-or-str)."""
    def get(url, timeout=2, auth=False):
        for suffix, (st, body) in responses.items():
            if url.endswith(suffix):
                return st, body if isinstance(body, str) else json.dumps(body)
        return None, ""
    return get


class TestSlotContract(unittest.TestCase):
    def setUp(self):
        self._real_get = dashboard._get
        # _API_KEY is frozen from the ambient env at import — pin it here so
        # the auth assertions test the CODE, not whoever's shell ran pytest
        # (conf.sh exports OPENBEAST_API_KEY on every keyed rig).
        self._real_key = dashboard._API_KEY
        dashboard._API_KEY = ""
        # _kv_unified() reads the REAL repo's .run/serve-script; pin it per
        # test so capacity assertions don't depend on what this box is serving.
        self._real_kv = dashboard._kv_unified
        # Same for the backend: a shell that sourced conf.sh on a Spark-backed
        # rig exports OPENBEAST_INFERENCE_BACKEND/URL. These are llama tests.
        self._real_backend = (dashboard._BACKEND, dashboard._INFER)
        dashboard._BACKEND, dashboard._INFER = "llama", "http://127.0.0.1:8080"

    def tearDown(self):
        dashboard._get = self._real_get
        dashboard._API_KEY = self._real_key
        dashboard._kv_unified = self._real_kv
        dashboard._BACKEND, dashboard._INFER = self._real_backend

    def _status(self, responses, kv=None):
        dashboard._get = _fake_get(responses)
        dashboard._kv_unified = lambda: kv
        return dashboard.slot_status()

    def test_full_healthy_contract(self):
        out = self._status({
            "/health": (200, "ok"), "/v1/models": (200, _MODELS),
            "/props": (200, _PROPS), "/slots": (200, _SLOTS_CURRENT),
            "/metrics": (200, _METRICS),
            "/api/version": (200, "version x"), "8888/": (200, ""),
        }, kv=True)
        self.assertEqual(out["beast_slot"], 2)
        self.assertEqual(out["min_client"], 1)
        self.assertTrue(out["healthy"])
        self.assertEqual(out["model"]["id"], "heretic-v2-27b-mtp-q6")
        self.assertEqual(out["model"]["ctx"], 212992)
        self.assertEqual(out["slots"], {"total": 1, "busy": 1})
        self.assertEqual(out["capacity"], {
            "ctx_shared": True, "ctx_total": 212992,
            "queue_deferred": 4, "serving_profile": "mtp-single-slot"})
        self.assertEqual(out["auth"], "open")
        # Contract keys are exactly these — clients parse this shape.
        self.assertEqual(
            set(out), {"beast_slot", "min_client", "healthy", "model", "slots",
                       "capacity", "services", "auth"})

    def test_v1_client_compatibility(self):
        # v2 is additive: a v1 parser must still find every field it reads,
        # under its v1 name and with its v1 meaning.
        out = self._status({
            "/health": (200, "ok"), "/v1/models": (200, _MODELS),
            "/props": (200, _PROPS), "/slots": (200, _SLOTS_CURRENT),
        }, kv=True)
        self.assertTrue(_V1_KEYS <= set(out))
        self.assertEqual(set(out["model"]), {"id", "ctx"})
        self.assertEqual(set(out["slots"]), {"total", "busy"})
        self.assertIn(out["auth"], ("key", "open"))
        self.assertGreaterEqual(out["beast_slot"], 1)
        # ...and the rig still serves clients that old.
        self.assertLessEqual(out["min_client"], out["beast_slot"])

    def test_ctx_shared_true_means_ctx_total_is_ctx(self):
        # --kv-unified: every slot advertises the FULL n_ctx while all slots
        # draw on ONE pool. 6 × 58000 would be a 6x lie.
        many = [{"id": i, "n_ctx": 58000, "is_processing": False}
                for i in range(6)]
        out = self._status({
            "/health": (200, "ok"),
            "/props": (200, {"total_slots": 6, "model_alias": "m"}),
            "/slots": (200, many),
        }, kv=True)
        self.assertTrue(out["capacity"]["ctx_shared"])
        self.assertEqual(out["capacity"]["ctx_total"], out["model"]["ctx"])
        self.assertEqual(out["capacity"]["ctx_total"], 58000)

    def test_ctx_shared_false_multiplies_by_slots(self):
        # --no-kv-unified: llama.cpp splits n_ctx per sequence, so the
        # advertised per-slot ctx really does add up across slots.
        many = [{"id": i, "n_ctx": 58000, "is_processing": False}
                for i in range(6)]
        out = self._status({
            "/health": (200, "ok"),
            "/props": (200, {"total_slots": 6, "model_alias": "m"}),
            "/slots": (200, many),
        }, kv=False)
        self.assertFalse(out["capacity"]["ctx_shared"])
        self.assertEqual(out["capacity"]["ctx_total"], 58000 * 6)

    def test_ctx_shared_unknown_leaves_ctx_total_null(self):
        # Launch path unreadable → report null, never a guessed budget.
        out = self._status({
            "/health": (200, "ok"), "/props": (200, _PROPS),
            "/slots": (200, _SLOTS_CURRENT),
        }, kv=None)
        self.assertIsNone(out["capacity"]["ctx_shared"])
        self.assertIsNone(out["capacity"]["ctx_total"])
        self.assertEqual(out["model"]["ctx"], 212992)   # still reported

    def test_serving_profile_naming(self):
        many = [{"id": i, "n_ctx": 58000, "is_processing": i < 3}
                for i in range(6)]
        multi = self._status({
            "/health": (200, "ok"),
            "/props": (200, {"total_slots": 6, "model_alias": "m"}),
            "/slots": (200, many),
        }, kv=True)
        self.assertEqual(multi["capacity"]["serving_profile"],
                         "batched-multi-slot")
        single = self._status({
            "/health": (200, "ok"), "/props": (200, _PROPS),
            "/slots": (200, _SLOTS_CURRENT),
        }, kv=True)
        self.assertEqual(single["capacity"]["serving_profile"],
                         "mtp-single-slot")
        self.assertEqual(self._status({})["capacity"]["serving_profile"],
                         "unknown")

    def test_legacy_state_field_counts_busy(self):
        out = self._status({
            "/health": (200, "ok"), "/props": (200, _PROPS),
            "/slots": (200, _SLOTS_LEGACY),
        })
        self.assertEqual(out["slots"]["busy"], 1)
        self.assertEqual(out["model"]["ctx"], 65536)

    def test_slots_endpoint_disabled_falls_back_to_props(self):
        # --no-slots on the server: busy must be null, never a guess — but ctx
        # is NOT lost, /props still carries it (default_generation_settings).
        out = self._status({
            "/health": (200, "ok"), "/v1/models": (200, _MODELS),
            "/props": (200, _PROPS), "/slots": (501, "disabled"),
        }, kv=True)
        self.assertIsNone(out["slots"]["busy"])
        self.assertEqual(out["slots"]["total"], 1)     # from /props
        self.assertEqual(out["model"]["ctx"], 212992)  # from /props
        self.assertEqual(out["capacity"]["ctx_total"], 212992)

    def test_metrics_disabled_leaves_queue_deferred_null(self):
        # /metrics answers 501 unless llama-server was started with --metrics.
        out = self._status({
            "/health": (200, "ok"), "/props": (200, _PROPS),
            "/slots": (200, _SLOTS_CURRENT), "/metrics": (501, "disabled"),
        }, kv=True)
        self.assertIsNone(out["capacity"]["queue_deferred"])

    def test_metrics_missing_entirely_leaves_queue_deferred_null(self):
        out = self._status({
            "/health": (200, "ok"), "/props": (200, _PROPS),
            "/slots": (200, _SLOTS_CURRENT),
        }, kv=True)
        self.assertIsNone(out["capacity"]["queue_deferred"])

    def test_queue_deferred_read_from_prometheus_text(self):
        out = self._status({
            "/health": (200, "ok"), "/props": (200, _PROPS),
            "/slots": (200, _SLOTS_CURRENT), "/metrics": (200, _METRICS),
        }, kv=True)
        # requests_deferred, not requests_processing — the queue, not the work.
        self.assertEqual(out["capacity"]["queue_deferred"], 4)

    def test_model_down_still_answers(self):
        out = self._status({})
        self.assertFalse(out["healthy"])
        self.assertIsNone(out["model"]["id"])
        self.assertIsNone(out["slots"]["total"])
        self.assertIsNone(out["capacity"]["ctx_total"])
        self.assertEqual(out["beast_slot"], 2)

    def test_no_prompt_or_key_material_in_output(self):
        os.environ["OPENBEAST_API_KEY"] = "super-sekrit"
        try:
            # _API_KEY is read at import; simulate a keyed dashboard.
            saved = dashboard._API_KEY
            dashboard._API_KEY = "super-sekrit"
            props = dict(_PROPS, chat_template="You are {{ system }}",
                         default_generation_settings={
                             "n_ctx": 212992,
                             "params": {"temperature": 0.7, "seed": 42}})
            slots = [dict(_SLOTS_CURRENT[0], prompt="the user's secret prompt",
                          params={"temperature": 0.7})]
            out = self._status({
                "/health": (200, "ok"), "/props": (200, props),
                "/slots": (200, slots), "/metrics": (200, _METRICS),
            }, kv=True)
            self.assertEqual(out["auth"], "key")
            blob = json.dumps(out)
            for leak in ("super-sekrit", "secret prompt", "temperature",
                         "chat_template", "seed"):
                self.assertNotIn(leak, blob)
        finally:
            dashboard._API_KEY = saved
            del os.environ["OPENBEAST_API_KEY"]

    def test_slot_count_agnostic(self):
        # Future multi-slot serving profile: same shape, bigger numbers.
        many = [{"id": i, "n_ctx": 58000,
                 "is_processing": i < 3} for i in range(6)]
        out = self._status({
            "/health": (200, "ok"),
            "/props": (200, {"total_slots": 6, "model_alias": "m"}),
            "/slots": (200, many),
        })
        self.assertEqual(out["slots"], {"total": 6, "busy": 3})


# vLLM's Prometheus text: per-engine labels, several samples per gauge
# (vllm/v1/metrics/loggers.py). kv_cache_usage_perc is a 0..1 FRACTION.
_VLLM_METRICS = (
    "# HELP vllm:num_requests_running Number of requests in model execution batches.\n"
    "# TYPE vllm:num_requests_running gauge\n"
    'vllm:num_requests_running{engine="0",model_name="q"} 3.0\n'
    'vllm:num_requests_running{engine="1",model_name="q"} 2.0\n'
    'vllm:num_requests_waiting{engine="0",model_name="q"} 4.0\n'
    'vllm:num_requests_waiting_by_reason{engine="0",model_name="q",reason="capacity"} 99.0\n'
    'vllm:kv_cache_usage_perc{engine="0",model_name="q"} 0.25\n'
    'vllm:kv_cache_usage_perc{engine="1",model_name="q"} 0.5\n')
_VLLM_MODELS = {"object": "list", "data": [
    {"id": "Qwen3.8 27B NVFP4 (vLLM TP2)", "object": "model",
     "max_model_len": 262144}]}


class TestRemoteBackends(unittest.TestCase):
    """INFERENCE_BACKEND=vllm|tensorfold (docs/DGX_SPARK_PLAN.md): the same
    contract version, plus `backend` and `capacity.kv_usage`, which appear
    ONLY there — a llama rig's answer is unchanged (pinned by
    TestSlotContract.test_full_healthy_contract's exact key sets)."""

    def setUp(self):
        self._saved = (dashboard._get, dashboard._API_KEY, dashboard._kv_unified,
                       dashboard._BACKEND, dashboard._INFER, dashboard._EDGE_GATE,
                       os.environ.get("OPENBEAST_INFERENCE_SLOTS"))
        dashboard._API_KEY = ""
        dashboard._EDGE_GATE = False
        dashboard._INFER = "http://10.0.0.5:8000"
        # A llama-only probe must never be consulted on these branches.
        dashboard._kv_unified = lambda: self.fail("_kv_unified read on a remote backend")

    def tearDown(self):
        (dashboard._get, dashboard._API_KEY, dashboard._kv_unified,
         dashboard._BACKEND, dashboard._INFER, dashboard._EDGE_GATE, slots) = self._saved
        if slots is None:
            os.environ.pop("OPENBEAST_INFERENCE_SLOTS", None)
        else:
            os.environ["OPENBEAST_INFERENCE_SLOTS"] = slots

    def _status(self, backend, responses, slots=None):
        dashboard._BACKEND = backend
        if slots is None:
            os.environ.pop("OPENBEAST_INFERENCE_SLOTS", None)
        else:
            os.environ["OPENBEAST_INFERENCE_SLOTS"] = str(slots)
        seen = []

        def get(url, timeout=2, auth=False):
            seen.append(url)
            return _fake_get(responses)(url, timeout, auth)
        dashboard._get = get
        out = dashboard.slot_status()
        self.seen = seen
        return out

    def test_vllm_maps_models_and_metrics(self):
        out = self._status("vllm", {
            "8000/health": (200, ""), "/v1/models": (200, _VLLM_MODELS),
            "8000/metrics": (200, _VLLM_METRICS),
        }, slots=8)
        self.assertEqual(out["beast_slot"], 2)
        self.assertEqual(out["min_client"], 1)
        self.assertTrue(out["healthy"])           # an EMPTY 200 is healthy
        self.assertEqual(out["backend"], "vllm")
        self.assertEqual(out["model"], {"id": "Qwen3.8 27B NVFP4 (vLLM TP2)",
                                        "ctx": 262144})
        self.assertEqual(out["slots"], {"total": 8, "busy": 5})   # summed engines
        self.assertEqual(out["capacity"], {
            "ctx_shared": True, "ctx_total": None, "queue_deferred": 4,
            "kv_usage": 0.5, "serving_profile": "batched-multi-slot"})
        self.assertTrue(out["services"]["model"])
        self.assertEqual(
            set(out), {"beast_slot", "min_client", "healthy", "backend", "model",
                       "slots", "capacity", "services", "auth"})
        # Every probe went to INFERENCE_URL, none to llama's /props or /slots.
        self.assertFalse(any(u.endswith(("/props", "/slots")) for u in self.seen))
        self.assertTrue(all(u.startswith("http://10.0.0.5:8000")
                            for u in self.seen if "8000" in u))

    def test_vllm_is_v1_compatible(self):
        out = self._status("vllm", {"8000/health": (200, ""),
                                    "/v1/models": (200, _VLLM_MODELS)})
        self.assertTrue(_V1_KEYS <= set(out))
        self.assertEqual(set(out["model"]), {"id", "ctx"})
        self.assertEqual(set(out["slots"]), {"total", "busy"})

    def test_vllm_without_metrics_or_slots_is_null_not_zero(self):
        out = self._status("vllm", {"8000/health": (200, ""),
                                    "/v1/models": (200, _VLLM_MODELS),
                                    "8000/metrics": (404, "")})
        self.assertIsNone(out["slots"]["total"])
        self.assertIsNone(out["slots"]["busy"])
        self.assertIsNone(out["capacity"]["queue_deferred"])
        self.assertIsNone(out["capacity"]["kv_usage"])
        self.assertEqual(out["capacity"]["serving_profile"], "unknown")

    def test_vllm_prefix_sharing_metric_is_not_counted(self):
        # num_requests_waiting_by_reason shares the waiting gauge's prefix.
        body = 'vllm:num_requests_waiting_by_reason{reason="capacity"} 7.0\n'
        out = self._status("vllm", {"8000/health": (200, ""),
                                    "8000/metrics": (200, body)})
        self.assertIsNone(out["capacity"]["queue_deferred"])

    def test_vllm_pre_v1_kv_metric_name(self):
        body = 'vllm:gpu_cache_usage_perc{model_name="q"} 0.75\n'
        out = self._status("vllm", {"8000/health": (200, ""),
                                    "8000/metrics": (200, body)})
        self.assertEqual(out["capacity"]["kv_usage"], 0.75)

    def test_vllm_down(self):
        out = self._status("vllm", {"8000/health": (503, "")}, slots=4)
        self.assertFalse(out["healthy"])
        self.assertFalse(out["services"]["model"])
        self.assertIsNone(out["model"]["id"])

    def test_wrong_url_404_is_not_up(self):
        out = self._status("vllm", {"8000/health": (404, "not found")})
        self.assertFalse(out["healthy"])
        self.assertFalse(out["services"]["model"])

    def test_tensorfold_reports_health_model_and_conf_slots(self):
        out = self._status("tensorfold", {
            "8000/health": (200, {"ok": True}),
            "/v1/models": (200, {"object": "list", "data": [{"id": "local-model"}]}),
            "8000/metrics": (200, _VLLM_METRICS),   # must NOT be read
        }, slots=1)
        self.assertTrue(out["healthy"])
        self.assertEqual(out["backend"], "tensorfold")
        self.assertEqual(out["model"], {"id": "local-model", "ctx": None})
        self.assertEqual(out["slots"], {"total": 1, "busy": None})
        self.assertEqual(out["capacity"], {
            "ctx_shared": None, "ctx_total": None, "queue_deferred": None,
            "kv_usage": None, "serving_profile": "single-slot"})
        self.assertFalse(any(u.endswith("/metrics") for u in self.seen))

    def test_bad_slots_value_is_ignored(self):
        out = self._status("tensorfold", {"8000/health": (200, {"ok": True})},
                           slots="eight")
        self.assertIsNone(out["slots"]["total"])

    def test_key_never_sent_to_tensorfold(self):
        # The real _get, with urlopen captured: TensorFold has no auth, so
        # the key must not ride along; vLLM (--api-key) must get it.
        import urllib.request as ur
        dashboard._get = self._saved[0]
        dashboard._API_KEY = "k-secret"
        seen = []

        class R:
            status = 200

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        real = ur.urlopen
        ur.urlopen = lambda req, timeout=2: (seen.append(req.get_header("Authorization")), R())[1]
        try:
            for backend, want in (("tensorfold", None), ("vllm", "Bearer k-secret")):
                seen.clear()
                dashboard._BACKEND = backend
                dashboard._get("http://10.0.0.5:8000/v1/models", auth=True)
                self.assertEqual(seen, [want], backend)
        finally:
            ur.urlopen = real

    def test_llama_backend_has_no_backend_field(self):
        # The llama answer stays byte-identical: no `backend`, no kv_usage.
        dashboard._kv_unified = lambda: True
        out = self._status("llama", {"/health": (200, "ok"),
                                     "/props": (200, _PROPS),
                                     "/slots": (200, _SLOTS_CURRENT)})
        self.assertNotIn("backend", out)
        self.assertNotIn("kv_usage", out["capacity"])


class TestKvUnifiedDerivation(unittest.TestCase):
    """capacity.ctx_shared comes from the recorded launch path, not a guess."""

    def _repo(self, serve_script, model_src, serve_src="exec llama-server \\\n"
                                                       "  --kv-unified \\\n"):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, ".run"))
        os.makedirs(os.path.join(d, "scripts"))
        if serve_script is not None:
            with open(os.path.join(d, ".run", "serve-script"), "w") as fh:
                fh.write(serve_script + "\n")
        if model_src is not None:
            with open(os.path.join(d, "scripts", serve_script), "w") as fh:
                fh.write(model_src)
        with open(os.path.join(d, "scripts", "serve.sh"), "w") as fh:
            fh.write(serve_src)
        return d

    def setUp(self):
        self._real_repo = dashboard.REPO_DIR

    def tearDown(self):
        dashboard.REPO_DIR = self._real_repo

    def _kv(self, **kw):
        dashboard.REPO_DIR = self._repo(**kw)
        return dashboard._kv_unified()

    def test_serve_sh_flag_detected(self):
        self.assertTrue(self._kv(serve_script="serve-x.sh",
                                 model_src='exec "$SCRIPT_DIR/serve.sh" -c 1\n'))

    def test_model_script_can_opt_out(self):
        # --no-kv-unified lands after serve.sh's flag; llama.cpp takes the last.
        self.assertFalse(self._kv(
            serve_script="serve-x.sh",
            model_src='exec "$SCRIPT_DIR/serve.sh" --no-kv-unified\n'))

    def test_commented_flag_does_not_count(self):
        # serve.sh documents the flag in prose; a deleted flag that is still
        # explained must not read as "in use".
        self.assertFalse(self._kv(
            serve_script="serve-x.sh", model_src="exec serve.sh\n",
            serve_src="# with --kv-unified the KV cache is shared\n"
                      "exec llama-server -c 1\n"))

    def test_unrecorded_serve_script_is_null(self):
        self.assertIsNone(self._kv(serve_script=None, model_src=None))

    def test_missing_scripts_are_null(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, ".run"))
        with open(os.path.join(d, ".run", "serve-script"), "w") as fh:
            fh.write("serve-ghost.sh\n")
        dashboard.REPO_DIR = d
        self.assertIsNone(dashboard._kv_unified())

    def test_path_in_serve_script_record_is_rejected(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, ".run"))
        with open(os.path.join(d, ".run", "serve-script"), "w") as fh:
            fh.write("../../etc/passwd\n")
        dashboard.REPO_DIR = d
        self.assertIsNone(dashboard._kv_unified())

    def test_live_repo_derivation_is_boolean_or_null(self):
        # Against the real checkout: whatever it answers must be one of the
        # three honest values — never a crash, never a string.
        self.assertIn(dashboard._kv_unified(), (True, False, None))


class TestEdgeAuthMode(unittest.TestCase):
    """`auth: anon` promises a keyless client it will be served. beast-gate
    honours ALLOW_ANON only while NO device is enrolled — past that a keyless
    caller gets 401 — so the contract must say "device" there too."""

    def setUp(self):
        self._saved = (dashboard.REPO_DIR, dashboard._EDGE_GATE,
                       dashboard._EDGE_ANON, dashboard._API_KEY,
                       dashboard._get, dashboard._kv_unified, dashboard._BACKEND)
        dashboard._BACKEND = "llama"
        dashboard._EDGE_GATE = True
        dashboard._API_KEY = ""
        dashboard._get = _fake_get({"/health": (200, "ok")})
        dashboard._kv_unified = lambda: None
        self.repo = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.repo, ".run"))
        dashboard.REPO_DIR = self.repo

    def tearDown(self):
        (dashboard.REPO_DIR, dashboard._EDGE_GATE, dashboard._EDGE_ANON,
         dashboard._API_KEY, dashboard._get, dashboard._kv_unified,
         dashboard._BACKEND) = self._saved

    def _auth(self, anon, registry=None):
        dashboard._EDGE_ANON = anon
        if registry is not None:
            with open(os.path.join(self.repo, ".run", "clients.json"), "w") as f:
                f.write(registry if isinstance(registry, str)
                        else json.dumps(registry))
        return dashboard.slot_status()["auth"]

    def test_anon_with_enrolled_device_is_device(self):
        reg = {"version": 1, "devices": [{"id": "laptop",
                                          "key_sha256": "ab" * 32}]}
        self.assertEqual(self._auth(True, reg), "device")

    def test_anon_with_only_revoked_devices_is_still_device(self):
        # The gate counts revoked entries as "configured" (a revoked key is
        # refused, it does not reopen the anon door).
        reg = {"version": 1, "devices": [{"id": "old", "key_sha256": "cd" * 32,
                                          "revoked_at": "2026-09-01T00:00:00Z"}]}
        self.assertEqual(self._auth(True, reg), "device")

    def test_corrupt_registry_does_not_claim_anon(self):
        self.assertEqual(self._auth(True, "{ half-written"), "device")

    def test_anon_holds_while_registry_is_empty(self):
        # Negative controls: no file, or a file with no keyed device — the
        # gate really does serve keyless callers as "anon" then.
        self.assertEqual(self._auth(True), "anon")
        self.assertEqual(self._auth(True, {"version": 1, "devices": []}), "anon")

    def test_gate_without_anon_is_device(self):
        self.assertEqual(self._auth(False), "device")


if __name__ == "__main__":
    unittest.main()
