# beast-instinct — research report (2026-09-30)

Research input to [`docs/BEAST_INSTINCT_PLAN.md`](../BEAST_INSTINCT_PLAN.md): the LMSYS decision-models post, Open-Jev, SGLang's scoring API at a pinned commit, and equivalents in vLLM / llama.cpp / TensorFold.

Research report: beast-instinct (decision models and scoring for OpenBeast), as of 2026-09-30

## 0. Corrections to the computed task (read first)

1. **arXiv 2512.07846 is not the JEV paper.** It is *"MixLM: High-Throughput and Effective LLM Ranking via Text-Embedding Mix-Interaction"* (LinkedIn), https://arxiv.org/abs/2512.07846. The LMSYS post links it only as "background on high-performance prefill-only decision models". It never mentions JEV.
2. **JEV is "Jev", TypeSafe AI's closed "System One model"**, released 2026-09-15 (https://typesafe.ai/blog/introducing-system-one-models-and-jev, https://www.marktechpost.com/2026/09/19/typesafe-ai-releases-jev/).
   - It is an API only: waitlist or Vercel AI Gateway. There are no weights, no parameter count and no self-hosting.
   - **Open-Jev** (Zefan Cai) is an independent open reimplementation. https://github.com/Zefan-Cai/Open-Jev
3. **SGLang has moved past the blog.** These landed on `main` after the last release, v0.5.20 (2026-09-18):
   - `/v1/decisions` (typed decisions)
   - `/v1/systemone` (a Jev-API-compatible route)
   - per-item label candidates plus temperature
   - setwise CausalLM scoring

   All of them are **in no tagged release yet**. Pin by commit: main HEAD is `3b537e96e2e676583f72d0c86558570bc7723282` (2026-09-30), verified with the GitHub API.
4. **The MIS flag is `--enable-mis`.** I verified this in `python/sglang/srt/arg_groups/fields/exec_.py` at both v0.5.20 and main. The flag PR #10979 originally used, `--multi-item-scoring-delimiter`, is gone from server_args.

---

## 1. The LMSYS post, JEV and Open-Jev

**LMSYS post** (https://www.lmsys.org/blog/2026-09-25-sglang-decision-models; LinkedIn, SGLang, NVIDIA and TikTok authors)

- **Decision LLMs** return category or score signals. The post uses the "answer boundary": the next-token distribution already exists there, so no decoding is needed.
- **Pointwise vs setwise is a semantic choice** ("what each judgment can see"):
  - Pointwise: query + one candidate, a Yes/No per candidate.
  - Setwise: all candidates in one prompt, with one or more answer boundaries.
- **SIS vs MIS is an execution choice**, independent of the above:
  - SIS treats each query+item as an independent sequence.
  - MIS packs the query plus all items into one sequence with candidate-isolating attention. It is FlashInfer-only.
- **Server recipes:**
  - SIS: `--schedule-policy lpm --chunked-prefill-size 4096 --enable-mixed-chunk --max-running-requests 256`
  - MIS: `--attention-backend flashinfer --disable-radix-cache --chunked-prefill-size -1 --enable-mis`
  - Setwise/fused baseline: `--disable-radix-cache --chunked-prefill-size -1` (FA3)
- **Numbers.** All on one H200, CUDA 13.0, Open-Jev test split, p95 per question with all candidates.
  - At concurrency 1 with 16 candidates:

    | Model | Generate | SIS | MIS |
    |---|---|---|---|
    | Qwen3-0.6B | 39.6 ms | 24.3 ms | 18.7 ms |
    | Qwen3-8B | 54.1 ms | 53.1 ms | **20.6 ms** |
    | Qwen3.5-4B | 84.8 ms | 74.4 ms | 55.7 ms |

  - MIS latency is roughly flat from 2 to 16 candidates.
  - MIS "is not consistently faster at low load". Its advantage grows with load.
  - Qwen3.5 (hybrid GDN) gains less than Qwen3.
  - Setwise vs fused-choice: "neither approach wins at every load". At 668 offered QPS on 0.6B, achieved throughput was only 182 and 167 q/s.
- **Caveats stated in the post:**
  - Serving performance only. "They do not establish equivalent decisions across prompt formulations or execution modes."
  - Softmax label scores are "not automatically calibrated correctness probabilities".
  - "One sweep per load is not a confidence interval."
  - Measure at the intended load.
  - Reproduction requires pinning model, dataset and package versions and keeping raw `samples.jsonl`.
  - Bench code: https://github.com/chuanrui/sglang-benchmark @ `1a5972c6`.
  - Open-Jev dataset revision `c67699e1`.

**Jev (TypeSafe).**
- The API is `POST /v1/systemone` with `{state, model, questions}`, using three primitives:
  - Choice: up to 255 options.
  - Score: ordered levels.
  - Noul: a yes/no statement.
- It returns probabilities plus a "confidence" computed from the shape of the distribution. Their example: p=0.84 but confidence 0.596.
- Vendor claims (not verified):
  - Latency 70–500 ms; $0.042/M input tokens.
  - "RLCD" (RL for calibrated decisions) training.
  - "Zero hallucination", which only means schema compliance.
- Their evals use GPT-6 Astra / Fable 5.1 reference probabilities as ground truth, which they admit is biased.
- Sources: https://typesafe.ai/blog/introducing-system-one-models-and-jev, https://www.marktechpost.com/2026/09/19/typesafe-ai-releases-jev/

**Open-Jev** (https://github.com/Zefan-Cai/Open-Jev, https://huggingface.co/datasets/ZefanCai/Open-Jev)

- **Models** are LoRA adapters (r8, α16) plus a trained **scalar decision head**:
  - Loss: soft cross-entropy + 0.1·Brier.
  - One temperature, fitted only on held-out calibration rows.
  - They need a custom loader, not stock PEFT or vLLM.
- **Model cards:**

  | Model | Base | Test acc | OOD acc | JevBench | Other |
  |---|---|---|---|---|---|
  | **Open-Jev-27B-v1.1** (https://huggingface.co/ZefanCai/Open-Jev-27B-v1.1) | **Qwen3.8-27B**, the same backbone as our default | 98.31% | 95.98% | 85.28% (197/231); Hard 72.07% | T=2.534 from 512 calibration rows; no latency reported |
  | Open-Jev-9B (https://huggingface.co/ZefanCai/Open-Jev-9B) | Qwen3.5-9B | 97.54% | 91.97% | 77.49% | Test NLL 0.131 / Brier 0.039 / **ECE 0.008**; OOD ECE 0.037; 4,096-token cap per candidate (rejects, does not truncate) |
  | Open-Jev-2B | — | 94.71% | 86.02% | 64.94% | — |

  - Licenses: adapters Apache-2.0, code MIT.
- **Dataset:**
  - 12 configs, 520,199 rows. `release-v2-redistributable` has 113,568 rows split into train, calibration, validation, test and OOD.
  - Languages: EN, ZH and TR.
  - Tasks: support triage, entity alignment, IR, email categorization, game state.
  - Mostly synthetic. License CC0-1.0, code MIT. Upstream question text comes from TypeSafe docs "without a verified source license".
- **`jev/api.py`:**
  - `compile_request(state, questions) -> list[dict]` turns choice (1–255), score (2–10) and noul questions into isolated records.
  - `candidate_prompts(record)` produces one Yes/No prompt per option, which is pointwise.
  - `format_response(records, probabilities)` returns choice + distribution + `choice_confidence`, and score + expected value + `score_confidence`.

**MixLM (the paper the post actually cites)**
- A 3-stage distillation from a 7B judge; items are pre-encoded into a few embedding tokens.
- NDCG@10: 0.9239 vs 0.9432 full-text.
- On H100 with SGLang at p99 < 500 ms: 22k items/s/GPU, 10× vs summarized text and 75.9× vs full text; +0.47% DAU online.
- Serving tricks: in-batch prefix caching, multi-item scoring, batched send, dropping the KV cache after prefill.

**Reusable for OpenBeast**
- Take Open-Jev's typed question schema (choice / score / noul), its confidence formulas and its **methodology**: calibrate on a held-out split, and report accuracy + NLL + Brier + ECE on test and OOD. That is exactly the "measured, not assumed" bar our eval culture needs.
- Open-Jev-27B sharing our Qwen3.8-27B backbone makes it a candidate for a "same-weights" decision head on the Sparks. It needs a custom loader, so treat it as a research item, not the first build.

## 2. SGLang details

**`/v1/score`** (source: `entrypoints/openai/protocol.py` at main; docs https://docs.sglang.io/docs/basic_usage/native_api)

- **Request:**
  ```
  query: str | list[int]            # [] for complete prompts
  items: str | list[str] | list[list[int]]
  label_token_ids: list[int] | list[list[int]]   # shared, or one list per item (PR #40826, main only)
  apply_softmax: bool = False
  temperature: float = 1.0          # >0; divides label logits, needs apply_softmax
  return_token_logprobs: bool = False
  item_first: bool = False
  return_pooled_hidden_states: bool = False
  score_extraction_token: str | None     # setwise readout at every occurrence (PR #41188, merged 2026-09-26)
  embed_override_token_id / query_embed_overrides / item_embed_overrides   # MixLM-style hybrid inputs
  model: str
  ```
- **Response:**
  - `scores` is `[num_rows][num_labels]` for pointwise, or `[num_items][N_i][num_labels]` for setwise.
  - Plus optional `pooled_hidden_states`, `token_logprobs`, `usage`, and `object:"scoring"`.
- Softmax normalizes **across labels within one item**, not across items.
- Labels must be single tokens, taken from the served checkpoint's tokenizer.
- Usage/token counts: https://github.com/sgl-project/sglang/issues/18132

**MIS constraints**, enforced in `arg_groups/attention_hook.py`:
- `--enable-mis` auto-disables CUDA graph (prefill and decode), the radix cache and chunked prefill.
- It asserts the FlashInfer attention backend.
- The original implementation (PR #10979, 2025-10-09) measured 16.2× lower P99 and +26% items/s on H100 with Qwen3-0.6B.
- `layers/attention/linear/gdn_backend.py` and `hybrid_linear_attn_backend.py` reference MIS, so Qwen3.5/3.8 hybrid GDN is covered on main (the roadmap names NVIDIA's Qwen3.5 MIS work, https://github.com/sgl-project/sglang/issues/15344).
- **Implication:** an MIS server is a **dedicated scoring server**. Without radix cache and CUDA graphs, it should not share a process with chat generation.

**Sequence classification** (PR #22118, merged 2026-04-08; https://github.com/sgl-project/sglang/pull/22118)
- Supports `Qwen3ForSequenceClassification`, `Qwen2ForSequenceClassification` and `LlamaForClassification`.
- `/v1/score` returns raw class logits with no label ids, and works with MIS.
- Also available: `/v1/classify` (reward models), `/encode`, and `/v1/rerank` (cross-encoders).

**`/v1/decisions`** (PR #40992, closed and landed via #41208, 2026-09-25)
- **Request** (`extra=forbid`):
  - `{input, questions:[{id, type:"choice", question, options:[{name, description}] (2–26, labeled A–Z)} | {type:"score", levels (2–10, labeled 0–9)} | {type:"yes_no", yes?, no?}], temperature, chat_template_kwargs, prompt_format_version, return_prompt_token_ids, model}`
- **Response:**
  - `{object:"decisions", model, prompt_format_version, answers:{id:{type, probabilities, label_mass, choice?, score?, prompt_token_ids?, label_token_ids?}}, usage}`
- The server owns the prompt wording (`PROMPT_FORMAT_VERSION = 1`). It turns reasoning off and validates that each label is a single token.
- `label_mass` is the full-vocab probability of all labels at the answer position. It is a free out-of-distribution / "model isn't answering" signal.
- **Refused:**
  - with `--enable-mis` (it is setwise / fused-choice), with LoRA, or with `--dllm-algorithm`;
  - with built-in named templates or Python encoders;
  - with tokenizers that split labels, and with models whose reasoning parser expects the answer inside reasoning.
- The PR itself reports roughly 0.07 variance in probabilities and 0.14 in label_mass across batch composition and cache state, and says "Nothing is calibrated".
- **`/v1/systemone`** (PR #41208) is the Jev wire format (`state`, questions keyed by caller id, noul/choice/score up to 255, `x_label_mass`, `confidence`, `legend`). Official System One SDKs work by changing only the base URL.
- **Extra hazard for us:** the route needs an added token between message and answer (e.g. `</think>` on Qwen). Test it against our Qwen3.8 chat template.

**OpenAI-compatible server otherwise.** SGLang serves `/v1/chat/completions`, `/v1/completions` (with logprobs), `/v1/models`, `/v1/rerank` and `/v1/classify`. So SGLang is a viable **4th `INFERENCE_BACKEND=sglang`** for generation too, but instinct does not need that. It only needs a scoring endpoint.

**Install**
- **Generic** (https://docs.sglang.io/get_started/install.html):
  - `uv pip install --prerelease=allow sglang`; nightly index `https://docs.sglang.ai/whl/cu130/`; Docker `lmsysorg/sglang:latest`.
  - CUDA 13 is now required: cu129 wheels were retired after v0.5.19, and PyTorch 2.14 ships no cu129.
  - aarch64 (GB200) is listed.
  - Because the decision routes are main-only, we would need nightly, or a source build at a pinned commit plus an image digest.
- **RTX 5090 (sm_120):**
  - No official sm_120 statement found.
  - Community images: `voipmonitor/sglang:cu130` (https://hub.docker.com/r/voipmonitor/sglang), https://github.com/local-inference-lab/blackwell-llm-docker (SM120, CUDA 13.2).
  - Older tracking issue: https://github.com/sgl-project/sglang/issues/9542.
  - FlashInfer lists SM120 and SM121 under one "12.x" column (https://github.com/flashinfer-ai/flashinfer/issues/3170), which is favourable for MIS. **Verify on hardware.**
- **DGX Spark (GB10, sm_121a):**
  - Upstream tracking issue https://github.com/sgl-project/sglang/issues/11658 is still open. Known problems: Triton PTXAS `.tile::gather4` error on sm_121a, FP8 CUTLASS dispatch failures, and the official `lmsysorg/sglang:spark` was an Oct-2025 custom snapshot.
  - Community options:
    - https://github.com/ubehera/sglang-spark (sgl-kernel 0.4.3+sm121a, torch 2.12+cu130, custom NCCL 2.30.4, wedge watcher).
    - `scitrera/dgx-spark-sglang` 0.5.10 / 0.5.11 (flashinfer 0.6.10), "still experimental" (https://forums.developer.nvidia.com/t/new-pre-built-sglang-docker-images-for-nvidia-dgx-spark/360656?page=2).
  - For a small single-GPU scorer, most of those NCCL/TP problems do not apply. Still, treat SGLang on Spark as **verify-on-hardware, tier 2**.

**Reusable for OpenBeast**
- Adopt the `/v1/score` and `/v1/decisions` shapes as instinct's canonical **engine** contracts.
- Adopt `label_mass`, `prompt_format_version` and the replay path (`return_prompt_token_ids` → `/v1/score`) as instinct's provenance and verification mechanism.

## 3. Equivalents in other engines

**vLLM** (v0.30.0, 2026-09-22)
- **Generative server:**
  - `/v1/completions` supports `logprobs` and `prompt_logprobs`.
  - `allowed_token_ids` "only retains scores for the given token ids". With `max_tokens:1` plus `logprobs`, this gives label-restricted scoring on the **same** 27B server we already plan for the Sparks.
  - `logprob_token_ids` (max 128) exists but is engine-only, "not exposed in the OpenAI API server" (https://docs.vllm.ai/en/latest/api/vllm/sampling_params/).
- **Pooling server** (a separate process, one model each):
  - Launch: `vllm serve <m> --runner pooling --convert classify`, overriding with `--hf_overrides '{"classifier_from_token":[...], "method":...}'`.
  - `/classify` returns `{data:[{index, label, probs, num_classes}]}`.
  - `/score` and `/v1/score` take `{model, queries, documents, ...}` and return `data[{index, score}]`.
  - `/rerank`, `/v1/rerank` and `/v2/rerank` are Cohere-compatible (`results[{index, document, relevance_score}]`).
  - Supported architectures include `Qwen3ForSequenceClassification` and Qwen3VL.
  - Docs: https://docs.vllm.ai/en/latest/models/pooling_models/classify/ and https://docs.vllm.ai/en/latest/models/pooling_models/scoring/
- **Qwen3-Reranker trap:** it needs `--hf_overrides '{"architectures":["Qwen3ForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}'` **and** `--chat-template examples/pooling/score/template/qwen3_reranker.jinja`. Without them it starts cleanly and returns HTTP 200 with **meaningless scores** (https://github.com/vllm-project/vllm/issues/55501, closed by PR #56017). Instinct's conformance check must catch this with a known-answer probe.
- vLLM has **no MIS equivalent** (prefix caching only) as far as I found.

**llama.cpp** (local tree at `8e126574f`, 2026-09-08; `llama.cpp/tools/server/README.md`)
- **Generative scoring on the running 8080 server:**
  - `/completion` with `n_predict:1` and `n_probs:K` returns top-K `top_logprobs`. With temperature < 0 this is "a simple softmax of the logits".
  - `logit_bias` and `/tokenize` exist.
  - **Hazard:** top-K can omit labels. This is exactly the post's argument for explicit `label_token_ids`.
  - **Candidate fix, unverified:** use a GBNF grammar restricted to the labels plus `post_sampling_probs:true` (probabilities after the sampler chain) with a neutral sampler (temperature 1, top_k 0, top_p 1, min_p 0). That should give a renormalized distribution over just the labels. It needs a test, including whether it loses `label_mass`. It does, so keep a second unconstrained call when an OOD signal is wanted.
- **Rerank:**
  - `--embedding --pooling rank --rerank` serves `/rerank`, `/v1/rerank` and `/v1/reranking`.
  - Qwen3-Reranker support landed in `b5bd03783` (#15824), multi-label classifier heads in `d17a809ef` (#13940), and Qwen3-VL reranker in #20332 (all in our tree).
  - Community GGUFs are often broken, missing `cls.output.weight` and producing scores like 4.5e-23 (https://gist.github.com/VooDisss/42bce4eb5c76d3c325633886c5e348ee). **Convert it ourselves with `convert_hf_to_gguf.py` and pin the sha256.**
- **No MIS.**
- Pointwise prefix reuse relies on per-slot `cache_prompt`. On hybrid recurrent Qwen3.8 that depends on state checkpoints (our tree has recurrent rollback #28123). **Measure it; don't assume it.**

**TensorFold** (local source `/tmp/claude-1000/obreview/tensorfold` @ `6b2e4c40`, 2026-09-29)
- Routes: `/v1/models`, `/health`, `/v1/chat/completions`, `/v1/completions` only.
- `docs/api.md:39`: "Unsupported generation features include multiple choices through `n` and `logprobs`." `server/http.py` hard-codes `"logprobs": None`.
- There is no grammar or json_schema and no scoring.
- **Instinct cannot get probabilities from TensorFold.** The only fallback is generate-and-parse (temperature 0, a one-token answer), which gives a hard label with no confidence. Mark it `calibrated:false, probs:null` in the contract.

**Resulting capability matrix (what instinct can do per engine)**

| Engine | Label probabilities (pointwise) | Setwise | MIS | Classifier head | Rerank | label_mass |
|---|---|---|---|---|---|---|
| SGLang main | yes (`/v1/score`) | yes (`score_extraction_token`, `/v1/decisions`) | yes | yes | yes | yes |
| vLLM | via `allowed_token_ids` + logprobs (gen) or `/classify` (pooling) | prompt-level | no | yes (pooling runner) | yes | via a second unrestricted call |
| llama.cpp | `n_probs` top-K (risky) or grammar + post_sampling_probs (to verify) | prompt-level | no | `--pooling rank`, multi-label cls | yes | via an unrestricted call |
| TensorFold | no | no | no | no | no | no |

## 4. Decision models in agents and routers

- **RouteLLM** (https://github.com/lm-sys/RouteLLM, Apache-2.0; paper arXiv 2406.18665, cited from memory)
  - Routers: `mf`, `sw_ranking`, `bert`, `causal_llm`, plus a random baseline.
  - `calibrate_threshold` sets the threshold for a target percentage of strong-model calls.
  - Claims up to 85% cost reduction at 95% of GPT-4 quality.
  - **Reuse:** the threshold-for-target-share calibration idea, for hydra's "which pool" when pools differ in quality and cost (the 27B on the 5090 vs a Spark MoE).
- **semantic-router** (https://github.com/aurelio-labs/semantic-router, MIT)
  - Utterance-defined routes, local encoders, threshold fitting.
  - **Reuse:** a zero-GPU tier-0 prefilter. It is a better-founded version of router.py's keyword `_HINTS`.
- **Tool selection:** RAG-MCP (https://arxiv.org/abs/2505.03275) retrieves tool descriptions before the model call. It cuts prompt tokens by more than 50% and raised tool-selection accuracy from 13.62% to 43.13%.
  - **Reuse:** a reranker over the 15 MCP tools, or skills.
  - It touches the agent loop (tools.py, runner.py), so it is **era-locked**. Design it as an opt-in proxy-side hint and defer.
  - This also answers the repo's known "skills fire ~0% on local models" gap in the push→escalate pattern beast-lang already uses. That is an inference from our memory notes.
- **LLM-as-judge with logprobs** (cited from memory, not fetched this session):
  - G-Eval weights scores by the probability of each score token (arXiv 2303.16634).
  - MT-Bench judge (arXiv 2306.05685).
  - **Reuse:** a Score question (0–9 levels) with expected value, which maps directly onto `/v1/decisions` type `score`.
- **Reward models / PRMs:**
  - SGLang `/v1/classify` serves reward models.
  - Qwen PRM lessons (arXiv 2501.07301, from memory).
  - **Reuse:** best-of-N or step verification for beast-assist / beast-lang candidates, as a future phase.
- **Rerankers:** Qwen3-Reranker 0.6B/4B/8B (https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)
  - Apache-2.0, 32k context.
  - Format: system "Judge whether the Document meets the requirements… answer can only be 'yes' or 'no'", plus `<Instruct>/<Query>/<Document>`.
  - Score = softmax(yes, no). 0.6B scores MTEB-R 65.80 and MTEB-Code 73.42.
  - It is a pointwise yes/no decision model **already in the shape `/v1/score` MIS wants**.
- **Guard classifiers:** Qwen3Guard (https://github.com/QwenLM/Qwen3Guard)
  - Gen and Stream variants, 0.6B/4B/8B; 9 categories × safe / controversial / unsafe.
  - The Stream variant is a token-level head, with engine support pending.
  - The license was not stated in the README. Check the LICENSE file before adopting it.
- **Cascades and calibration** (from memory):
  - Temperature scaling (Guo et al. 2017, arXiv 1706.04599).
  - P(True) self-evaluation (Kadavath et al. 2022, arXiv 2207.05221).
  - FrugalGPT cascades (arXiv 2305.05176).
  - Conformal / abstain approaches.
  - **Concrete precedent:** Open-Jev fits one temperature on a calibration split and reports ECE. TypeSafe grades confidence into act / review / escalate.
  - **Reuse:** every instinct question type carries `{threshold_act, threshold_abstain}` fitted on labelled OpenBeast data. Below `abstain`, fall back to the current behaviour (router.py already fails safe to "no-spawn / pass through").

## 5. Small decision-model candidates

| Candidate | Fits | License | Notes |
|---|---|---|---|
| Qwen3-0.6B (generative, zero-shot yes/no) | 5090 spare (~4.76 GB free under the default model), CPU, Spark | Apache-2.0 (Qwen3 family; not re-fetched) | The blog's primary model; 18.7 ms MIS at 16 candidates on H200 |
| Qwen3-Reranker-0.6B / 4B / 8B | 0.6B on the 5090 or CPU; 4B/8B on Spark | Apache-2.0 | Best zero-shot pointwise judge; must be converted or overridden correctly (see §3 traps) |
| Qwen3-8B | Spark | Apache-2.0 | 20.6 ms MIS vs 54.1 ms generate on H200 |
| Qwen3.5-4B | Spark | Qwen (verify) | Hybrid GDN; smaller MIS gains per the post |
| `Qwen3ForSequenceClassification` fine-tune (ours, trained on OpenBeast labels) | any | inherits Apache-2.0 | Served by SGLang #22118, vLLM `--convert classify` and llama.cpp multi-label cls (#13940). The only way to get real calibrated heads cheaply |
| Open-Jev-2B / 9B / 27B-v1.1 | 9B/27B on Spark | Apache-2.0 adapters | Custom loader; 27B shares our Qwen3.8-27B base. Research item |
| Qwen3Guard-Gen 0.6B / 4B / 8B | any | check LICENSE | A safety decision, not a routing one |
| The primary 27B itself via `/v1/decisions` or logprobs | already loaded | — | Zero extra VRAM, but competes for the single MTP slot (the same 17.8 s / 45.2 s vs 15.1 s penalty ROUTER_SIDECAR_PLAN measured) |

## 6. What this means for beast-instinct (recommended shape; for the architecture phase)

1. **Instinct is an engine-agnostic decision service, not an engine.**
   - It exposes one OpenBeast contract: typed questions in, calibrated typed answers out. Modelled on `/v1/decisions` / Open-Jev: `choice | score | yes_no`, plus `label_mass`, `confidence`, `calibrated: bool`, `prompt_format_version`, `engine`, `model_sha`.
   - Adapters, in capability order:
     - SGLang `/v1/score` (MIS) or `/v1/decisions`
     - vLLM `allowed_token_ids` + logprobs, or `/classify`
     - llama.cpp grammar + post_sampling_probs, or `/rerank`
     - TensorFold hard-label only
   - It advertises its capability in `/api/slot` under `services` (a v2-compatible additive field; unknown fields are ignored per BEAST_SLOT.md).
2. **Hydra contract:**
   - Hydra calls `POST instinct /v1/instinct/route {request_features, candidate_pools:[{id, model, engine, load, capabilities}]}` and gets back `{pool_id, probabilities, confidence, abstain}`.
   - On abstain, timeout or error, hydra uses its own static policy. Instinct never sees hydra internals, and hydra never needs instinct to be up. This is the same fail-open posture as router.py.
   - Pools map naturally to pointwise items. That is MIS's sweet spot: one shared query, N pool descriptions.
3. **First integration outside the era lock:**
   - Replace router.py's `_classify` with an instinct `yes_no` / choice call on a sidecar model. router.py is **not** on the era-lock list.
   - This implements ROUTER_SIDECAR_PLAN's goal and must pass its §6 validation gate: the 16/16 spawn battery plus a held-out labelled set.
   - Agent-loop uses (tool or skill preselection, judge) stay opt-in and deferred to an era boundary.
4. **Quality gate (non-negotiable per constraints):**
   - Build a decision eval (e.g. `evals/decisions/`, outside `SUITE_VERSION`) with train / calibration / test / OOD splits, following Open-Jev.
   - Report accuracy, NLL, Brier, ECE, label_mass distribution, and the abstain rate at the chosen thresholds, per model × engine × prompt format (pointwise vs setwise).
   - Replay Open-Jev test and OOD as an external sanity check.
   - Latency comes only from an sglang-benchmark-style open-loop run at *our* intended load.
5. **Placement:**
   - 5090 today: llama.cpp CPU or 0.6B GGUF sidecar (no new engine).
   - Sparks: SGLang MIS scorer or vLLM pooling next to the 27B.
   - Supply chain: SGLang pinned by commit `3b537e96…` plus image digest (no release contains the decision routes); reranker GGUF self-converted with sha256 pinned; label token ids derived at startup from the served tokenizer and asserted single-token.
6. **Conformance probes** (extend `scripts/backends/conformance.sh`):
   - A known-answer yes/no pair must separate by more than a margin. This catches the vLLM Qwen3-Reranker silent-garbage trap and broken GGUF `cls.output`.
   - A label-tokenization assertion.
   - Replay determinism, tolerating the batch-composition variance #40992 reports.

**Open items to verify on hardware:**
- SGLang main plus FlashInfer MIS on sm_120 (5090) and sm_121a (GB10).
- `/v1/decisions` against Qwen3.8's template (the added-token-between-message-and-answer requirement).
- llama.cpp grammar + `post_sampling_probs` giving an exact label distribution.
- Hybrid-model prefix reuse for pointwise scoring in llama.cpp.
- Qwen3 / Qwen3Guard license texts.

Scratch artifacts (downloaded SGLang sources used for the schema and flag verification): `/tmp/claude-1000/-home-max-Documents-openbeast/8d7ac45d-f255-4af1-83d2-99f1da1013b0/scratchpad/sg-main/` and `.../sg-v0.5.20/`. Repo files consulted: `/home/max/Documents/openbeast/agents/router.py`, `/home/max/Documents/openbeast/docs/ROUTER_SIDECAR_PLAN.md`, `/home/max/Documents/openbeast/docs/DGX_SPARK_PLAN.md`, `/home/max/Documents/openbeast/docs/BEAST_SLOT.md`, `/home/max/Documents/openbeast/llama.cpp/tools/server/README.md`, `/tmp/claude-1000/obreview/tensorfold/docs/api.md`.

**Sources:**
- https://www.lmsys.org/blog/2026-09-25-sglang-decision-models
- https://arxiv.org/abs/2512.07846
- https://typesafe.ai/blog/introducing-system-one-models-and-jev
- https://www.marktechpost.com/2026/09/19/typesafe-ai-releases-jev/
- https://github.com/Zefan-Cai/Open-Jev
- https://github.com/Zefan-Cai/Open-Jev/blob/main/jev/api.py
- https://huggingface.co/datasets/ZefanCai/Open-Jev
- https://huggingface.co/ZefanCai/Open-Jev-27B-v1.1
- https://huggingface.co/ZefanCai/Open-Jev-9B
- https://docs.sglang.io/docs/basic_usage/native_api
- https://docs.sglang.io/get_started/install.html
- https://github.com/sgl-project/sglang/pull/10979
- https://github.com/sgl-project/sglang/pull/22118
- https://github.com/sgl-project/sglang/pull/40826
- https://github.com/sgl-project/sglang/pull/40992
- https://github.com/sgl-project/sglang/pull/41208
- https://github.com/sgl-project/sglang/pull/41188
- https://github.com/sgl-project/sglang/issues/15344
- https://github.com/sgl-project/sglang/issues/18132
- https://github.com/sgl-project/sglang/issues/11658
- https://github.com/sgl-project/sglang/issues/9542
- https://github.com/ubehera/sglang-spark
- https://forums.developer.nvidia.com/t/new-pre-built-sglang-docker-images-for-nvidia-dgx-spark/360656?page=2
- https://hub.docker.com/r/voipmonitor/sglang
- https://github.com/local-inference-lab/blackwell-llm-docker
- https://github.com/flashinfer-ai/flashinfer/issues/3170
- https://github.com/chuanrui/sglang-benchmark
- https://docs.vllm.ai/en/latest/serving/online_serving/
- https://docs.vllm.ai/en/latest/models/pooling_models/classify/
- https://docs.vllm.ai/en/latest/models/pooling_models/scoring/
- https://docs.vllm.ai/en/latest/api/vllm/sampling_params/
- https://github.com/vllm-project/vllm/issues/55501
- https://github.com/vllm-project/vllm/issues/30378
- https://gist.github.com/VooDisss/42bce4eb5c76d3c325633886c5e348ee
- https://huggingface.co/Qwen/Qwen3-Reranker-0.6B
- https://github.com/QwenLM/Qwen3Guard
- https://github.com/lm-sys/RouteLLM
- https://github.com/aurelio-labs/semantic-router
- https://arxiv.org/abs/2505.03275
- From memory, not fetched this session: arXiv 2406.18665, 2303.16634, 2306.05685, 2501.07301, 1706.04599, 2207.05221, 2305.05176