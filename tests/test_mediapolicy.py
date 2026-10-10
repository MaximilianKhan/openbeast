"""agents/mediapolicy.py — the inline-media rule beast-gate and the agent
router share (2026-10-09 review, netsec S4, integration pass).

The two proxies each wrote their own version of this rule and disagreed at
the edges: the router matched `data:` in any case and after leading
whitespace and found media by key anywhere in the body; the gate matched
`data:` exactly and only looked at typed parts under messages[].content[];
only the gate looked at a bare string under input_audio. Neither difference
was a way to make llama-server fetch something, but two rules drift. These
tests pin the one rule and that both proxies use the same function object.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

import mediapolicy  # noqa: E402

FETCHABLE = [
    "http://127.0.0.1:3000/api/config",
    "https://169.254.169.254/latest/meta-data",
    "HTTP://10.0.0.1/x.png",
    "  http://10.0.0.1/x.png",
    "file:///etc/passwd",
    "ftp://10.0.0.1/x",
    "httpbin.org/image/png",        # no scheme: llama-server tests "http" only
    "httpd",
    " httpd",
    " data:image/png;base64,AAAA",  # not a data: URI — it does not start with one
    "javascript:alert(1)",
]
INLINE = [
    "data:image/png;base64,iVBORw0KGgo=",
    "DATA:image/png;base64,iVBORw0KGgo=",   # a URI scheme has no case
    "data:video/mp4;base64,AAAA",
    "iVBORw0KGgo/+A==",                      # raw base64 (OpenAI input_audio.data)
    "UklGRiQAAABXQVZF",
    "UklGRiQA\nAABXQVZF\n",                  # wrapped base64
    "",
    None, 5, True, ["http://10.0.0.1/"], {"url": "http://10.0.0.1/"},
]


@pytest.mark.parametrize("value", FETCHABLE)
def test_anything_llama_server_could_fetch_is_fetchable(value):
    assert mediapolicy.fetchable(value) is True


@pytest.mark.parametrize("value", INLINE, ids=repr)
def test_inline_data_and_non_strings_are_not(value):
    assert mediapolicy.fetchable(value) is False


URL = "http://127.0.0.1:3000/x.png"
REMOTE_BODIES = [
    # the chat shape, object and bare-string forms
    ("image_url", {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": URL}}]}]}),
    ("image_url", {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": URL}]}]}),
    ("input_audio", {"messages": [{"role": "user", "content": [
        {"type": "input_audio", "input_audio": {"data": URL, "format": "wav"}}]}]}),
    ("input_audio", {"messages": [{"role": "user", "content": [
        {"type": "input_audio", "input_audio": {"url": URL}}]}]}),
    ("input_audio", {"messages": [{"role": "user", "content": [
        {"type": "input_audio", "input_audio": URL}]}]}),
    # `data` wins upstream; `url` is judged anyway
    ("input_video", {"messages": [{"role": "user", "content": [
        {"type": "input_video", "input_video": {"data": "AAAA", "url": URL}}]}]}),
    # the same parts where the other routes put them
    ("image_url", {"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": URL}]}]}),
    ("source", {"messages": [{"role": "user", "content": [
        {"type": "tool_result", "content": [
            {"type": "image", "source": {"type": "url", "url": URL}}]}]}]}),
    # found by key, whatever the part claims to be
    ("image_url", {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "hi", "image_url": {"url": URL}}]}]}),
]
INLINE_BODIES = [
    {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "input_audio", "input_audio": {"data": "UklGRiQA", "format": "wav"}},
        {"type": "text", "text": "see http://example.com/pic.png and file:///x"}]}]},
    {"messages": [{"role": "user", "content": "see " + URL}]},
    {"messages": URL},
    {"messages": [{"role": "user", "content": [
        "str", 5, None, {"type": ["image_url"]}, {"type": "image_url"},
        {"type": "image_url", "image_url": {"url": None}}]}, "x", None]},
    # `source` is a media source only inside an image block
    {"messages": [{"role": "user", "content": "hi"}],
     "metadata": {"source": {"url": "https://example.com/a"}}},
    {"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": "iVBORw0KGgo="}}]}]},
    # a tool whose parameter happens to be called image_url is a schema
    {"tools": [{"type": "function", "function": {"name": "show", "parameters": {
        "type": "object", "properties": {"image_url": {
            "type": "string", "description": "e.g. https://example.com/a.png"}}}}}],
     "messages": [{"role": "user", "content": "hi"}]},
    {"input": "http://example.com is a string, not a media part"},
    [], "x", None,
]


@pytest.mark.parametrize("key,body", REMOTE_BODIES, ids=lambda v: str(v)[:60])
def test_a_media_part_naming_a_url_is_found_wherever_it_sits(key, body):
    assert mediapolicy.remote_media(body) == key


@pytest.mark.parametrize("body", INLINE_BODIES, ids=lambda v: str(v)[:60])
def test_bodies_with_only_inline_media_pass(body):
    assert mediapolicy.remote_media(body) is None


def test_deep_nesting_does_not_recurse():
    body = {"image_url": {"url": URL}}
    for _ in range(50_000):
        body = [body]
    assert mediapolicy.remote_media(body) == "image_url"


def test_the_gate_and_the_router_use_this_rule_and_no_other():
    """The drift guard: neither proxy carries its own copy any more. A
    re-implementation in either file fails here, which is the point."""
    import edge
    import router
    assert edge._remote_media is mediapolicy.remote_media
    assert router._remote_media is mediapolicy.remote_media
    assert router._fetchable is mediapolicy.fetchable
    for name in ("edge.py", "router.py"):
        src = (ROOT / "agents" / name).read_text()
        assert "def _remote_media" not in src and "def _fetchable" not in src, name
