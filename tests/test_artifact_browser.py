#!/usr/bin/env python3
"""beast-artifact in a REAL headless browser (artifact-browser-11).

The four UI defects of the 2026-09-30 review — a gallery filter that hid
nothing, a theme toggle that could not reach the frame, an external link that
blanked the artifact, runtime template compilers refused by CSP — all passed
163 header/HTML tests, because they only exist in a browser. Same lesson as
the 09-17 review. So this drives Chromium over the DevTools pipe
(tests/_cdp_pipe.py: no websocket dependency, no fixed port) against a live
server behind a `tailscale serve` stand-in (tests/_artifact_live.py).

SKIPS cleanly when no chromium/chrome binary is found (CI's ubuntu runner has
google-chrome; set CHROME_BIN or OPENBEAST_TEST_CHROME to point elsewhere). The same fixes are pinned
at the DOM/unit level in tests/test_artifact_review_0930.py, which always
runs.

The framed artifact lives in an opaque origin, so it reports what it sees by
postMessage to the shell; a DevTools-injected listener collects that (DevTools
evaluation is exempt from the page's CSP — the page's own scripts are not).

Run: pytest tests/test_artifact_browser.py
"""
import os
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "agents"))

import _cdp_pipe  # noqa: E402

CHROME = _cdp_pipe.find_chrome()
pytestmark = pytest.mark.skipif(CHROME is None,
                                reason="no chromium/chrome binary found")

MAX = "max@example.com"

# Reports to the shell: its background, whether a supporting image loaded,
# and whether a runtime-compiled function runs (what Alpine/Vue need).
PROBE = """<title>probe</title>
<style>
:root{--bg:#ffffff;--fg:#111111}
@media (prefers-color-scheme: dark){:root{--bg:#101418;--fg:#eeeeee}}
body{background:var(--bg);color:var(--fg)}
</style>
<img id="im" src="dot.png" alt="">
<a id="ext" href="http://127.0.0.2:9/elsewhere">a source</a>
<script>
function report(tag){
  var i = document.getElementById('im');
  var ev; try { ev = new Function('return 6*7')(); } catch (e) { ev = 'ERR ' + e.name; }
  parent.postMessage({tag: tag, bg: getComputedStyle(document.body).backgroundColor,
                      img: i ? i.naturalWidth : -1, ev: ev,
                      q: location.search}, '*');
}
window.addEventListener('load', function(){ report('load'); });
// The scheme a sandboxed (out-of-process) frame sees follows the parent
// iframe's color-scheme, which can land AFTER 'load'; media queries are live,
// so report again when it settles instead of trusting the first paint.
try {
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change',
    function(){ report('scheme'); });
} catch (e) {}
</script>
"""

CLICKER = """<title>clicker</title>
<a id="ext" href="http://127.0.0.2:9/elsewhere">a source</a>
<script>
window.addEventListener('load', function(){
  setTimeout(function(){ document.getElementById('ext').click(); }, 200);
  setTimeout(function(){ parent.postMessage({tag: 'still-here'}, '*'); }, 1500);
});
</script>
"""

# 1x1 PNG
DOT = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0000003010100c9fe92ef"
    "0000000049454e44ae426082")


@pytest.fixture()
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("OPENBEAST_ARTIFACT_BASE_URL", "https://beast:8446")
    monkeypatch.setenv("OPENBEAST_CONF", str(tmp_path / "absent.conf"))
    monkeypatch.setenv("OPENBEAST_CHAT_BASE_URL", "off")
    for var in ("OPENBEAST_ARTIFACT_OPERATORS", "OPENBEAST_CHAT_OPERATORS",
                "OPENBEAST_ARTIFACT_ADMINS", "OPENBEAST_BIND"):
        monkeypatch.delenv(var, raising=False)
    import _artifact_live
    import artifact
    with _artifact_live.LiveServer() as srv, \
            _artifact_live.IdentityProxy(srv.port, MAX) as proxy, \
            _cdp_pipe.Chrome(CHROME) as browser:
        def pub(html, **kw):
            token = artifact.set_owner_override(MAX)
            try:
                return artifact.publish(html, **kw)["id"]
            finally:
                artifact.reset_owner_override(token)
        yield {"base": f"http://127.0.0.1:{proxy.port}", "pub": pub,
               "browser": browser, "store": artifact, "srv": srv}


def _wait_msg(page, pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for m in page.messages():
            if isinstance(m, dict) and pred(m):
                return m
        time.sleep(0.1)
    return None


def test_gallery_filter_hides_non_matching_rows(live):
    """artifact-browser-5: every row stayed visible whatever was typed."""
    for t in ("alpha report", "beta numbers", "gamma notes"):
        live["pub"](f"<title>{t}</title><p>x</p>")
    page = live["browser"].new_page()
    page.goto(live["base"] + "/")
    visible = ("Array.from(document.querySelectorAll('#list > *'))"
               ".filter(function(r){return getComputedStyle(r).display!=='none'})"
               ".length")
    assert page.eval(visible) == 3
    page.eval("var f=document.getElementById('filter'); f.value='beta';"
              "f.dispatchEvent(new Event('input'))")
    assert page.eval(visible) == 1
    assert page.eval("getComputedStyle(document.getElementById('nomatch'))"
                     ".display") == "none"
    page.eval("var f=document.getElementById('filter'); f.value='zzzz';"
              "f.dispatchEvent(new Event('input'))")
    assert page.eval(visible) == 0
    assert page.eval("getComputedStyle(document.getElementById('nomatch'))"
                     ".display") == "block"


def test_the_shell_runs_its_own_script_and_nothing_else(live):
    """security-3: the shell's script-src is its own hash. Its script must
    still run (the theme button is painted, the owner's Manage button shows)
    while a model-authored same-origin .js file is refused."""
    aid = live["pub"](PROBE, files={"dot.png": DOT,
                                    "x.js": "window.__modeljs = 1;"})
    page = live["browser"].new_page()
    page.goto(f"{live['base']}/a/{aid}")
    assert page.eval("document.getElementById('theme')"
                     ".getAttribute('data-mode')") in ("light", "dark")
    assert page.eval("document.getElementById('manage').hidden") is False
    page.eval("var s=document.createElement('script');"
              f"s.src='/raw/{aid}/v/1/x.js'; document.head.appendChild(s)")
    time.sleep(1.0)
    assert page.eval("typeof window.__modeljs") == "undefined"


def test_supporting_files_load_and_eval_is_allowed_in_the_frame(live):
    """browser-4: new Function / eval — what Alpine.js and Vue's in-DOM
    templates run on — threw EvalError, so those pages rendered nothing."""
    aid = live["pub"](PROBE, files={"dot.png": DOT})
    page = live["browser"].new_page()
    page.goto(f"{live['base']}/a/{aid}")
    m = _wait_msg(page, lambda m: m.get("tag") == "load")
    assert m, "the framed page never reported"
    assert m["ev"] == 42, m
    assert m["img"] == 1, m


def test_the_theme_toggle_reaches_the_frame_on_a_dark_device(live):
    """browser-2: on a dark device, toggling to light turned the shell light
    and left the artifact dark."""
    aid = live["pub"](PROBE, files={"dot.png": DOT})
    page = live["browser"].new_page(scheme="dark")
    page.goto(f"{live['base']}/a/{aid}")
    first = _wait_msg(page, lambda m: m.get("tag") in ("load", "scheme")
                      and m.get("bg") == "rgb(16, 20, 24)")
    assert first, "the framed page never reported dark on a dark device"
    page.eval("document.getElementById('theme').click()")
    served = _wait_msg(page, lambda m: m.get("tag") == "load"
                       and "theme=light" in (m.get("q") or ""))
    assert served, "the frame was not re-served with the chosen theme"
    # Wait for the scheme to SETTLE light rather than asserting on the first
    # load report (it can precede the parent's color-scheme; 1 in 4 flaked).
    # On the old code the frame stays dark, so this never arrives.
    light = _wait_msg(page, lambda m: m.get("tag") in ("load", "scheme")
                      and "theme=light" in (m.get("q") or "")
                      and m.get("bg") == "rgb(255, 255, 255)")
    assert light, ("the frame never turned light: "
                   f"{[m for m in page.messages() if isinstance(m, dict)]}")


def test_an_external_link_does_not_blank_the_artifact(live, monkeypatch):
    """browser-3: a plain off-site link navigated the FRAME into the shell's
    frame-src 'self' wall — 'This content is blocked' in place of the page."""
    aid = live["pub"](CLICKER)
    page = live["browser"].new_page()
    page.goto(f"{live['base']}/a/{aid}")
    assert _wait_msg(page, lambda m: m.get("tag") == "still-here"), \
        "the frame navigated away from the artifact"
    # negative control: without the served link guard the frame is lost.
    # A FRESH id: /raw/ is cacheable, so the same URL would come back with
    # the guard still in it.
    monkeypatch.setattr(live["store"], "LINK_GUARD", b"")
    bare = live["pub"](CLICKER)
    page2 = live["browser"].new_page()
    page2.goto(f"{live['base']}/a/{bare}")
    assert _wait_msg(page2, lambda m: m.get("tag") == "still-here",
                     timeout=4) is None
