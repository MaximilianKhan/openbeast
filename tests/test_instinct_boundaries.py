#!/usr/bin/env python3
"""Invariants that keep instinct a decision plane and nothing more
(plan §2.3). Each has a negative control so removing the guard fails it.

I1 rank ids ⊆ input ids (and deterministic eligibility runs first — router/hydra tests)
I2 no authn/authz/RBAC/SSRF/gate/eval-grading path imports instinct
I4 only labels in policy.act can act
I6 an engine without probabilities never enforces
I7 engine traffic never routes through hydra or beast-gate
I8 era-locked files unchanged
(I3 -> test_instinct_client.py + test_router_instinct.py; I5 -> test_instinct_server.py)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

import pytest

import _instinct_helpers as H
from instinct import calibrate as C
from instinct import core
from instinct.config import load_config
from instinct.engines import rules as R
from instinct.service import Instinct
from instinct.spec import decision_hash, load_spec

REPO = H.REPO
# Security controls, the edge, the eval grader and the era-locked runtime.
NEVER_IMPORT = ["agents/edge.py", "agents/openapi_tools.py", "agents/hostpolicy.py",
                "agents/tools.py", "agents/runner.py", "agents/mcp_server.py",
                "agents/sessions.py", "agents/chat_server.py", "agents/artifact_server.py",
                "agents/artifact.py", "evals/run_eval.py", "evals/benchmark_all.py",
                "evals/scoring.py", "evals/cache.py"]
ERA_LOCKED = ["system-prompt.md", "system-prompt-tools.md", "opencode.json",
              "agents/runner.py", "agents/tools.py", "evals/SUITE_VERSION"]
_IMPORT = re.compile(r"^\s*(?:from\s+(?:agents\.)?instinct\b|import\s+(?:agents\.)?instinct\b)"
                     r"|importlib\.import_module\(\s*['\"](?:agents\.)?instinct",
                     re.MULTILINE)


def imports_instinct(path) -> bool:
    return bool(_IMPORT.search(path.read_text(errors="replace")))


@pytest.mark.parametrize("rel", NEVER_IMPORT)
def test_i2_security_and_eval_paths_never_import_instinct(rel):
    p = REPO / rel
    assert p.exists(), rel
    assert not imports_instinct(p), f"{rel} imports instinct (I2)"


def test_i2_grep_finds_a_planted_import(tmp_path):
    """Negative control: the grep really detects an import."""
    for planted in ("from instinct import client\n", "import agents.instinct.client\n",
                    "    from agents.instinct.client import decide\n",
                    "x = importlib.import_module('instinct.client')\n"):
        copy = tmp_path / "edge.py"
        shutil.copy(REPO / "agents" / "edge.py", copy)
        copy.write_text(copy.read_text() + planted)
        assert imports_instinct(copy), planted


def test_router_imports_only_the_client_layer():
    src = (REPO / "agents" / "router.py").read_text()
    for m in _IMPORT.finditer(src):
        line = src[m.start():src.index("\n", m.start())]
        assert re.search(r"instinct(\.|\s+import\s+)(client|routerhook)\b", line), line


def test_client_module_pulls_in_no_engine_code():
    code = ("import sys; sys.path.insert(0, %r); import instinct.client, instinct.routerhook; "
            "bad = [m for m in sys.modules if m.startswith('instinct.') and m not in "
            "('instinct.client', 'instinct.routerhook')]; print(bad)") % str(REPO / "agents")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_evals_decisions_is_its_own_namespace():
    """run_eval never reaches the decision harness, and the harness never
    touches SUITE_VERSION or the v4 cache."""
    src = (REPO / "evals" / "run_eval.py").read_text()
    assert "decisions" not in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M)
    for f in ("run.py", "metrics.py", "loadgen.py"):
        text = (REPO / "evals" / "decisions" / f).read_text()
        assert "SUITE_VERSION" not in text.replace("never touches SUITE_VERSION", "") \
            and "import cache" not in text


def test_rules_hints_equal_router_hints():
    sys.path.insert(0, str(REPO / "agents"))
    import router
    assert R.ROUTER_HINTS.pattern == router._HINTS.pattern
    assert R.ROUTER_HINTS.flags == router._HINTS.flags


# --- I4 --------------------------------------------------------------------------

def test_i4_label_outside_act_never_acts_even_at_certainty():
    spec = load_spec(H.DECISIONS / "router.spawn_intent.toml")
    ans = core.build_answer(spec, q={"spawn": 1.0, "inline": 0.0}, label_mass=1.0,
                            calibrated=True, temperature=1.0)
    assert ans.label == "spawn" and ans.confidence["p_top"] == 1.0
    assert core.decide_action(spec, ans, thresholds={"spawn": 0.0, "inline": 0.0}) == (
        "abstain", "not_act_label")
    ans2 = core.build_answer(spec, q={"spawn": 0.0, "inline": 1.0}, label_mass=1.0,
                             calibrated=True, temperature=1.0)
    assert core.decide_action(spec, ans2)[0] == "act"            # control


def test_i4_through_the_service(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path, thresholds={"inline": 0.0, "spawn": 0.0},
                               chain='["linear"]')

    async def go():
        inst = Instinct(load_config(cfgp, env={}), repo_root=tmp_path)
        await inst.start()
        r = await inst.decide({"decision": "router.spawn_intent", "inputs": {
            "user_turn": "spawn a background agent to refactor the parser and report back"}})
        await inst.aclose()
        return r
    r = H.run(go())
    assert r["answer"]["label"] == "spawn" and r["mode"] == "enforce"
    assert r["action"] == "abstain" and r["fallback"]["reason"] == "not_act_label"
    assert r["enforce"] is False


# --- I6 --------------------------------------------------------------------------

def test_i6_rules_never_enforce_even_with_forged_records(tmp_path):
    text = H.spec_text("router.spawn_intent").replace(
        'mode             = "shadow"', 'mode             = "enforce"').replace(
        'chain = ["linear", "rig-cpu", "rules"]', 'chain = ["rules"]')
    cfgp = H.write_config(tmp_path, {}, extra_decisions={"router.spawn_intent": text})
    cfg = load_config(cfgp, env={})
    spec = load_spec(tmp_path / "decisions" / "router.spawn_intent.toml")
    h = decision_hash(spec, cfg.engines["rules"].hash_identity(), None)
    cp = C.calib_path(cfg.records_dir, spec.id, h)
    C.write_record(cp, {"decision_hash": h, "T": 1.0, "thresholds": {"inline": 0.0}})
    C.write_record(C.gate_path(cfg.records_dir, spec.id, h),
                   {"decision_hash": h, "passed": True, "calib_sha256": C.file_sha256(cp)})

    async def go():
        inst = Instinct(cfg, repo_root=tmp_path)
        await inst.start()
        r = await inst.decide({"decision": "router.spawn_intent",
                               "inputs": {"user_turn": "fix the login page"}})
        await inst.aclose()
        return r
    r = H.run(go())
    assert r["answer"]["label"] == "inline" and r["answer"]["calibrated"] is False
    assert r["enforce"] is False and r["mode"] == "shadow"


# --- I1 --------------------------------------------------------------------------

def test_i1_rank_items_are_the_input_ids_in_input_order(tmp_path):
    text = H.spec_text("hydra.pool_fit").replace('mode           = "off"',
                                                 'mode           = "shadow"')
    with H.stub_server() as (url, _):
        cfgp = H.write_config(tmp_path, {"sg": H.sglang_binding(url, exec="sis")},
                              extra_decisions={"hydra.pool_fit": text.replace(
                                  '"rig-sglang"', '"sg"')})

        async def go():
            inst = Instinct(load_config(cfgp, env={}))
            await inst.start()
            items = [{"id": f"p{i}", "text": f"pool {i}: fast GPU"} for i in (3, 1, 2)]
            r = await inst.decide({"decision": "hydra.pool_fit",
                                   "inputs": {"prompt_head": "fix my code"}, "items": items})
            await inst.aclose()
            return r, items
        r, items = H.run(go())
    assert [i["id"] for i in r["items"]] == [i["id"] for i in items]
    assert all(set(i) == {"id", "p", "label_mass", "action"} for i in r["items"])


def test_i1_duplicate_or_excess_items_rejected(tmp_path):
    text = H.spec_text("hydra.pool_fit").replace('mode           = "off"',
                                                 'mode           = "shadow"')
    cfgp = H.write_config(tmp_path, {}, extra_decisions={"hydra.pool_fit": text.replace(
        '["rig-sglang", "rules"]', '["rules"]')})

    async def go(items):
        inst = Instinct(load_config(cfgp, env={}))
        await inst.start()
        try:
            return await inst.decide({"decision": "hydra.pool_fit",
                                      "inputs": {"prompt_head": "x"}, "items": items})
        finally:
            await inst.aclose()
    from instinct.render import InputError
    with pytest.raises(InputError):
        H.run(go([{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]))
    with pytest.raises(InputError):
        H.run(go([{"id": str(i), "text": "x"} for i in range(17)]))
    assert H.run(go([{"id": "a", "text": "x"}]))["items"][0]["id"] == "a"   # control


# --- I8 --------------------------------------------------------------------------

def _git(*args, repo=None):
    return subprocess.run(["git", "-C", str(repo or REPO), *args], capture_output=True, text=True,
                          timeout=30)


def _i8_offending(repo, env) -> list[str] | None:
    """Era-locked files that an instinct commit on this branch touches.

    The base is OPENBEAST_I8_BASE when set (CI exports the PR base, fetched
    deep enough for a merge-base), else origin/main, else main. None means
    no base resolved: fine on a local detached checkout, but in CI (or with
    an explicit base that does not resolve) it FAILS. CI's checkout is
    shallow and has neither ref, so this check used to pass vacuously."""
    want = (env.get("OPENBEAST_I8_BASE") or "").strip()
    if want == "none":
        return None
    for base in ([want] if want else ["origin/main", "main"]):
        mb = _git("merge-base", "HEAD", base, repo=repo)
        if mb.returncode != 0:
            continue
        rng = f"{mb.stdout.strip()}..HEAD"
        changed = _git("diff", "--name-only", mb.stdout.strip(), "HEAD", "--", *ERA_LOCKED,
                       repo=repo).stdout.split()
        instinct_commits = set(_git("log", "--format=%H", rng, "--", "agents/instinct",
                                    repo=repo).stdout.split())
        # only meaningful on a branch that carries instinct work
        return [f for f in changed
                if set(_git("log", "--format=%H", rng, "--", f, repo=repo).stdout.split())
                & instinct_commits]
    if want:
        raise AssertionError(f"OPENBEAST_I8_BASE={want!r} has no merge-base with HEAD "
                             "(fetch more history, or set it to 'none')")
    if env.get("CI") == "true":
        raise AssertionError("I8 in CI needs a branch base: export OPENBEAST_I8_BASE "
                             "(the PR base sha, fetched) or OPENBEAST_I8_BASE=none")
    return None


def test_i8_era_locked_files_untouched():
    if _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout")
    dirty = _git("status", "--porcelain", "--", *ERA_LOCKED).stdout.strip()
    assert dirty == "", f"era-locked files modified in the working tree: {dirty}"
    offending = _i8_offending(REPO, os.environ)
    assert not offending, f"an instinct commit touches era-locked {offending}"


def test_i8_guard_needs_a_base_in_ci(tmp_path):
    """Built case: a shallow-CI-like repo (no origin/main, no main) whose
    branch has an instinct commit that edits agents/tools.py."""
    r = tmp_path / "r"
    r.mkdir()

    def g(*a):
        out = _git(*a, repo=r)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()
    g("init", "-q", "-b", "trunk")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("config", "commit.gpgsign", "false")
    (r / "agents" / "instinct").mkdir(parents=True)
    (r / "agents" / "tools.py").write_text("x = 1\n")
    (r / "agents" / "instinct" / "__init__.py").write_text("")
    g("add", "-A")
    g("commit", "-qm", "base")
    base = g("rev-parse", "HEAD")
    (r / "agents" / "tools.py").write_text("x = 2\n")
    (r / "agents" / "instinct" / "__init__.py").write_text("# touch\n")
    g("commit", "-qam", "instinct: touch tools")
    assert _i8_offending(r, {}) is None                       # local, no base: nothing to diff
    with pytest.raises(AssertionError, match="needs a branch base"):
        _i8_offending(r, {"CI": "true"})                      # CI never passes vacuously
    assert _i8_offending(r, {"CI": "true", "OPENBEAST_I8_BASE": base}) == ["agents/tools.py"]
    with pytest.raises(AssertionError, match="no merge-base"):
        _i8_offending(r, {"OPENBEAST_I8_BASE": "0" * 40})
    assert _i8_offending(r, {"CI": "true", "OPENBEAST_I8_BASE": "none"}) is None
    g("branch", "main", base)                                  # control: the local fallback
    assert _i8_offending(r, {}) == ["agents/tools.py"]
