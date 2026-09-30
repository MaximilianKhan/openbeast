"""A dependency-free Chrome DevTools Protocol client over --remote-debugging-pipe.

No websocket library, no fixed port: Chromium reads NUL-terminated JSON
commands on fd 3 and writes responses/events on fd 4. Used by the headless
browser tests, which SKIP when no chromium/chrome binary is found.

    with Chrome() as b:
        page = b.new_page(scheme="dark")
        page.goto(url)
        page.eval("document.title")
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time

# google-chrome first: on Ubuntu, `chromium` is usually the snap (see
# _usable_chrome); locally an Arch/Fedora chromium is found next.
CANDIDATES = ("google-chrome-stable", "google-chrome", "chromium",
              "chromium-browser", "chrome")


def _usable_chrome(path: str) -> bool:
    """False for snap-packaged Chromium. Snap confinement cannot inherit the
    extra pipe fds (--remote-debugging-pipe) or write DevToolsActivePort into
    a /tmp profile, so on Ubuntu runners every browser test died while
    google-chrome sat installed next to it. Ubuntu's /usr/bin/chromium-browser
    is a shell wrapper that execs the snap; skip that too."""
    real = os.path.realpath(path)
    if real.startswith("/snap/"):
        return False
    try:
        with open(real, "rb") as fh:
            head = fh.read(4096)
        if head.startswith(b"#!") and b"snap" in head:
            return False
    except OSError:
        return False
    return True


def find_chrome() -> str | None:
    """CHROME_BIN or OPENBEAST_TEST_CHROME (either, for every browser suite),
    else the first usable binary on PATH; None when there is none."""
    for var in ("CHROME_BIN", "OPENBEAST_TEST_CHROME"):
        env = os.environ.get(var, "").strip()
        if env and os.access(env, os.X_OK):
            return env
    for name in CANDIDATES:
        path = shutil.which(name)
        if path and _usable_chrome(path):
            return path
    return None


class Chrome:
    def __init__(self, binary: str | None = None):
        self.binary = binary or find_chrome()
        if not self.binary:
            raise RuntimeError("no chromium/chrome binary")
        self.udd = tempfile.mkdtemp(prefix="ob-cdp-")
        cmd_r, self._cmd_w = os.pipe()          # we write, chrome reads fd 3
        self._out_r, out_w = os.pipe()          # chrome writes fd 4, we read
        args = [self.binary, "--headless=new", "--remote-debugging-pipe",
                f"--user-data-dir={self.udd}", "--no-first-run",
                "--no-default-browser-check", "--disable-gpu",
                "--disable-extensions", "--disable-background-networking",
                "--hide-scrollbars", "--mute-audio", "about:blank"]
        # Chrome's sandbox needs unprivileged user namespaces. Root can't use
        # it at all, and Ubuntu 24.04 CI runners (GitHub Actions) block the
        # namespaces through AppArmor, so there the browser dies on its first
        # CDP command (Target.createTarget timeout, then a broken pipe). On a
        # desktop the sandbox stays on. OPENBEAST_CHROME_NO_SANDBOX=1 forces
        # it off for other containers.
        if (os.geteuid() == 0 or os.environ.get("GITHUB_ACTIONS") == "true"
                or os.environ.get("CI") == "true"
                or os.environ.get("OPENBEAST_CHROME_NO_SANDBOX") == "1"):
            args[1:1] = ["--no-sandbox", "--disable-dev-shm-usage"]
        # sh re-plumbs the two pipe ends onto fds 3 and 4 and execs chrome.
        script = (f'exec "$0" "$@" 3<&{cmd_r} 4>&{out_w} '
                  f'{cmd_r}<&- {out_w}>&-')
        wrapper = []
        if shutil.which("nice"):
            wrapper += ["nice", "-n", "19"]
        if shutil.which("ionice"):
            wrapper += ["ionice", "-c3"]
        # bash, not sh: the pipe ends are usually fds >= 10, and dash (Ubuntu's
        # /bin/sh) rejects multi-digit fds in a redirection ("3<&10: Bad fd
        # number"), so chrome never got its pipes and every browser test in
        # CI timed out on Target.createTarget. Arch's /bin/sh is bash, which
        # is why it only failed there.
        shell = shutil.which("bash") or "sh"
        self.proc = subprocess.Popen(
            wrapper + [shell, "-c", script] + args,
            pass_fds=(cmd_r, out_w), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(cmd_r)
        os.close(out_w)
        self._id = 0
        self._lock = threading.Lock()
        self._waiters: dict = {}
        self.events: "queue.Queue" = queue.Queue()
        self.sessions: dict = {}                 # sessionId -> targetInfo
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    # -- plumbing -------------------------------------------------------------

    def _read(self):
        buf = b""
        while True:
            try:
                chunk = os.read(self._out_r, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\0" in buf:
                raw, buf = buf.split(b"\0", 1)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if "id" in msg:
                    with self._lock:
                        q = self._waiters.pop(msg["id"], None)
                    if q is not None:
                        q.put(msg)
                else:
                    if msg.get("method") == "Target.attachedToTarget":
                        p = msg["params"]
                        self.sessions[p["sessionId"]] = p["targetInfo"]
                    self.events.put(msg)

    def send(self, method, params=None, session=None, timeout=30):
        with self._lock:
            self._id += 1
            mid = self._id
            q: "queue.Queue" = queue.Queue()
            self._waiters[mid] = q
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        os.write(self._cmd_w, json.dumps(msg).encode() + b"\0")
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(method)
        if "error" in resp:
            raise RuntimeError(f"{method}: {resp['error']}")
        return resp.get("result", {})

    def new_page(self, width=390, height=844, scheme="light"):
        tid = self.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        sid = self.send("Target.attachToTarget",
                        {"targetId": tid, "flatten": True})["sessionId"]
        return Page(self, sid, tid, width, height, scheme)

    def close(self):
        try:
            self.send("Browser.close", timeout=5)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for fd in (self._cmd_w, self._out_r):
            try:
                os.close(fd)
            except OSError:
                pass
        shutil.rmtree(self.udd, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Page:
    def __init__(self, b: Chrome, sid, tid, width, height, scheme):
        self.b, self.sid, self.tid = b, sid, tid
        for m in ("Page.enable", "Runtime.enable"):
            b.send(m, session=sid)
        b.send("Emulation.setDeviceMetricsOverride",
               {"width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": width < 600}, session=sid)
        b.send("Emulation.setEmulatedMedia",
               {"features": [{"name": "prefers-color-scheme",
                              "value": scheme}]}, session=sid)
        # Collect what framed pages post to their parent. DevTools-injected
        # script is not subject to the page's CSP, which is the point: the
        # page's OWN scripts still are.
        b.send("Page.addScriptToEvaluateOnNewDocument", {"source": (
            "if (window === window.top) { window.__msgs = [];"
            " addEventListener('message', function (e) {"
            " window.__msgs.push(e.data); }); }")}, session=sid)

    def goto(self, url, settle=1.5):
        self.b.send("Page.navigate", {"url": url}, session=self.sid)
        self.wait_for("document.readyState === 'complete'", timeout=15)
        time.sleep(settle)

    def eval(self, expr, timeout=15):
        r = self.b.send("Runtime.evaluate",
                        {"expression": expr, "returnByValue": True,
                         "awaitPromise": True}, session=self.sid,
                        timeout=timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:500])
        return r.get("result", {}).get("value")

    def wait_for(self, expr, timeout=10.0, interval=0.1):
        end = time.monotonic() + timeout
        last = None
        while time.monotonic() < end:
            try:
                last = self.eval(expr)
            except RuntimeError:
                last = None
            if last:
                return last
            time.sleep(interval)
        return last

    def messages(self):
        return self.eval("window.__msgs || []") or []
