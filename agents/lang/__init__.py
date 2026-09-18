"""beast-lang — the offline language library (docs/BEAST_LANG_PLAN.md).

L1 of the design: the INSTALLED toolchain is the ground truth. Documents say
what a language says; the compiler says what will compile, and only the second
one matters to an agent about to write a file.

THE FACADE. `safe_pack` and `safe_escalation` are the two calls a serving path
should make (`safe_reference` and `safe_languages` are the same thing for the
PULL surface, the `language_reference` tool), and they carry the two promises
nothing underneath them can:

  * THEY DO NOT RAISE. Everything below reads JSON a person can corrupt, opens
    fixture files an install can lack, and runs compilers that can hang or be
    absent. A language note is an optional extra on somebody's turn; it is
    never worth that turn. Any failure is "" — nothing to say.
  * THEY ARE SILENT UNDER EVAL. evals/run_eval.py sets OPENBEAST_EVAL in every
    child, and an eval unit is a measurement: text that appears in its context
    because a toolchain happened to be installed changes the number without
    changing the cache key. So under OPENBEAST_EVAL both return "" unless
    OPENBEAST_LANG_IN_EVAL=1 says the run is measuring beast-lang itself.

Importing this package stays free — no subprocess, no file I/O, no submodule
import. The submodules are pulled in inside the functions, on first use.
"""
from __future__ import annotations

import os

__all__ = ["safe_escalation", "safe_languages", "safe_pack", "safe_reference"]


def _silenced() -> bool:
    return (bool(os.environ.get("OPENBEAST_EVAL"))
            and os.environ.get("OPENBEAST_LANG_IN_EVAL") != "1")


def safe_pack(lang: str) -> str:
    """The pack text this rig would hand a model for `lang`, or ""."""
    if _silenced():
        return ""
    try:
        from . import packs                      # noqa: PLC0415
        pack = packs.pack_for(lang)
        return pack.text if pack else ""
    except Exception:                            # noqa: BLE001
        return ""


def safe_escalation(lang: str, diagnostic: str) -> str:
    """The card block for a compiler diagnostic in `lang`, or ""."""
    if _silenced():
        return ""
    try:
        from . import escalate                   # noqa: PLC0415
        return escalate.render_escalation(lang, diagnostic) or ""
    except Exception:                            # noqa: BLE001
        return ""


def safe_reference(lang: str, topic: str) -> str:
    """The VERIFIED / GENERATED lines that answer `topic` in `lang`, or ""."""
    if _silenced():
        return ""
    try:
        from . import reference                  # noqa: PLC0415
        return reference.topic_reference(lang, topic) or ""
    except Exception:                            # noqa: BLE001
        return ""


def safe_languages() -> list[str]:
    """The languages this rig will actually answer for, or []. What a caller
    shows when it was asked about a language it cannot serve — a list read
    off the allow list and the installed toolchains, never a hardcoded one."""
    if _silenced():
        return []
    try:
        from . import reference                  # noqa: PLC0415
        return list(reference.served_languages())
    except Exception:                            # noqa: BLE001
        return []
