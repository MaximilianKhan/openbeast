# hydra.task_class — labelling rules (scaffold, no rows yet)

**Status: SCAFFOLD.** No rows. `hydra.task_class` stays in shadow until this
set exists, is human-labelled, and passes its gate (P3 — needs hydra and a GPU
scorer).

## The question

Which workload class is this request? Labels (answer-boundary letters):

| label | letter | means |
|---|---|---|
| `chat` | A | conversational question or answer, no tool loop |
| `code_agent` | B | an agentic coding turn that drives tools (OpenCode, runner) |
| `long_context` | C | reading or reasoning over a very long input — **mechanical** |
| `vision` | D | the request carries images — **mechanical** |
| `bulk` | E | non-interactive batch work (`stream = false`, `client_class = "batch"`) |

`vision` and `long_context` are computed from the request facts by the
`rules` engine (`has_images`; `est_prompt_tokens > rules.long_context_tokens`,
32,000). They are never left to a model: label rows with those facts so the
mechanical path is exercised, but a model's own vote for them is zeroed.

## Row format

```json
{"id": "...", "input": {"prompt_head": "...", "est_prompt_tokens": 1234,
  "has_images": false, "has_tools": true, "stream": true, "client_class": "agent"},
 "label": "code_agent", "source": "...", "group": "...", "labeller": "...",
 "added_at": "YYYY-MM-DD", "note": "..."}
```

## Seed sources (plan §5.8)

- v4 eval task **prompts** (never outcomes) → `code_agent`;
- WebUI and OpenCode samples → `chat` / `code_agent`;
- long-context and vision fixtures → `long_context` / `vision`;
- hydra's own audit log (request → deployment → outcome) once it exists.

`test` and `ood` never contain synthetic rows, and a `group` never spans two
splits — `evals/decisions/run.py` enforces both.
