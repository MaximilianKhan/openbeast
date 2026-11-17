"""
title: Thinking Default
author: OpenBeast
description: Applies the serving host's thinking default (INFERENCE_THINKING) to chats that do not choose one.
version: 1.0.0
"""

# An Open WebUI global Filter function. scripts/configure-webui.sh installs it
# through WebUI's functions API (id `thinking_default`), active and global,
# when INFERENCE_THINKING is on or off in openbeast.conf, and writes that
# value into DEFAULT_THINKING below; with INFERENCE_THINKING unset an
# installed copy is switched off. Open WebUI runs it; nothing in OpenBeast
# imports it, and it must not import open_webui (it is unit-tested outside
# the container, tests/test_webui_thinking_default.py).
#
# Why: thinking is a per-request chat-template switch
# (chat_template_kwargs.enable_thinking), and a request that does not set it
# gets the server's default. TensorFold (the spark-hydra GLM server) defaults
# to OFF, while GLM-5.3-Flash measured 51/71 with thinking on against 36/71
# off (v5 arms C and B, docs/SPARK_HYDRA.md "Recommended serving
# configuration"). Open WebUI talks to the server on loopback, not through
# beast-gate, so the gate's injection never reaches it: this filter is the
# WebUI half of the same default. WebUI 0.13.3 renders the returned
# reasoning_content as a collapsed "Thought for N seconds" block.
#
# Who keeps control: a thinking choice that is already there wins. That
# covers the request body (the chat's Controls -> Custom Parameters, e.g.
# chat_template_kwargs = {"enable_thinking": false}, or Reasoning Effort;
# WebUI copies those into the body before inlet filters run) and the
# model's own Admin -> Models params (WebUI applies those AFTER the filters,
# and only to keys the body lacks, so the filter must step aside for them
# here or it would shadow them). WebUI's background tasks (titles, tags)
# do not run inlet filters and keep the server default.

import json

# Rewritten by scripts/configure-webui.sh from INFERENCE_THINKING ("on" or
# "off"). Anything else makes the filter a no-op.
DEFAULT_THINKING = "on"

# The same set beast-gate respects (agents/edge.py _THINKING_BODY_KEYS).
BODY_KEYS = ("enable_thinking", "thinking", "reasoning", "reasoning_effort",
             "reasoning_budget_tokens", "thinking_budget")
TEMPLATE_KEYS = ("enable_thinking", "thinking")


def _loads(v):
    """WebUI keeps custom params as the strings the admin typed."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def names_thinking(d):
    """True when the params dict `d` already makes a thinking choice."""
    if not isinstance(d, dict):
        return False
    if any(_loads(d.get(k)) is not None for k in BODY_KEYS):
        return True
    ctk = _loads(d.get("chat_template_kwargs"))
    if ctk is not None and not isinstance(ctk, dict):
        return True        # not ours to read: leave the request as it is
    if isinstance(ctk, dict) and any(ctk.get(k) is not None for k in TEMPLATE_KEYS):
        return True
    custom = _loads(d.get("custom_params"))
    return isinstance(custom, dict) and names_thinking(custom)


def _model_params(model):
    if not isinstance(model, dict):
        return None
    info = model.get("info")
    return info.get("params") if isinstance(info, dict) else None


def has_explicit_thinking(body, model=None):
    """True when the request, its params or the model's params choose."""
    return (names_thinking(body)
            or names_thinking(body.get("params"))
            or names_thinking(_model_params(model)))


class Filter:
    def inlet(self, body: dict, __model__: dict = None,
              __metadata__: dict = None) -> dict:
        if DEFAULT_THINKING not in ("on", "off"):
            return body
        # Anything that is not a chat request passes through untouched.
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            return body
        if isinstance(__metadata__, dict) and __metadata__.get("task"):
            return body    # a background task (title, tags): server default
        if has_explicit_thinking(body, __model__):
            return body
        ctk = body.get("chat_template_kwargs")
        ctk = dict(ctk) if isinstance(ctk, dict) else {}
        ctk["enable_thinking"] = DEFAULT_THINKING == "on"
        body["chat_template_kwargs"] = ctk
        return body
