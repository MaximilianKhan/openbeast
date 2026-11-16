#!/usr/bin/env python3
"""docs/BEAST_SLOT.md's published-port table against setup-tailscale.sh.

Operators audit their tailnet exposure from that table, and in the
2026-11-16 review it disagreed with the code: :443 read "(always)" though the
script publishes it only behind an enforced login (or the open
acknowledgement) and otherwise takes it down; :8445 named only TCP :3003
though CHAT_LOGIN_FROM=unix mounts the socket; the raw :8443 named a fixed
llama-server :8080. Each row is pinned to the code path it describes.

Run: pytest tests/test_beast_slot_ports_doc.py
"""

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = open(os.path.join(REPO, "docs", "BEAST_SLOT.md")).read()
SRC = open(os.path.join(REPO, "scripts", "setup-tailscale.sh")).read()


def _row(port: str) -> str:
    m = re.search(r"^\| `" + re.escape(port) + r"` \|.*$", DOC, re.M)
    assert m, f"no {port} row in the BEAST_SLOT.md port table"
    return m.group(0)


def test_443_row_is_conditional_like_the_script():
    row = _row(":443")
    assert "always" not in row, row
    assert "--i-accept-open-webui" in row and "login" in row, row
    # the script really does take :443 down when it will not publish it
    assert "serve --https=443 off" in SRC


def test_8445_row_names_the_unix_socket_mode():
    row = _row(":8445")
    assert "CHAT_LOGIN_FROM=unix" in row and "CHAT_SOCKET" in row, row
    assert '"unix:$CHAT_SOCKET"' in SRC


def test_8443_row_follows_inference_url():
    row = _row(":8443")
    assert "INFERENCE_URL" in row, row
    assert 'serve --bg --https=8443 "$RAW_TARGET"' in SRC


def test_webui_auth_line_shows_conf_and_effective_values():
    """conf.sh forces login on under a live :443 mount; the script used to
    print only the effective value as if the conf said it."""
    assert "openbeast.conf says" in SRC and "effective:" in SRC
    assert "keeps login" in DOC and "**on** whatever the file says" in DOC
