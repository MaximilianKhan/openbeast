#!/usr/bin/env python3
"""beast-chat console — checks that need no browser, so they ALWAYS run.

Two layers:
  * static properties of agents/chat_ui/console.html that the review's
    browser findings reduce to (no innerHTML anywhere, [hidden] wins, the
    composer cap matches what an agent receives, scroll is coalesced, the
    stream is read with fetch — not EventSource, which cannot carry a key);
  * the page's pure helpers (the SSE parser, the linkifier, the steer label)
    executed in node when it is installed — they are plain functions in their
    own <script> block, so no DOM is needed. Skips without node.

The same behaviours are exercised end-to-end in Chromium by
tests/test_chat_console_browser.py when a browser is available.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSOLE = os.path.join(REPO, "agents", "chat_ui", "console.html")
sys.path.insert(0, os.path.join(REPO, "agents"))

import sessions  # noqa: E402


@pytest.fixture(scope="module")
def html():
    with open(CONSOLE, encoding="utf-8") as f:
        return f.read()


def _scripts(html):
    return re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)


def _css(html):
    return re.search(r"<style>(.*?)</style>", html, re.S).group(1)


def test_no_transcript_text_can_become_markup(html):
    # CODE, not the comments that explain the rule.
    code = re.sub(r"(?m)^\s*//.*$", "", "\n".join(_scripts(html)))
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                 "document.write", "eval(", "new Function"):
        assert sink not in code, sink


def test_hidden_beats_every_display_rule(html):
    """chat-browser-2."""
    assert re.search(r"\[hidden\]\{display:none !important\}", _css(html))


def test_the_composer_cap_is_what_an_agent_receives(html):
    """chat-security-1: maxlength and the JS cap equal sessions.OP_MAX_TEXT."""
    assert f'maxlength="{sessions.OP_MAX_TEXT}"' in html
    assert re.search(rf"var MAX_MSG = {sessions.OP_MAX_TEXT};", html)


def test_the_stream_is_read_with_fetch_not_eventsource(html):
    """chat-security-3 / chat-security-4."""
    code = "\n".join(_scripts(html))
    assert "new EventSource" not in code
    assert "getReader()" in code and "X-OpenBeast-Device-Key" in code


def test_scroll_is_coalesced_per_frame(html):
    """chat-lifecycle-console-scroll-thrash."""
    body = re.search(r"function scroll\(\)\{(.*?)\n\}", html, re.S).group(1)
    assert "requestAnimationFrame" in body


def test_fresh_opens_use_the_tail(html):
    assert re.search(r"openStream\(0, \{ tail: TAIL_BYTES \}\)", html)


def test_enter_sends(html):
    """chat-browser-8: no Ctrl/Cmd requirement."""
    m = re.search(r'ta\.addEventListener\("keydown", function\(e\)\{(.*?)\}\);',
                  html, re.S)
    assert m and "metaKey" not in m.group(1) and "shiftKey" in m.group(1)


def test_new_session_goes_through_a_server_dry_run(html):
    """F-C1: the confirm dialog shows the SERVER's argv."""
    assert "dry.dry_run = true" in html and "#csArgv" in html


# ---------------------------------------------------------------------------
# The pure helpers, run in node
# ---------------------------------------------------------------------------

NODE = shutil.which("node")


def _run_pure(html, js_body):
    pure = re.search(r'<script id="bc-pure">(.*?)</script>', html, re.S).group(1)
    prog = pure + "\n;(function(){\n" + js_body + "\n})();"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_sse_parser_handles_split_frames_crlf_and_an_error_event(html):
    got = _run_pure(html, r'''
      var p = BCPure.sseParser(), out = [];
      out = out.concat(p.push("retry: 2000\n\n: hb 1\n\nid: 10\nevent: hel"));
      out = out.concat(p.push("lo\ndata: {\"a\":1}\n\nid: 20\r\nevent: error\r\ndata: {\"e\":2}\r\n\r\n"));
      out = out.concat(p.push("event: log\ndata: x\ndata: y\n\n"));
      console.log(JSON.stringify(out));''')
    assert got == [{"id": "10", "event": "hello", "data": '{"a":1}'},
                   {"id": "20", "event": "error", "data": '{"e":2}'},
                   {"id": None, "event": "log", "data": "x\ny"}]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_link_tokens_only_link_https_and_artifact_pages(html):
    art = "http://127.0.0.1:3004/a/0f8fad5b-d9cb-469f-a165-70867728950e/v/2"
    got = _run_pure(html, f'''
      console.log(JSON.stringify([
        BCPure.linkTokens("see https://x.example/a?b=1. ok"),
        BCPure.linkTokens("{art} and http://plain.example/x"),
        BCPure.linkTokens("javascript:alert(1) data:text/html,<b>x</b>"),
        BCPure.linkTokens("(https://y.example/p)")]));''')
    assert got[0] == [{"t": "text", "v": "see "},
                      {"t": "link", "v": "https://x.example/a?b=1",
                       "href": "https://x.example/a?b=1", "art": False},
                      {"t": "text", "v": ". ok"}]
    assert got[1][0] == {"t": "link", "v": art, "href": art, "art": True}
    assert all(t["t"] == "text" for t in got[1][1:])
    assert all(t["t"] == "text" for t in got[2])
    assert got[3][1]["href"] == "https://y.example/p"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_steer_label_names_the_sender(html):
    got = _run_pure(html, '''
      console.log(JSON.stringify([
        BCPure.steerLabel("max@x.com (phone)", "max@x.com"),
        BCPure.steerLabel("max@x.com", "max@x.com"),
        BCPure.steerLabel("eve@x.com (laptop)", "max@x.com"),
        BCPure.steerLabel("", "max@x.com")]));''')
    assert got == ["you → agent", "you → agent", "eve@x.com (laptop) → agent",
                   "operator → agent"]
