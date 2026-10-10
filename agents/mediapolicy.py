"""The inline-media rule beast-gate and the agent router share.

With an mmproj loaded (the shipped vision serve scripts), llama-server goes
and gets whatever a media part names: its handle_media() (tools/server/
server-common.cpp) downloads anything that starts with "http" from the rig's
own loopback, opens "file://" under --media-path, and otherwise decodes the
value in place — a "data:" URI, or raw base64, which is how OpenAI's
input_audio.data arrives. For a caller who is not the operator that download
is SSRF: `http://127.0.0.1:3000/…`, a LAN or metadata address, with success
versus "Failed to download image" as the oracle and the model describing
whatever decodes. tools.fetch's SSRF guard is not on this path.

So both proxies in front of llama-server forward INLINE media only, and both
ask this module what inline means. They were written separately the first
time (2026-10-09 review, netsec S4) and disagreed at the edges — which
spelling of "data:" counts, whether a part is found by its `type` or by its
key, whether a bare string under input_audio is looked at. One rule, the
stricter reading of each:

  inline    a "data:" URI (scheme matched without regard to case, as a URI
            scheme is — but at the very start: leading whitespace is not a
            data: URI), or a value with no ":" in it that does not start with
            "http" — the base64 alphabet has no colon
  fetchable everything else: any other scheme, and a scheme-less
            "httpbin.org/x", because "http" is llama-server's whole test

Media is found by KEY, anywhere in the body, not only as a typed part under
messages[].content[]: /v1/responses carries the same parts under `input`,
/v1/messages nests Anthropic image blocks inside tool results, and all of
them end in the same download. A proxy that allowlists one route today still
gets the rule that covers the route it adds tomorrow.

Pure functions, no imports: nothing here can fail a request by itself.
"""

from __future__ import annotations

# Keys whose value is a media reference, as llama-server reads them:
#   image_url: {"url": ...}        or, on /v1/responses, image_url: "..."
#   input_audio / input_video: {"data": ... | "url": ...}
# `data` is read first upstream and `url` is the fallback; both are judged —
# never trust which one wins.
_MEDIA_KEYS = {
    "image_url": ("url",),
    "input_audio": ("data", "url"),
    "input_video": ("data", "url"),
}


def fetchable(value) -> bool:
    """True when llama-server would go and GET (or open) this media value."""
    if not isinstance(value, str):
        return False
    if value[:5].lower() == "data:":
        return False
    return ":" in value or value.lstrip()[:4].lower() == "http"


def remote_media(body) -> str | None:
    """The key of the first media part that names a URL instead of carrying
    its data ("image_url", "input_audio", "input_video", or "source" for an
    Anthropic image block), else None.

    Iterative: the body's depth is the caller's to choose, and a recursive
    walk would hand them the stack.
    """
    stack = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        for key, val in node.items():
            fields = _MEDIA_KEYS.get(key) if isinstance(key, str) else None
            if fields:
                found = ([val.get(f) for f in fields] if isinstance(val, dict)
                         else [val])
            elif (key == "source" and isinstance(val, dict)
                  and node.get("type") == "image"):
                # {"type": "image", "source": {"type": "url", "url": ...}}
                found = [val.get("url")]
            else:
                found = ()
            if any(fetchable(f) for f in found):
                return key
            stack.append(val)
    return None
