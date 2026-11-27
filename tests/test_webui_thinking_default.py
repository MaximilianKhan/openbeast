#!/usr/bin/env python3
"""agents/webui_functions/thinking_default.py — the WebUI thinking default.

Open WebUI talks to the inference server on loopback, so beast-gate's
INFERENCE_THINKING injection never reaches it; this global filter applies
the same default to WebUI chats and steps aside for any thinking choice the
chat or the model already made. Loaded by path, the way Open WebUI execs it.

The configure-webui.sh step that installs it is tested in
tests/test_webui_default_admin.py (the stubbed-WebUI rig).

Run: pytest tests/test_webui_thinking_default.py
"""

import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "agents", "webui_functions", "thinking_default.py")


def _load(default="on"):
    spec = importlib.util.spec_from_file_location("thinking_default_under_test", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.DEFAULT_THINKING = default
    return m


def _chat(**extra):
    body = {"model": "glm", "messages": [{"role": "user", "content": "hi"}]}
    body.update(extra)
    return body


@pytest.mark.parametrize("default,flag", [("on", True), ("off", False)])
def test_sets_the_default_when_absent(default, flag):
    out = _load(default).Filter().inlet(_chat())
    assert out["chat_template_kwargs"] == {"enable_thinking": flag}


def test_the_shipped_default_is_on():
    spec = importlib.util.spec_from_file_location("td_raw", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.DEFAULT_THINKING == "on"


@pytest.mark.parametrize("default", ["", "maybe", None])
def test_no_valid_default_is_a_no_op(default):
    body = _chat()
    assert _load(default).Filter().inlet(dict(body)) == body


def test_keeps_other_template_kwargs():
    out = _load().Filter().inlet(_chat(chat_template_kwargs={"foo": 1}))
    assert out["chat_template_kwargs"] == {"foo": 1, "enable_thinking": True}


@pytest.mark.parametrize("extra", [
    {"chat_template_kwargs": {"enable_thinking": False}},
    {"chat_template_kwargs": {"thinking": True}},
    {"reasoning_effort": "low"},
    {"enable_thinking": False},
    {"reasoning_budget_tokens": 0},
    {"chat_template_kwargs": "weird"},
    {"params": {"custom_params": {"chat_template_kwargs": '{"enable_thinking": false}'}}},
])
def test_an_explicit_choice_in_the_request_wins(extra):
    body = _chat(**extra)
    before = repr(body)
    assert repr(_load().Filter().inlet(body)) == before


@pytest.mark.parametrize("params", [
    {"custom_params": {"chat_template_kwargs": '{"enable_thinking": false}'}},
    {"custom_params": {"chat_template_kwargs": {"enable_thinking": False}}},
    {"chat_template_kwargs": {"enable_thinking": False}},
    {"reasoning_effort": "high"},
])
def test_a_choice_in_the_model_params_wins(params):
    # WebUI applies model params AFTER inlet filters and only to keys the body
    # lacks: setting chat_template_kwargs here would shadow the admin's value.
    body = _chat()
    out = _load().Filter().inlet(body, __model__={"info": {"params": params}})
    assert "chat_template_kwargs" not in out


def test_unrelated_model_params_do_not_block():
    out = _load().Filter().inlet(_chat(), __model__={"info": {"params": {"temperature": 0.6}}})
    assert out["chat_template_kwargs"] == {"enable_thinking": True}


def test_background_tasks_and_non_chat_bodies_pass_through():
    f = _load().Filter()
    assert "chat_template_kwargs" not in f.inlet(_chat(), __metadata__={"task": "title_generation"})
    assert f.inlet({"prompt": "x"}) == {"prompt": "x"}
    assert f.inlet(None) is None
