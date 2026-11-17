"""Tier-3 zig-0.16 awareness pack (docs/LANG_AWARENESS_PLAN.md §5) tests.

The pins that matter:
  1. Budget: the committed pack is ≤ 2,000 tokens (4-chars/token estimate)
     and carries the "Language notes: zig 0.16" delimiter.
  2. Generator determinism: `gen_zig_pack.py --check` regenerates section
     (2) from the installed stdlib and must reproduce the committed bytes
     (skips without zig). The stamped sha256 always matches the digest.
  3. Curated entries are machine-verified: every entry in
     tests/fixtures/zig016/MANIFEST.json has fixtures, every
     `pack_must_contain` string is in section (1), every curated bullet
     hits ≥1 manifest entry — and the fixtures BEHAVE (old fails, new
     compiles) under the real zig (skips without zig).
  4. Injection: BEAST_PACKS=1 (alias OPENBEAST_PACKS=1, CLI --packs) →
     zig units get `--context-file <pack>`; non-zig units get nothing
     when the host pack is empty, the host pack alone when it is not (zig
     units then get one file: zig pack, then host pack); the runner puts
     the file in a delimited system-prompt block.
  5. Cache era: `pack1-<sha8>` component present and task-scoped;
     provenance records harness.packs = {zig: sha8}; drift-abort on a
     tampered pack.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "evals"))

PACK = ROOT / "agents" / "packs" / "zig-0.16.md"          # v1: measured, production
PACK_V2 = ROOT / "agents" / "packs" / "zig-0.16-v2.md"    # v2: eval-only until measured
BOTH = [pytest.param(PACK, id="v1"), pytest.param(PACK_V2, id="v2")]
GEN = ROOT / "agents" / "packs" / "gen_zig_pack.py"
FIXTURES = ROOT / "tests" / "fixtures" / "zig016"
MANIFEST = FIXTURES / "MANIFEST.json"
DELIM = "Language notes: zig 0.16"
HAS_ZIG = shutil.which("zig") is not None


# run_eval() and its CLI write the arm flags straight into os.environ
# (`os.environ["OPENBEAST_PACKS"] = "1" if packs_on else "0"`), which
# monkeypatch cannot undo for a variable it never set — so this file used to
# leave them exported for every test collected after it (test_eval_env_era.py
# found out). Each test now gets them back exactly as it found them.
_HARNESS_ENV = ("BEAST_PACKS", "OPENBEAST_PACKS", "BEAST_ASSIST",
                "OPENBEAST_DIAGNOSTICS", "OPENBEAST_DIAG_TIMING_LOG",
                "BEAST_ESCALATE", "OPENBEAST_ESCALATE", "OPENBEAST_LANG_IN_EVAL")
_ENV_AT_START: dict = {}


@pytest.fixture(autouse=True)
def _restore_harness_env():
    before = {k: os.environ.get(k) for k in _HARNESS_ENV}
    # What this file's FIRST test found — not what import found: collection
    # imports every file before any test runs, so by the time these run an
    # earlier file may have changed the environment, and that is not ours.
    _ENV_AT_START.setdefault("env", dict(before))
    yield
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


#: The host pack (agents/lang/host_pack.py) is probed from the machine
#: running the tests, so every test here gets a stub: EMPTY by default (the
#: Linux-with-GNU case — the language-pack wiring below is then exactly what
#: it was before the host pack), or the fixed text a test asks for.
HOST_TEXT = ("=== Host notes: Darwin tool host (probed when this run started) ===\n"
             "- no `nproc` and no /proc: CPU count is `sysctl -n hw.ncpu`.\n")


@pytest.fixture(autouse=True)
def _host_pack_stub(monkeypatch, tmp_path):
    from lang import host_pack as HP
    monkeypatch.setenv("OPENBEAST_HOST_PACK_DIR", str(tmp_path / "host-pack"))
    monkeypatch.delenv("OPENBEAST_PACK_VARIANT", raising=False)
    state = {"text": ""}
    monkeypatch.setattr(HP, "build", lambda host=None: HP.HostPack(
        "Darwin", state["text"], [HP.Fact("nproc_missing", "x", "probe")] if state["text"] else []))
    yield state


def _gen():
    spec = importlib.util.spec_from_file_location("gen_zig_pack", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_run_eval():
    for m in ("cache", "run_eval"):
        sys.modules.pop(m, None)
    importlib.import_module("cache")
    return importlib.import_module("run_eval")


def _section1(text: str) -> str:
    return _gen().split_curated(text)


# --- 1. budget + delimiter --------------------------------------------------

@pytest.mark.parametrize("pack", BOTH)
def test_pack_exists_within_budget_with_delimiter(pack):
    gen = _gen()
    text = pack.read_text()
    assert text.startswith("=== " + DELIM), "pack must open with the delimiter line"
    assert gen.token_estimate(text) <= gen.PACK_TOKEN_BUDGET
    assert "(1) CURATED" in text and gen.SECTION2_HEADER_PREFIX in text
    assert "machine-verified on zig 0.16.0 on 2026-09-11" in text  # curated = dated, not pinned


@pytest.mark.parametrize("pack", BOTH)
def test_pack_header_sha_matches_digest(pack):
    h = _gen().parse_header(pack.read_text())
    assert h["sha"] == h["digest_sha"]
    assert h["version"] == "0.16.0"
    assert h["digest"].count("\n- ") + 1 >= h["lines"]  # legend + N lines


# --- 2. generator determinism ----------------------------------------------

@pytest.mark.skipif(not HAS_ZIG, reason="zig not installed")
@pytest.mark.parametrize("pack", BOTH)
def test_generator_is_deterministic_and_matches_committed_pack(pack):
    gen = _gen()
    lib = gen.zig_lib_dir()
    version = gen.zig_version()
    if version != "0.16.0":
        pytest.skip(f"pack is for zig 0.16.0, installed {version}")
    curated = _section1(pack.read_text())
    a = gen.compose(curated, lib, version)
    b = gen.compose(curated, lib, version)
    assert a == b, "two regenerations differ — generator is not deterministic"
    assert a == pack.read_text(), "committed pack drifted from regeneration (run gen_zig_pack.py)"
    r = subprocess.run([sys.executable, str(GEN), "--check", "--pack", str(pack)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


# --- 3. curated entries ↔ fixtures ------------------------------------------

def _manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def test_v1_is_the_measured_pack_and_v2_is_a_variant():
    """Production serves the A/B-measured bytes (pack_context.MEASURED_SHA256);
    v2 exists only as an eval variant (OPENBEAST_PACK_VARIANT=v2) until its
    own A/B moves the pin."""
    from lang import pack_context as PC
    assert hashlib.sha256(PACK.read_bytes()).hexdigest() == PC.MEASURED_SHA256["zig"]
    assert PC.PACK_FILES == {"zig": "zig-0.16.md"}
    assert PC.PACK_VARIANTS["v1"] == PC.PACK_FILES
    assert PC.PACK_VARIANTS["v2"] == {"zig": "zig-0.16-v2.md"}
    assert PACK_V2.read_bytes() != PACK.read_bytes()


def test_v1_curated_bullets_each_hit_a_manifest_entry():
    musts = [s for e in _manifest()["entries"] for s in e["pack_must_contain"]]
    for ln in _section1(PACK.read_text()).splitlines():
        if ln.startswith("- "):
            assert any(s in ln for s in musts), f"v1 bullet has no verifying fixture: {ln[:80]}"


def test_manifest_fixtures_exist_and_cover_curated_section():
    """Against v2, the superset: every manifest entry (v1's and the five
    2026-11-16 ones) must be stated in its curated section."""
    man = _manifest()
    sec1 = _section1(PACK_V2.read_text())
    assert man["entries"], "empty manifest"
    all_musts = []
    for e in man["entries"]:
        assert e["new"], f"{e['entry']}: needs at least one NEW fixture"
        for fn in e["old"] + e["new"]:
            assert (FIXTURES / fn).exists(), f"missing fixture {fn}"
        for s in e["pack_must_contain"]:
            assert s in sec1, f"{e['entry']}: curated section lacks {s!r}"
            all_musts.append(s)
    bullets = [ln for ln in sec1.splitlines() if ln.startswith("- ")]
    assert len(bullets) >= 15
    for ln in bullets:
        assert any(s in ln for s in all_musts), f"curated bullet has no verifying fixture: {ln[:80]}"
    # the manifest itself must not be stale vs the fixture dir
    on_disk = {p.name for p in FIXTURES.glob("*.zig")}
    listed = {fn for e in man["entries"] for fn in e["old"] + e["new"]}
    assert on_disk == listed


def _compile(path: Path, cache: Path) -> bool:
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(cache / "g"), ZIG_LOCAL_CACHE_DIR=str(cache / "l"))
    r = subprocess.run(["zig", "build-exe", "-fno-emit-bin", str(path)],
                       capture_output=True, text=True, env=env, cwd=str(cache), timeout=300)
    return r.returncode == 0


@pytest.mark.skipif(not HAS_ZIG, reason="zig not installed")
def test_fixtures_behave_old_fails_new_compiles(tmp_path):
    files = sorted(FIXTURES.glob("*.zig"))
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        ok = dict(zip(files, pool.map(lambda p: _compile(p, tmp_path), files)))
    wrong = [p.name for p, compiled in ok.items()
             if (".old." in p.name and compiled) or (".new." in p.name and not compiled)]
    assert not wrong, f"fixtures not behaving as named: {wrong}"


def _compile_err(path: Path, cache: Path) -> str:
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(cache / "g"), ZIG_LOCAL_CACHE_DIR=str(cache / "l"))
    r = subprocess.run(["zig", "build-exe", "-fno-emit-bin", str(path)],
                       capture_output=True, text=True, env=env, cwd=str(cache), timeout=300)
    return r.stderr


@pytest.mark.skipif(not HAS_ZIG, reason="zig not installed")
def test_old_fixtures_fail_with_the_expected_error(tmp_path):
    """Pack v2 entries (GLM-5.3-Flash v5 taxonomy, 2026-11-16) pin the
    error each OLD fixture must produce: an OLD file that fails for some
    unrelated reason (a typo, a missing import) proves nothing about the
    claim it is filed under."""
    expect = {fn: want for e in _manifest()["entries"] for fn, want in (e.get("expect") or {}).items()}
    assert len(expect) >= 14, "pack v2 entries lost their expected errors"
    for fn in expect:
        assert ".old." in fn and any(fn in e["old"] for e in _manifest()["entries"]), fn
    files = sorted(expect)
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        out = dict(zip(files, pool.map(lambda f: _compile_err(FIXTURES / f, tmp_path), files)))
    wrong = {f: out[f][-300:] for f in files if expect[f] not in out[f]}
    assert not wrong, f"OLD fixtures failing for the wrong reason: {wrong}"


# --- 4. injection wiring ----------------------------------------------------

def test_runner_puts_context_in_delimited_block():
    import runner
    prompt = runner.build_system_prompt(context=PACK.read_text())
    assert "Background context from the caller:\n---\n=== " + DELIM in prompt
    assert prompt.count("---") >= 2


def _tasks_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tasks"
    d.mkdir()
    (d / "90_pack.json").write_text(json.dumps({
        "id": "90_pack", "name": "pack probe", "difficulty": "easy",
        "task": "noop", "setup": "true",
        "validation": {"type": "bash", "script": "true"},
        "cleanup": "true", "max_iter": 1,
        "variants": [{"id": "a", "language": "python"}, {"id": "f", "language": "zig"}],
    }))
    return d


def test_env_injects_pack_for_zig_only_and_stamps_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAST_PACKS", "1")
    monkeypatch.delenv("OPENBEAST_PACKS", raising=False)
    monkeypatch.delenv("OPENBEAST_DIAGNOSTICS", raising=False)
    monkeypatch.delenv("BEAST_ASSIST", raising=False)
    run_eval = _fresh_run_eval()
    run_eval.TASKS_DIR = str(_tasks_dir(tmp_path))
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    seen = {}

    def fake_agent(task, base_url, max_iter_override=None, timeout_scale=1.0, **_kw):
        seen[task["id"]] = task.get("_context_file")
        return {"exit_code": 0, "elapsed_seconds": 0.1, "stdout": "[iter 1/1]\nTask complete (iteration 1)\nTOKENS: prompt=1 completion=5 total=6\n",
                "stderr": "", "tokens": {"prompt": 1, "completion": 5, "total": 6}, "iterations": 1}
    run_eval.run_agent = fake_agent
    results = run_eval.run_eval(model_name="fake-model", use_cache=False)
    assert seen["90_pack_a"] is None, "python unit must get NO pack"
    assert seen["90_pack_f"] and seen["90_pack_f"].endswith("zig-0.16.md")
    sha8 = hashlib.sha256(PACK.read_bytes()).hexdigest()[:8]
    assert results["harness"]["packs"] == {"zig": sha8}
    assert results["harness"]["packs_component"] == f"pack1-{sha8}"
    assert all(t.get("iterations") == 1 for t in results["tasks"])
    # both env spellings pinned to the arm's state
    assert os.environ["BEAST_PACKS"] == "1" and os.environ["OPENBEAST_PACKS"] == "1"


def test_packs_off_by_default_no_context_no_provenance(tmp_path, monkeypatch):
    monkeypatch.delenv("BEAST_PACKS", raising=False)
    monkeypatch.delenv("OPENBEAST_PACKS", raising=False)
    run_eval = _fresh_run_eval()
    run_eval.TASKS_DIR = str(_tasks_dir(tmp_path))
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    seen = {}

    def fake_agent(task, base_url, max_iter_override=None, timeout_scale=1.0, **_kw):
        seen[task["id"]] = task.get("_context_file")
        return {"exit_code": 0, "elapsed_seconds": 0.1, "stdout": "", "stderr": "",
                "tokens": {"prompt": 1, "completion": 5, "total": 6}}
    run_eval.run_agent = fake_agent
    results = run_eval.run_eval(model_name="fake-model", use_cache=False)
    assert seen == {"90_pack_a": None, "90_pack_f": None}
    assert results["harness"]["packs"] == {} and "packs_component" not in results["harness"]
    assert os.environ["BEAST_PACKS"] == "0" and os.environ["OPENBEAST_PACKS"] == "0"


def test_run_agent_passes_context_file_flag(monkeypatch):
    run_eval = _fresh_run_eval()
    calls = []

    class FakeProc:
        pid = 4242
        returncode = 0

        def communicate(self, timeout=None):
            return ("TOKENS: prompt=1 completion=1 total=2\n", "")

    def fake_popen(cmd, **kw):
        if "--max-iter" in cmd:     # the runner; not the leftover sweep's `ps`
            calls.append(cmd)
        return FakeProc()
    monkeypatch.setattr(run_eval.subprocess, "Popen", fake_popen)
    base = {"id": "t", "task": "do it", "max_iter": 1}
    run_eval.run_agent(dict(base), "http://x/v1")
    run_eval.run_agent({**base, "_context_file": "/p/zig-0.16.md"}, "http://x/v1")
    assert "--context-file" not in calls[0]
    i = calls[1].index("--context-file")
    assert calls[1][i + 1] == "/p/zig-0.16.md"
    assert calls[1][-1] == "do it"  # positional task text stays last


def test_openbeast_packs_alias_enables(monkeypatch):
    monkeypatch.delenv("BEAST_PACKS", raising=False)
    monkeypatch.setenv("OPENBEAST_PACKS", "1")
    run_eval = _fresh_run_eval()
    on, comp, meta = run_eval.packs_flag()
    assert on and comp.startswith("pack1-") and len(comp) == len("pack1-") + 8
    assert meta["sha"]["zig"] == hashlib.sha256(PACK.read_bytes()).hexdigest()[:8]
    assert comp == f"pack1-{meta['sha']['zig']}"  # single pack ⇒ file sha8


def test_packs_flag_off_is_none(monkeypatch):
    monkeypatch.delenv("BEAST_PACKS", raising=False)
    monkeypatch.delenv("OPENBEAST_PACKS", raising=False)
    run_eval = _fresh_run_eval()
    assert run_eval.packs_flag() == (False, None, {})


def test_packs_cli_flag_sets_env(monkeypatch, capsys):
    monkeypatch.delenv("BEAST_PACKS", raising=False)
    run_eval = _fresh_run_eval()
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--packs", "--list"])
    run_eval.main()
    assert os.environ.get("BEAST_PACKS") == "1"
    assert "Available tasks" in capsys.readouterr().out


def test_benchmark_all_passes_packs_through():
    src = (ROOT / "evals" / "benchmark_all.py").read_text()
    assert '"--packs"' in src and 'os.environ["BEAST_PACKS"] = "1"' in src


def test_eval_arm_pins_both_pack_spellings():
    src = (ROOT / "evals" / "run_eval.py").read_text()
    assert 'os.environ["BEAST_PACKS"] = "1" if ctx.packs_on else "0"' in src
    assert 'os.environ["OPENBEAST_PACKS"] = "1" if ctx.packs_on else "0"' in src


# --- 5. cache era + drift ---------------------------------------------------

def test_cache_key_pack_component():
    import cache
    t = {"id": "01_x", "task": "t"}
    k_off = cache.cache_key(t, "slug", max_iter=10)
    k_on = cache.cache_key(t, "slug", max_iter=10, pack="pack1-abcd1234")
    assert k_on != k_off and ".pack1-abcd1234." in k_on and "pack1" not in k_off
    k_all = cache.cache_key(t, "slug", max_iter=10, diag="diag2-0000ffff", greedy=True, pack="pack1-abcd1234")
    assert ".diag2-0000ffff.greedy.pack1-abcd1234." in k_all


def test_pack_component_is_task_scoped(tmp_path, monkeypatch):
    """Non-zig units are byte-identical with packs on/off, so their cache
    keys must NOT carry the pack component (they share the unpacked era)."""
    monkeypatch.setenv("BEAST_PACKS", "1")
    run_eval = _fresh_run_eval()
    import cache
    cache.CACHE_DIR = tmp_path / "cache"
    run_eval.TASKS_DIR = str(_tasks_dir(tmp_path))
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    keys = []
    orig = cache.cache_key

    def spy(task, slug, **kw):
        k = orig(task, slug, **kw)
        keys.append((task["id"], k))
        return k
    monkeypatch.setattr(cache, "cache_key", spy)
    run_eval.run_agent = lambda *a, **kw: {"exit_code": 0, "elapsed_seconds": 0.1, "stdout": "",
                                          "stderr": "", "tokens": {"prompt": 1, "completion": 5, "total": 6}}
    run_eval.run_eval(model_name="fake-model", use_cache=True)
    by_id = dict(keys)
    assert "pack1-" in by_id["90_pack_f"] and "pack1-" not in by_id["90_pack_a"]


def _run_with_fake_agent(run_eval, tmp_path, use_cache=False):
    run_eval.TASKS_DIR = str(_tasks_dir(tmp_path))
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    seen = {}

    def fake_agent(task, base_url, max_iter_override=None, timeout_scale=1.0, **_kw):
        seen[task["id"]] = task.get("_context_file")
        return {"exit_code": 0, "elapsed_seconds": 0.1, "stdout": "", "stderr": "",
                "tokens": {"prompt": 1, "completion": 5, "total": 6}}
    run_eval.run_agent = fake_agent
    return seen, run_eval.run_eval(model_name="fake-model", use_cache=use_cache)


def test_host_pack_goes_to_every_unit_and_joins_the_zig_pack(tmp_path, monkeypatch, _host_pack_stub):
    """BEAST_PACKS=1 = language pack + host pack: a python unit gets the host
    pack alone, a zig unit ONE file holding the zig pack then the host pack."""
    _host_pack_stub["text"] = HOST_TEXT
    monkeypatch.setenv("BEAST_PACKS", "1")
    monkeypatch.delenv("OPENBEAST_PACKS", raising=False)
    run_eval = _fresh_run_eval()
    seen, results = _run_with_fake_agent(run_eval, tmp_path)
    py, zg = seen["90_pack_a"], seen["90_pack_f"]
    assert py and Path(py).read_text() == HOST_TEXT
    assert str(tmp_path / "host-pack") in py, "host pack written outside OPENBEAST_HOST_PACK_DIR"
    both = Path(zg).read_text()
    assert both.startswith(PACK.read_text().rstrip("\n") + "\n\n") and both.endswith(HOST_TEXT)
    zsha = hashlib.sha256(PACK.read_bytes()).hexdigest()[:8]
    hsha = hashlib.sha256(HOST_TEXT.encode()).hexdigest()[:8]
    assert results["harness"]["packs"] == {"zig": zsha, "host": hsha}
    comp = "pack1-" + hashlib.sha256(PACK.read_bytes() + HOST_TEXT.encode()).hexdigest()[:8]
    assert results["harness"]["packs_component"] == comp
    assert results["harness"]["host_pack"]["facts"] == ["nproc_missing"]
    assert "path" not in results["harness"]["host_pack"]


def test_host_pack_sha_is_in_the_cache_component(monkeypatch, _host_pack_stub):
    """The pack1 component names the host pack too: another host's facts
    (or a toolchain update that changes them) is another era."""
    monkeypatch.setenv("BEAST_PACKS", "1")
    run_eval = _fresh_run_eval()
    _, empty, meta0 = run_eval.packs_flag()
    _host_pack_stub["text"] = HOST_TEXT
    _, with_host, meta1 = run_eval.packs_flag()
    _host_pack_stub["text"] = HOST_TEXT + "- another probed gap.\n"
    _, other_host, _ = run_eval.packs_flag()
    assert len({empty, with_host, other_host}) == 3
    assert meta0["host"] == {} and "host" not in meta0["sha"]
    assert meta1["sha"]["host"] == hashlib.sha256(HOST_TEXT.encode()).hexdigest()[:8]


def test_host_pack_puts_every_unit_in_the_pack_era(tmp_path, monkeypatch, _host_pack_stub):
    _host_pack_stub["text"] = HOST_TEXT
    monkeypatch.setenv("BEAST_PACKS", "1")
    run_eval = _fresh_run_eval()
    import cache
    cache.CACHE_DIR = tmp_path / "cache"
    keys = []
    orig = cache.cache_key

    def spy(task, slug, **kw):
        k = orig(task, slug, **kw)
        keys.append((task["id"], k))
        return k
    monkeypatch.setattr(cache, "cache_key", spy)
    _run_with_fake_agent(run_eval, tmp_path, use_cache=True)
    by_id = dict(keys)
    assert "pack1-" in by_id["90_pack_f"] and "pack1-" in by_id["90_pack_a"]


def test_packs_off_never_probes_the_host(monkeypatch, _host_pack_stub):
    """Default behaviour unchanged: with packs off nothing is probed."""
    from lang import host_pack as HP
    monkeypatch.delenv("BEAST_PACKS", raising=False)
    monkeypatch.delenv("OPENBEAST_PACKS", raising=False)
    run_eval = _fresh_run_eval()
    monkeypatch.setattr(HP, "build", lambda host=None: pytest.fail("probed with packs off"))
    assert run_eval.packs_flag() == (False, None, {})


def test_pack_variant_v2_switches_the_eval_pack_and_its_era(monkeypatch):
    """OPENBEAST_PACK_VARIANT picks which bytes an eval arm measures; the
    bytes are in pack1-<sha8>, so v1 and v2 rows never share a key."""
    monkeypatch.setenv("BEAST_PACKS", "1")
    run_eval = _fresh_run_eval()
    on1, c1, m1 = run_eval.packs_flag({"BEAST_PACKS": "1"})
    on2, c2, m2 = run_eval.packs_flag({"BEAST_PACKS": "1", "OPENBEAST_PACK_VARIANT": "v2"})
    assert m1["variant"] == "v1" and m2["variant"] == "v2"
    assert m1["paths"]["zig"] == str(PACK) and m2["paths"]["zig"] == str(PACK_V2)
    assert c1 == "pack1-" + hashlib.sha256(PACK.read_bytes()).hexdigest()[:8]
    assert c2 == "pack1-" + hashlib.sha256(PACK_V2.read_bytes()).hexdigest()[:8]
    assert c1 != c2
    assert run_eval.packs_flag({"BEAST_PACKS": "1", "OPENBEAST_PACK_VARIANT": "V1"})[1] == c1


def test_pack_variant_v2_with_host_pack_joins_both(monkeypatch, _host_pack_stub):
    _host_pack_stub["text"] = HOST_TEXT
    run_eval = _fresh_run_eval()
    _, comp, meta = run_eval.packs_flag({"BEAST_PACKS": "1", "OPENBEAST_PACK_VARIANT": "v2"})
    both = Path(meta["unit_paths"]["zig"]).read_text()
    assert both.startswith(PACK_V2.read_text().rstrip("\n") + "\n\n") and both.endswith(HOST_TEXT)
    assert comp == "pack1-" + hashlib.sha256(PACK_V2.read_bytes() + HOST_TEXT.encode()).hexdigest()[:8]


def test_unknown_pack_variant_aborts(monkeypatch):
    run_eval = _fresh_run_eval()
    with pytest.raises(SystemExit, match="OPENBEAST_PACK_VARIANT"):
        run_eval.packs_flag({"BEAST_PACKS": "1", "OPENBEAST_PACK_VARIANT": "v3"})


def test_pack_variant_is_stamped_in_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAST_PACKS", "1")
    monkeypatch.setenv("OPENBEAST_PACK_VARIANT", "v2")
    run_eval = _fresh_run_eval()
    seen, results = _run_with_fake_agent(run_eval, tmp_path)
    assert seen["90_pack_f"] == str(PACK_V2)
    assert results["harness"]["pack_variant"] == "v2"
    assert results["harness"]["packs"]["zig"] == hashlib.sha256(PACK_V2.read_bytes()).hexdigest()[:8]


def test_beast_lang_never_serves_the_v2_file():
    """agents/lang/packs.py picks hand-written packs by file name stamp:
    `zig-0.16-v2.md` reads as a prerelease stamp and must never be what
    the language tools serve for a release zig."""
    from lang import packs as P
    hw = P._handwritten("zig", "0.16.0")
    assert hw and hw[0].endswith("zig-0.16.md")


def test_drift_abort_on_tampered_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAST_PACKS", "1")
    run_eval = _fresh_run_eval()
    bad = tmp_path / "packs"
    bad.mkdir()
    text = PACK.read_text().replace("sha256(digest)=", "sha256(digest)=0")[:-1]  # corrupt sha field
    (bad / "zig-0.16.md").write_text(text.replace("sha256(digest)=0", "sha256(digest)=") + "tampered\n")
    monkeypatch.setattr(run_eval, "PACKS_DIR", str(bad))
    with pytest.raises(SystemExit, match="drifted"):
        run_eval.packs_flag()


def test_parse_iterations():
    run_eval = _fresh_run_eval()
    assert run_eval._parse_iterations("[iter 1/15]\nx\n[iter 2/15]\nTask complete (iteration 2)\n") == 2
    assert run_eval._parse_iterations("[iter 1/15]\n[iter 7/15]\nMax iterations (15) reached") == 7
    assert run_eval._parse_iterations("(timed out)") is None


def test_this_file_leaves_the_arm_flags_as_it_found_them():
    """Last in the file on purpose: every test above that runs the harness
    wrote the flags into os.environ. Without _restore_harness_env this fails
    on OPENBEAST_PACKS (and the three flags written beside it)."""
    assert {k: os.environ.get(k) for k in _HARNESS_ENV} == _ENV_AT_START["env"]
