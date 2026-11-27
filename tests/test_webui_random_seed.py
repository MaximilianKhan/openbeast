#!/usr/bin/env python3
"""agents/webui_functions/random_seed.py — the Open WebUI random seed filter.

TensorFold falls back to a fixed seed when a request names none, so Open
WebUI's Regenerate returned the same answer. The filter's inlet gives every
unseeded chat request a fresh random seed and steps aside for any seed that
is already there. Loaded by path, the way Open WebUI execs it: it must not
need open_webui (or anything outside the stdlib) to import.

The configure-webui.sh step that installs it is tested in
tests/test_webui_default_admin.py (the stubbed-WebUI rig).

Run: pytest tests/test_webui_random_seed.py
"""

import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "agents", "webui_functions", "random_seed.py")


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("random_seed_under_test", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def flt(mod):
    return mod.Filter()


def _chat(**extra):
    body = {"model": "glm", "messages": [{"role": "user", "content": "hi"}]}
    body.update(extra)
    return body


def test_adds_a_seed_when_absent(flt, mod):
    out = flt.inlet(_chat())
    assert isinstance(out["seed"], int)
    assert 0 <= out["seed"] < mod.SEED_LIMIT <= 2**31


def test_seeds_differ_between_requests(flt):
    # 20 draws from 2**31: a collision of all of them is impossible in practice.
    assert len({flt.inlet(_chat())["seed"] for _ in range(20)}) > 1


def test_explicit_body_seed_is_kept(flt):
    assert flt.inlet(_chat(seed=1))["seed"] == 1
    # 0 is a real seed, not "absent".
    assert flt.inlet(_chat(seed=0))["seed"] == 0


def test_a_null_seed_counts_as_absent(flt):
    assert isinstance(flt.inlet(_chat(seed=None))["seed"], int)


def test_params_and_options_seed_are_kept(flt):
    out = flt.inlet(_chat(params={"seed": 7}))
    assert "seed" not in out and out["params"]["seed"] == 7
    out = flt.inlet(_chat(options={"seed": 9}))
    assert "seed" not in out


def test_model_params_seed_is_respected(flt):
    """WebUI applies Admin -> Models params AFTER the filters, and only to
    keys the body lacks: a seed added here would silently override it."""
    model = {"id": "glm", "info": {"params": {"seed": 42}}}
    assert "seed" not in flt.inlet(_chat(), __model__=model)
    # Negative control: a model without a seed still gets a random one.
    model = {"id": "glm", "info": {"params": {"temperature": 0.6}}}
    assert isinstance(flt.inlet(_chat(), __model__=model)["seed"], int)
    # A malformed model dict does not break the request.
    assert isinstance(flt.inlet(_chat(), __model__={"info": None})["seed"], int)
    assert isinstance(flt.inlet(_chat(), __model__="glm")["seed"], int)


@pytest.mark.parametrize("body", [None, "text", 3, ["a"], {}, {"messages": "x"},
                                  {"prompt": "complete me"}])
def test_non_chat_input_passes_through_unchanged(flt, body):
    before = repr(body)
    out = flt.inlet(body)
    assert out is body and repr(out) == before


def test_module_is_a_filter_and_stdlib_only(mod):
    # Open WebUI picks the type from the class name (Pipe > Filter > Action).
    assert hasattr(mod, "Filter")
    assert not hasattr(mod, "Pipe") and not hasattr(mod, "Action")
    src = open(SRC).read()
    imports = [ln.split()[1] for ln in src.splitlines()
               if ln.startswith(("import ", "from "))]
    assert imports == ["secrets"]


def test_source_survives_webui_import_rewrite():
    """WebUI stores replace_imports(content), which rewrites `from utils`,
    `from apps`, `from main` and `from config`. configure-webui.sh compares
    the stored content with this file to decide whether to update, so the
    rewrite must be a no-op here or every start would re-upload it."""
    src = open(SRC).read()
    for old in ("from utils", "from apps", "from main", "from config"):
        assert old not in src


def test_frontmatter_has_only_its_own_keys():
    """Open WebUI reads every `key: value` line of the leading docstring as
    frontmatter, so the explanation must live in comments, not there."""
    lines = open(SRC).read().splitlines()
    assert lines[0] == '"""'
    end = lines.index('"""', 1)
    keys = [ln.split(":", 1)[0] for ln in lines[1:end]]
    assert keys == ["title", "author", "description", "version"]
