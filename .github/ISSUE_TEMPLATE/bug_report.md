---
name: Bug report
about: Something broken in the stack, tools, evals, or scripts
labels: bug
---

**What happened**

**What you expected**

**Repro steps**

1.

**Environment**
- GPU + VRAM (e.g. RTX 5090 32 GB), or the DGX Spark pair:
- Distro / kernel:
- Install path: `bootstrap.sh` full / `--minimal` / manual
- `./start.sh --status` output:
- `./scripts/healthcheck.sh` output:
- On the Spark pair: `beaststatus` output (on spark-1)

**Logs**
Attach the relevant tail of `.run/stack.log` (daemon mode) or terminal
output (foreground), and `agents/logs/agent-*.jsonl` for agent issues.
