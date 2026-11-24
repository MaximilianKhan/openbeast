#!/usr/bin/env python3
"""AGENT_PROFILE=deepseek-best / qwen38-best: the v5 arm-T2 setup as OpenBeast's
daily default for DeepSeek-V4.1-Flash (Mia EXL3) (65/71, board row DS-T2,
2026-11-19) and for the RTX 5090 default, Qwen3.8 27B Uncensored MTP (52/71
scored, board row Q38U-T2, arm Q_T2, 2026-11-23). Both names alias one switch
table ("t2") in scripts/lib/conf.sh.

What is pinned here (the conf.sh exports themselves: tests/test_agent_profile_conf.sh):
  * each profile IS its measured arm: every switch it sets equals what the
    campaign arm resolves to (deepseek-best = T2, qwen38-best = Q_T2), so the
    daily default and the measured row cannot drift apart;
  * the two names resolve to identical values on every key;
  * the v3 zig pack production may serve is the exact bytes T2 measured, only
    on opt-in, never under eval, and a bad variant serves nothing;
  * conf.sh and pack_context accept the same variants;
  * beast-chat's agent sessions keep OPENBEAST_AGENT_MAX_TOKENS past the
    secret scrub (its name holds "TOKEN");
  * openbeast.conf.example documents every new key;
  * none of the touched files is an eval-era input.

Run: python3 -m pytest tests/test_agent_profile.py -q
"""
import hashlib
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
EVALS = os.path.join(REPO, "evals")
for _p in (AGENTS, EVALS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lang import pack_context as PC  # noqa: E402
from lang import packs as P  # noqa: E402

import campaign as C  # noqa: E402

CONF_SH = os.path.join(REPO, "scripts", "lib", "conf.sh")
EXAMPLE = os.path.join(REPO, "openbeast.conf.example")
V3 = os.path.join(AGENTS, "packs", "zig-0.16-v3.md")
V1 = os.path.join(AGENTS, "packs", "zig-0.16.md")

PROFILE_KEYS = ("INFERENCE_THINKING", "AGENT_MAX_TOKENS", "AGENT_ACT_NUDGE", "BEAST_ASSIST",
                "DIAG_EDIT_HINT", "AGENT_VERIFY_DONE", "AGENT_ENV_BLOCK", "AGENT_WALL_WARN",
                "LANG_PACK_VARIANT")
#: openbeast.conf key -> the env name an eval arm sets for the same switch.
ARM_ENV = {"AGENT_MAX_TOKENS": "OPENBEAST_AGENT_MAX_TOKENS",
           "AGENT_ACT_NUDGE": "OPENBEAST_AGENT_ACT_NUDGE",
           "BEAST_ASSIST": "BEAST_ASSIST",
           "DIAG_EDIT_HINT": "OPENBEAST_DIAG_EDIT_HINT",
           "AGENT_VERIFY_DONE": "OPENBEAST_AGENT_VERIFY_DONE",
           "AGENT_ENV_BLOCK": "OPENBEAST_AGENT_ENV_BLOCK",
           "AGENT_WALL_WARN": "OPENBEAST_AGENT_WALL_WARN",
           "LANG_PACK_VARIANT": "OPENBEAST_PACK_VARIANT"}


def _profile_value(key: str, profile: str = "deepseek-best") -> str | None:
    """What conf.sh's _ob_profile_value prints for KEY (None = nothing)."""
    script = (f"eval \"$(sed -n '/^_ob_profile_set() {{/,/^}}/p;/^_ob_profile_value() {{/,/^}}/p' '{CONF_SH}')\"; "
              f"AGENT_PROFILE='{profile}'; _ob_profile_value '{key}' || echo '<none>'")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         timeout=30).stdout.strip()
    return None if out == "<none>" else out


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    for k in ("OPENBEAST_EVAL", "OPENBEAST_TASK_PATHS", "OPENBEAST_LANG_IN_EVAL",
              "OPENBEAST_LANG_PACK_VARIANT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENBEAST_LANG_PACK_CONTEXT", "auto")
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "auto")
    monkeypatch.setenv("OPENBEAST_PACK_CONTEXT_DIR", str(tmp_path / "pack-ctx"))
    monkeypatch.setattr(P, "_REPO", str(tmp_path))            # no real openbeast.conf
    monkeypatch.setattr(PC, "_installed_zig", lambda ttl=60.0: "0.16.0")
    PC._LOGGED.clear()
    yield


# --- the profile is arm T2 -------------------------------------------------------------

#: profile name -> (campaign arm it reproduces, board row that arm earned).
PROFILES = {"deepseek-best": ("T2", "DS-T2"), "qwen38-best": ("Q_T2", "Q38U-T2"),
            "inkling-best": ("I_T2_stock", "INK-T2-STOCK")}


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_the_profile_is_its_campaign_arm(profile):
    camp = C.load_campaign("v5-thinking-first")
    t2 = C.resolve_arm(camp, PROFILES[profile][0])
    for key, env in ARM_ENV.items():
        assert _profile_value(key, profile) == t2["env"][env], key
    assert _profile_value("INFERENCE_THINKING", profile) == t2["thinking"] == "on"
    # Everything the arm switches on is either in the profile or named as
    # eval-only (the packs flag) — no T2 switch silently missing.
    on = {k for k, v in t2["env"].items() if v not in ("", "0")}
    covered = set(ARM_ENV.values()) | {"OPENBEAST_DIAGNOSTICS",     # = BEAST_ASSIST's other spelling
                                       "BEAST_PACKS", "OPENBEAST_PACKS",   # production: LANG_PACK_CONTEXT
                                       "OPENBEAST_EVAL_ENV_ERA"}           # eval bookkeeping
    assert on <= covered, on - covered


def test_q_t2_is_t2_at_one_stream():
    """qwen38-best may share T2's table only because Q_T2 differs from T2 in
    concurrency alone (the rig's llama-server serves one stream)."""
    camp = C.load_campaign("v5-thinking-first")
    t2, q = C.resolve_arm(camp, "T2"), C.resolve_arm(camp, "Q_T2")
    assert q["env"] == t2["env"] and q["thinking"] == t2["thinking"]
    assert {k for k in set(t2) | set(q) if t2.get(k) != q.get(k)} == {"jobs", "slots"}
    assert (q["jobs"], q["slots"]) == (1, 1)


def test_i_t2_stock_is_t2_at_one_stream():
    """inkling-best (the RTX 5090 default since 2026-11-24) may share T2's table
    only because I_T2_stock differs from T2 in concurrency alone: the model,
    weights and server are the row's, not the switches."""
    camp = C.load_campaign("v5-thinking-first")
    t2, i = C.resolve_arm(camp, "T2"), C.resolve_arm(camp, "I_T2_stock")
    assert i["env"] == t2["env"] and i["thinking"] == t2["thinking"]
    assert (i["jobs"], i["slots"]) == (1, 1)


def test_the_names_share_one_table():
    for name in ("qwen38-best", "inkling-best"):
        for key in PROFILE_KEYS:
            assert _profile_value(key, name) == _profile_value(key, "deepseek-best") \
                is not None, (name, key)


def test_the_rig_default_preset_is_the_board_default():
    """board.json "defaults" names each platform's shipped row and preset."""
    import json
    board = json.load(open(os.path.join(EVALS, "boards", "v5", "board.json")))
    for d in board["defaults"]:
        assert d["agent_profile"] in PROFILES and PROFILES[d["agent_profile"]][1] == d["run"], d
        for p in d.get("previous", []):
            assert PROFILES[p["agent_profile"]][1] == p["run"], p


def test_every_profile_cites_a_board_row():
    import json
    board = json.load(open(os.path.join(EVALS, "boards", "v5", "board.json")))
    ids = {r["id"] for r in board["runs"]}
    text = open(CONF_SH).read()
    for profile, (arm, row) in PROFILES.items():
        assert row in ids, row
        assert row in text                                          # cited in conf.sh's comment
    m = re.search(r'^_OB_AGENT_PROFILES="([^"]*)"', text, re.M)
    assert m and sorted(m.group(1).split()) == sorted(PROFILES)


def test_only_known_profiles_have_values():
    assert all(_profile_value(k, "glm-best") is None for k in PROFILE_KEYS)
    assert _profile_value("SERVE_SCRIPT") is None                  # never beyond its keys


def test_example_documents_every_key():
    text = open(EXAMPLE).read()
    for key in ("AGENT_PROFILE", "AGENT_ACT_NUDGE", "AGENT_VERIFY_DONE", "AGENT_ENV_BLOCK",
                "AGENT_WALL_WARN", "DIAG_EDIT_HINT", "LANG_PACK_VARIANT"):
        assert re.search(rf"^#?{key}=", text, re.M), key
    block = text.split("#AGENT_PROFILE=deepseek-best")[0].rsplit("A named set of daily-agent", 1)[1]
    assert "65/71" in block and "DS-T2" in block and "2026-11-19" in block
    assert "EVAL-ONLY" in block and "32768" in block                 # honest about limits
    assert "qwen38-best" in block and "Q38U-T2" in block and "2026-11-23" in block
    assert "52/71" in block and "single seed" in block
    assert "inkling-best" in block and "INK-T2-STOCK" in block and "53/71" in block


# --- the v3 pack in production ------------------------------------------------------------

def test_the_v3_pin_is_the_measured_bytes():
    """T2's results header: packs.zig = cfdab8fa (v3)."""
    sha = hashlib.sha256(open(V3, "rb").read()).hexdigest()
    assert PC.PRODUCTION_VARIANT_SHA256["v3"]["zig"] == sha
    assert sha.startswith("cfdab8fa")
    assert PC.PACK_VARIANTS["v3"] == {"zig": os.path.basename(V3)}


def test_default_stays_v1(tmp_path):
    assert PC.pack_variant()[0] == "v1"
    p, why = PC.served_pack("zig")
    assert p is not None and p.path == V1, why
    assert "LANG_PACK_VARIANT" not in PC.status_line()                   # v1 output unchanged


def test_v3_is_served_on_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENBEAST_LANG_PACK_VARIANT", "v3")
    path, info = PC.production_context("write a zig tokenizer", str(tmp_path))
    assert path == V3 and info["label"] == "zig@cfdab8fa"
    assert PC.status_line().endswith("@cfdab8fa, LANG_PACK_VARIANT=v3)")


def test_v3_from_openbeast_conf(tmp_path):
    (tmp_path / "openbeast.conf").write_text('LANG_PACK_VARIANT="V3"   # T2\n')
    assert PC.pack_variant()[0] == "v3"


@pytest.mark.parametrize("raw", ["v2", "v9", "latest"])
def test_an_unmeasured_or_unknown_variant_serves_nothing(raw, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENBEAST_LANG_PACK_VARIANT", raw)
    path, info = PC.production_context("write zig", str(tmp_path))
    assert path is None and "LANG_PACK_VARIANT" in info["reason"]
    assert "serving no pack" in capsys.readouterr().err


def test_an_edited_v3_is_an_unmeasured_treatment(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENBEAST_LANG_PACK_VARIANT", "v3")
    monkeypatch.setattr(PC, "PRODUCTION_VARIANT_SHA256", {"v3": {"zig": "0" * 64}})
    p, why = PC.served_pack("zig")
    assert p is None and "unmeasured treatment" in why


@pytest.mark.parametrize("marker", ["OPENBEAST_EVAL", "OPENBEAST_TASK_PATHS"])
def test_never_under_eval(marker, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENBEAST_LANG_PACK_VARIANT", "v3")
    monkeypatch.setenv(marker, "1")
    assert PC.production_context("write zig", str(tmp_path))[0] is None


def test_conf_sh_and_pack_context_accept_the_same_variants():
    m = re.search(r'^_OB_PACK_VARIANTS="([^"]*)"', open(CONF_SH).read(), re.M)
    assert m and tuple(m.group(1).split()) == PC.PRODUCTION_VARIANTS


def test_run_eval_variants_are_untouched():
    """The eval side reads PACK_VARIANTS / DEFAULT_PACK_VARIANT; this change
    only added the production pin beside them."""
    assert PC.DEFAULT_PACK_VARIANT == "v1"
    assert set(PC.PACK_VARIANTS) == {"v1", "v2", "v3"}


# --- beast-chat agent sessions -------------------------------------------------------------

def test_chat_agent_sessions_keep_the_turn_cap(monkeypatch):
    import chat_server
    monkeypatch.setenv("OPENBEAST_AGENT_MAX_TOKENS", "8192")
    monkeypatch.setenv("OPENBEAST_AGENT_ACT_NUDGE", "1")
    monkeypatch.setenv("OPENBEAST_MCPO_ADMIN_KEY", "s3cret")
    assert chat_server.is_secret_env_name("OPENBEAST_AGENT_MAX_TOKENS")   # why keep is needed
    assert "OPENBEAST_AGENT_MAX_TOKENS" not in chat_server.child_env()     # a job: scrubbed
    env = chat_server.child_env({}, keep=chat_server.AGENT_CHILD_KEEP)
    assert env["OPENBEAST_AGENT_MAX_TOKENS"] == "8192"
    assert env["OPENBEAST_AGENT_ACT_NUDGE"] == "1"
    assert "OPENBEAST_MCPO_ADMIN_KEY" not in env                            # still scrubbed
    assert set(chat_server.AGENT_CHILD_KEEP) == {"OPENBEAST_API_KEY", "OPENAI_API_KEY",
                                                 "OPENBEAST_AGENT_MAX_TOKENS"}


# --- the era ---------------------------------------------------------------------------------

def test_nothing_here_is_hashed_into_the_eval_era():
    """The profile is config: none of the files it touched are era inputs
    (evals/cache.py CONTEXT_FILES), so every DS-T2 row stays comparable."""
    import cache
    rels = {os.path.relpath(p, REPO) for p in cache.CONTEXT_FILES}
    for touched in ("scripts/lib/conf.sh", "agents/lang/pack_context.py", "agents/chat_server.py",
                    "evals/campaign.py", "evals/campaigns/v5-thinking-first.json",
                    "openbeast.conf.example"):
        assert touched not in rels, touched


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
