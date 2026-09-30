"""beast-instinct: a decision plane (docs/BEAST_INSTINCT.md).

Named, typed questions (yes_no | choice | score | rank) answered with a label
distribution, label_mass, a calibrated confidence and an explicit
act | review | abstain | fallback. Two rules hold everywhere:

  * instinct never grants anything — it can only skip work, or reorder or
    narrow options the deterministic layer already made eligible;
  * nothing is enforced until its quality is measured on our data — promotion
    is a gate record written by evals/decisions/run.py, never a config toggle.

Public API (lazy, so `from instinct import client` pulls in no engine code):
  decide(decision, inputs, ...) -> Verdict   fail-open client call
  render(spec, inputs, items=None)          the exact prompt an engine sees
  Verdict                                    what callers act on (enforce)
"""
from __future__ import annotations

__all__ = ["decide", "render", "Verdict", "gate"]


def __getattr__(name: str):
    if name in ("decide", "Verdict", "gate"):
        from . import client
        return getattr(client, name)
    if name == "render":
        from .render import render
        return render
    raise AttributeError(name)
