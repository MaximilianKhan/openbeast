"""
title: Random Seed
author: OpenBeast
description: Gives every chat request without a seed a fresh random one, so Regenerate returns a new answer.
version: 1.0.0
"""

# An Open WebUI global Filter function. scripts/configure-webui.sh installs it
# through WebUI's functions API (id `random_seed`), active and global, unless
# WEBUI_RANDOM_SEED=false in openbeast.conf. Open WebUI runs it; nothing in
# OpenBeast imports it, and it must not import open_webui (it is unit-tested
# outside the container, tests/test_webui_random_seed.py).
#
# Why: TensorFold (the spark-hydra GLM server) falls back to a FIXED seed
# when a request carries none, so Open WebUI's Regenerate re-sent the same
# request and got the same text back, token for token, even at temperature
# 0.6 (probed 2026-11-16). llama.cpp already draws a random seed per request,
# so on the rig this changes nothing observable.
#
# Who keeps control: a seed that is already there wins. That covers a seed
# in the request body, one set in the chat's Controls (WebUI copies those
# into the body before inlet filters run), an Ollama `options.seed`, and a
# seed in the model's own Admin -> Models params (WebUI applies those AFTER
# the filters and only to keys the body lacks, so the filter must step aside
# for it here). An explicit seed stays reproducible.
#
# Scope: Open WebUI chats only. The eval runner (agents/runner.py), opencode
# and every other client talk to the inference server directly and never
# pass through this filter.

import secrets

# Below 2**31 so every engine's seed field takes it (llama.cpp reads uint32,
# some OpenAI-compatible servers a signed int32).
SEED_LIMIT = 2**31


def _has_seed(d):
    return isinstance(d, dict) and d.get("seed") is not None


def _model_params(model):
    if not isinstance(model, dict):
        return None
    info = model.get("info")
    return info.get("params") if isinstance(info, dict) else None


def has_explicit_seed(body, model=None):
    """True when the request, its options or the model's params name a seed."""
    return (_has_seed(body)
            or _has_seed(body.get("params"))
            or _has_seed(body.get("options"))
            or _has_seed(_model_params(model)))


class Filter:
    def inlet(self, body: dict, __model__: dict = None) -> dict:
        # Anything that is not a chat request passes through untouched.
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            return body
        if has_explicit_seed(body, __model__):
            return body
        body["seed"] = secrets.randbelow(SEED_LIMIT)
        return body
