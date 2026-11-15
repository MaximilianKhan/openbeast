#!/usr/bin/env python3
"""The ambient inference key goes to configured ORIGINS only.

start_agent(base_url=...) is model-callable, so _key_endpoint_trusted must
match scheme + host + port against the endpoints this install was set up
for — not just the hostname (any loopback port, or any port/scheme on the
worker host, used to receive OPENBEAST_API_KEY).

Run: python3 -m pytest tests/test_runner_key_trust.py -q
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import runner  # noqa: E402

_ENV = ("OPENBEAST_API_KEY", "OPENAI_API_KEY", "OPENBEAST_AGENT_INFERENCE_URL",
        "OPENBEAST_INFERENCE_URL", "OPENBEAST_CONSUMER_BASE")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENBEAST_API_KEY", "sekrit")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:4444/v1",        # some other loopback listener
    "http://localhost:1/x",
    "https://localhost:8080/v1",       # right port, wrong scheme
])
def test_loopback_other_ports_get_no_key(url):
    assert runner.resolve_api_key(None, url) == "not-needed"


@pytest.mark.parametrize("url", [
    "http://localhost:8080/v1",        # the runner's default
    "http://127.0.0.1:8080/v1",        # same origin, loopback alias
    "http://[::1]:8080",
])
def test_default_local_server_gets_key(url):
    assert runner.resolve_api_key(None, url) == "sekrit"


def test_worker_host_other_port_or_scheme_gets_no_key(monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_INFERENCE_URL", "https://worker.tail.ts.net/v1")
    assert runner.resolve_api_key(None, "https://worker.tail.ts.net/v1") == "sekrit"
    assert runner.resolve_api_key(None, "https://worker.tail.ts.net:443/other") == "sekrit"
    assert runner.resolve_api_key(None, "http://worker.tail.ts.net:9999/v1") == "not-needed"
    assert runner.resolve_api_key(None, "http://worker.tail.ts.net/v1") == "not-needed"
    assert runner.resolve_api_key(None, "https://worker.tail.ts.net:8443/v1") == "not-needed"


def test_configured_local_endpoints_get_key(monkeypatch):
    # hydra (CONSUMER_BASE / AGENT_INFERENCE_URL) and a non-default local
    # INFERENCE_URL, both exported by scripts/lib/conf.sh.
    monkeypatch.setenv("OPENBEAST_CONSUMER_BASE", "http://127.0.0.1:8090")
    monkeypatch.setenv("OPENBEAST_INFERENCE_URL", "http://127.0.0.1:8081")
    assert runner.resolve_api_key(None, "http://localhost:8090/v1") == "sekrit"
    assert runner.resolve_api_key(None, "http://127.0.0.1:8081/v1") == "sekrit"
    assert runner.resolve_api_key(None, "http://127.0.0.1:8082/v1") == "not-needed"


def test_explicit_key_still_honored():
    assert runner.resolve_api_key("flag", "http://127.0.0.1:4444/v1") == "flag"
