"""evals/audit/basalt_log.py: speed, draft acceptance and the #588 count of one
arm from a saved Basalt server log (2026-11-24, arms I_T2_s1 and I_T2_stock)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals" / "audit"))

import basalt_log  # noqa: E402

LOG = """\
2026-11-24T09:56:46.095595534Z [server] inkling-2-mini  ctx=262144 kv=fp8 spec=8 temp=1.0
2026-11-24T09:56:52.821639770Z [server] s1 done  pp=21/0c 1.1s  tg=63 78.0t/s acc=55% dl=2.0  ttft=1.7s  end=stop  hit=100.0%  pcie=0.0%
2026-11-24T09:56:57.110873938Z [server] thinking budget 6144 tokens, output cap 8192 (answer-room auto)
2026-11-24T09:56:58.567970526Z [server] live | s1 tg=515/8192 551.1t/s | total 551t/s
2026-11-24T09:57:05.732187455Z [server] s1 done  pp=4734/4620c 0.1s  tg=3630 450.0t/s acc=74% dl=3.1  ttft=0.1s  end=stop  hit=100.0%  pcie=0.0%
2026-11-24T09:57:30.000000000Z [server] warning: max tokens reached while thinking, no answer (set reasoning_budget_tokens, or start with --reasoning-budget-tokens)
2026-11-24T09:57:30.100000000Z [server] s1 done  pp=9000/4734c 0.4s 10665t/s  tg=8192 512.0t/s acc=90% dl=5.0  ttft=0.4s  end=length  hit=100.0%  pcie=0.0%
2026-11-24T09:57:31.000000000Z [server] s1 done  pp=9100/9000c 0.0s  tg=12 300.0t/s  ttft=0.0s  end=stop  hit=100.0%  pcie=0.0%
2026-11-24T14:07:28.290741979Z [server] s1 done  pp=6750/6396c 0.1s  tg=360 450.0t/s acc=80% dl=3.0  ttft=0.1s  end=stop  hit=100.0%  pcie=0.0%
2026-11-24T14:07:29.500000000Z [server] s1 done  pp=21/0c 1.0s  tg=40 60.0t/s acc=50% dl=2.0  ttft=1.0s  end=stop  hit=100.0%  pcie=0.0%
"""


def _run(tmp_path, *args):
    p = tmp_path / "basalt.log"
    p.write_text(LOG)
    out = tmp_path / "s.json"
    rc = basalt_log.main([str(p), *args, "--json", str(out)])
    return rc, json.loads(out.read_text())


def test_window_leaves_out_probes_and_takes_the_whole_end_second(tmp_path):
    # START 09:56:55 drops the 09:56:52 probe; END 14:07:28 (whole second) keeps the
    # reply at 14:07:28.29 and drops the one at 14:07:29.5.
    rc, s = _run(tmp_path, "--since", "2026-11-24T09:56:55", "--until", "2026-11-24T14:07:28")
    assert rc == 0
    assert s["requests"] == 4
    assert s["ends"] == {"stop": 3, "length": 1}
    assert s["no_answer_warnings"] == 1
    assert s["generated_tokens"] == 3630 + 8192 + 12 + 360
    assert s["requests_without_draft"] == 1


def test_rates_and_acceptance(tmp_path):
    _, s = _run(tmp_path, "--since", "2026-11-24T09:56:55", "--until", "2026-11-24T14:07:28")
    secs = 3630 / 450 + 8192 / 512 + 12 / 300 + 360 / 450
    assert s["aggregate_decode_tps"] == round((3630 + 8192 + 12 + 360) / secs, 1)
    assert s["decode_tps_replies_at_8192"] == {"n": 1, "p10": 512.0, "median": 512.0, "p90": 512.0}
    assert s["decode_tps_replies_ge_256"]["n"] == 3        # the 12-token reply is left out
    w = 3630 + 8192 + 360
    assert s["acceptance_pct"]["tg_weighted_mean"] == round((74 * 3630 + 90 * 8192 + 80 * 360) / w, 1)
    assert s["acceptance_pct"]["n"] == 3
    assert s["prefill_tps_uncached_ge_1000"]["n"] == 1     # 9000 - 4734 uncached, timed


def test_no_window_counts_everything(tmp_path):
    _, s = _run(tmp_path)
    assert s["requests"] == 6
    assert s["no_answer_warnings"] == 1
