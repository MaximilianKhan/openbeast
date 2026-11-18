#!/bin/bash
# spark-model-intake.sh: the offline intake preflight. Hermetic: fixture
# checkpoints (tiny safetensors with real headers) and a fixture registry in a
# scratch dir; no Spark, network, docker or GPU. The run against the real
# folders on spark-1 is recorded in docs/EXL3_PLAYBOOK.md "Intake on the real
# folders".
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SMI="$REPO_DIR/scripts/backends/spark-model-intake.sh"
SDP="$REPO_DIR/scripts/backends/spark-deepseek-pack.sh"
PASS=0 FAIL=0 SKIP=0
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  SKIP: $*"; SKIP=$((SKIP + 1)); }
echo "=== spark-model-intake tests ==="

T="$(cd "$(mktemp -d)" && pwd -P)"; trap 'rm -rf "$T"' EXIT
export INTAKE_HOME="$T/home"

# --- fixtures: mk DIR MODEL_TYPE QUANT_JSON KEY... (two shards, keys split between them)
python3 -I - "$T" <<'PY'
import hashlib, json, os, struct, sys
T = sys.argv[1]
def shard(path, keys):
    h = {k: {"dtype": "F16", "shape": [2], "data_offsets": [4 * i, 4 * i + 4]} for i, k in enumerate(keys)}
    raw = json.dumps(h).encode()
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(raw))); f.write(raw); f.write(b"\0" * 4 * len(keys))
def mk(d, mt, arch, q, keys, extra_cfg=None):
    os.makedirs(d, exist_ok=True)
    cfg = {"architectures": [arch], "model_type": mt, "quantization_config": q,
           "text_config": {"num_hidden_layers": 2, "hidden_size": 8, "num_attention_heads": 2,
                           "num_key_value_heads": 2, "max_position_embeddings": 4096}}
    cfg.update(extra_cfg or {})
    json.dump(cfg, open(f"{d}/config.json", "w"))
    half = len(keys) // 2
    parts = {"model-00001-of-00002.safetensors": keys[:half], "model-00002-of-00002.safetensors": keys[half:]}
    wm = {}
    for name, ks in parts.items():
        shard(f"{d}/{name}", ks); wm.update({k: name for k in ks})
    json.dump({"metadata": {"total_size": 0}, "weight_map": wm}, open(f"{d}/model.safetensors.index.json", "w"))
    open(f"{d}/tokenizer.json", "w").write("{}")
    open(f"{d}/chat_template.jinja", "w").write("{% for m in messages %}{{ m.content }}{% endfor %}{% if tools %}{{ tools }}{% endif %}")
    lines = []
    for n in sorted(os.listdir(d)):
        if n.endswith(".safetensors"):
            lines.append(hashlib.sha256(open(f"{d}/{n}", "rb").read()).hexdigest() + "  " + n)
    open(f"{d}/SHA256SUMS", "w").write("\n".join(lines) + "\n")
ds_keys = [f"layers.{i}.attn.wq_a.{s}" for i in range(3) for s in ("trellis", "suh", "svh", "mul1")] + ["embed.weight"]
ds_q = {"quant_method": "exl3", "avg_bits": 2.9, "codebook": "mul1"}
glm_keys = [f"model.layers.{i}.mlp.experts.0.up_proj.{s}" for i in range(2) for s in ("trellis", "suh", "svh", "mcg")] + ["lm_head.weight"]
glm_q = {"quant_method": "exl3", "bits": 4, "codebook": "mcg", "scope": "glm53_routed_experts_only"}
mk(f"{T}/home/ref-ds", "deepseek_v41", "DeepseekV41ForCausalLM", ds_q, ds_keys)
mk(f"{T}/ds-pack", "deepseek_v41", "DeepseekV41ForCausalLM", dict(ds_q, avg_bits=2.8), ds_keys)
mk(f"{T}/ds-v11", "deepseek_v41", "DeepseekV41ForCausalLM", ds_q,
   [k.replace("layers.1.attn.wq_a.trellis", "layers.1.attn.wq_a.weight") for k in ds_keys])
mk(f"{T}/home/ref-glm", "glm5_next", "Glm5NextForConditionalGeneration", glm_q, glm_keys)
mk(f"{T}/glm-pack", "glm5_next", "Glm5NextForConditionalGeneration", glm_q, glm_keys)
mk(f"{T}/glm-3bit", "glm5_next", "Glm5NextForConditionalGeneration", dict(glm_q, bits=3), glm_keys)
mk(f"{T}/qwen-exl3", "qwen3_5", "Qwen3_5ForCausalLM", {"quant_method": "exl3", "bits": 4}, ["a.trellis", "b.weight"])
mk(f"{T}/mystery", "llama9", "Llama9ForCausalLM", {"quant_method": "exl3", "bits": 4}, ["a.trellis", "b.weight"])
mk(f"{T}/broken", "deepseek_v41", "DeepseekV41ForCausalLM", ds_q, ds_keys)
os.remove(f"{T}/broken/model-00002-of-00002.safetensors")
mk(f"{T}/badsum", "deepseek_v41", "DeepseekV41ForCausalLM", ds_q, ds_keys)
s = open(f"{T}/badsum/SHA256SUMS").read().splitlines()
s[0] = "0" * 64 + s[0][64:]
open(f"{T}/badsum/SHA256SUMS", "w").write("\n".join(s) + "\n")
os.makedirs(f"{T}/recipe/scripts"); open(f"{T}/recipe/start.sh", "w").write("#!/bin/bash\n")
os.makedirs(f"{T}/kit"); open(f"{T}/kit/apply.py", "w").write(""); shard(f"{T}/kit/overlay.safetensors", ["x"])
def sig(keys): return hashlib.sha256(("\n".join(sorted(keys)) + "\n").encode()).hexdigest()
reg = json.load(open(os.environ.get("REG_SRC", "/dev/null"))) if os.environ.get("REG_SRC") else None
fam = lambda name, mt, arch, q, keys, ref, swap, rank_files: {
    "name": name, "title": name, "model_types": [mt], "architectures": [arch], "quant": q, "index_keys": len(keys),
    "index_sha256": sig(keys), "reference_dirs": [ref], "known_packs": {}, "tp": 2, "swap": swap,
    "rank_files": rank_files, "hf_cache": "~/hf", "served_name": name}
registry = {
    "pair": {"mem_total_gib": 121.69, "rank_weight_comfortable_gib": 92, "rank_weight_limit_gib": 100,
             "rank_weight_factor": 1.008},
    "families": [
        fam("deepseek-v41", "deepseek_v41", "DeepseekV41ForCausalLM", {"quant_method": "exl3"}, ds_keys, "~/ref-ds",
            "spark-deepseek-pack.sh use {dir} --dry-run", True),
        fam("glm53", "glm5_next", "Glm5NextForConditionalGeneration", glm_q, glm_keys, "~/ref-glm",
            "manual: GLM pack by hand", False)],
    "engines": [{"name": "native-1.2.7", "title": "native TensorFold 1.2.7", "launch": "spark-node.sh",
                 "families": {"glm5_next": ["exl3"], "qwen3_5": ["mlx-q4g64", "exl3"]},
                 "ranks": {"glm5_next": [2], "qwen3_5/exl3": [1]},
                 "exl3_variants": {"glm5_next": {"bits": 4, "codebook": "mcg", "scope": "glm53_routed_experts_only"}},
                 "refuses": {"deepseek_v41": "no Zig family serves deepseek_v41"}}]}
json.dump(registry, open(f"{T}/registry.json", "w"))
registry["pair"]["rank_weight_limit_gib"] = 1e-9
registry["pair"]["rank_weight_comfortable_gib"] = 1e-9
json.dump(registry, open(f"{T}/tiny.json", "w"))
PY
export INTAKE_REGISTRY="$T/registry.json"
mkdir -p "$T/home/hf"

run() { out=$(bash "$SMI" "$@" 2>&1); rc=$?; }

# --- (a) a same-layout pack of a family we serve: DROP-IN, exit 0, the swap command
run "$T/ds-pack"
if [[ $rc == 0 ]] && grep -qx 'VERDICT: DROP-IN deepseek-v41' <<<"$out" \
   && grep -q "^Next       spark-deepseek-pack.sh use $T/ds-pack --dry-run" <<<"$out" && grep -q 'EXL3 2.8 bpw' <<<"$out"; then
  pass "same layout as the reference: DROP-IN, exit 0, the swap command"
else fail "drop-in (rc $rc): $out"; fi

# --- a GLM pack: DROP-IN for the GLM family
run "$T/glm-pack"
if [[ $rc == 0 ]] && grep -qx 'VERDICT: DROP-IN glm53' <<<"$out"; then pass "GLM same layout: DROP-IN glm53"
else fail "glm drop-in (rc $rc): $out"; fi

# --- same model_type, one tensor stored differently: SAME FAMILY, NOT A DROP-IN, with the index diff
run "$T/ds-v11"
if [[ $rc == 1 ]] && grep -qx 'VERDICT: SAME FAMILY, NOT A DROP-IN deepseek-v41' <<<"$out" \
   && grep -q "Index diff vs $T/home/ref-ds: +1 -1 keys" <<<"$out" && grep -q "'weight': 1" <<<"$out" \
   && grep -q 'no Zig family serves deepseek_v41' <<<"$out"; then
  pass "index differs: SAME FAMILY, NOT A DROP-IN, diff +1 -1 by suffix, native's refusal named"
else fail "same family (rc $rc): $out"; fi

# --- GLM at 3 bits: the recipe's quant fields and native's EXL3 variant both refuse it
run "$T/glm-3bit"
if [[ $rc == 1 ]] && grep -qx 'VERDICT: SAME FAMILY, NOT A DROP-IN glm53' <<<"$out" && grep -q 'bits 3 (needs 4)' <<<"$out" \
   && grep -q 'reads glm5_next EXL3 only as' <<<"$out"; then
  pass "quant variant differs: SAME FAMILY, native's EXL3 variant check named"
else fail "glm 3bit (rc $rc): $out"; fi

# --- (b) an architecture native TensorFold claims: NEEDS A PROFILE, TP1, model-inspect next
run "$T/qwen-exl3"
if [[ $rc == 1 ]] && grep -qx 'VERDICT: NEEDS A PROFILE' <<<"$out" && grep -q 'claims model_type qwen3_5 as exl3; TP1' <<<"$out" \
   && grep -q -- '--write-profile NAME --backend tensorfold' <<<"$out"; then
  pass "engine claims it: NEEDS A PROFILE, TP1, the profile command"
else fail "needs profile (rc $rc): $out"; fi

# --- (c) nothing claims it
run "$T/mystery"
if [[ $rc == 1 ]] && grep -qx 'VERDICT: NOT SERVABLE HERE' <<<"$out" && grep -q "model_type 'llama9'" <<<"$out"; then
  pass "unknown architecture: NOT SERVABLE HERE, says why"
else fail "not servable (rc $rc): $out"; fi

# --- a drop-in that does not fit (fixture registry with a tiny per-rank limit)
out=$(INTAKE_REGISTRY="$T/tiny.json" bash "$SMI" "$T/ds-pack" 2>&1); rc=$?
if [[ $rc == 1 ]] && grep -qx 'VERDICT: NOT SERVABLE HERE' <<<"$out" && grep -q 'a deepseek-v41 drop-in by layout, but' <<<"$out"; then
  pass "drop-in layout that does not fit: NOT SERVABLE HERE"
else fail "too big (rc $rc): $out"; fi

# --- a shard missing: INCOMPLETE
run "$T/broken"
if [[ $rc == 1 ]] && grep -qx 'VERDICT: INCOMPLETE' <<<"$out" && grep -q '1 shard(s) the index names are missing' <<<"$out"; then
  pass "missing shard: INCOMPLETE"
else fail "incomplete (rc $rc): $out"; fi

# --- not models: a recipe repo, an overlay kit
run "$T/recipe"; r1=$out; run "$T/kit"; r2=$out
if grep -qx 'VERDICT: NOT A MODEL' <<<"$r1" && grep -q 'a recipe repository' <<<"$r1" \
   && grep -qx 'VERDICT: NOT A MODEL' <<<"$r2" && grep -q 'an overlay or patch kit' <<<"$r2"; then
  pass "recipe repo and overlay kit: NOT A MODEL, named"
else fail "not a model: $r1 / $r2"; fi

# --- checksums: not read by default; --checksums verifies and names a mismatch
run "$T/badsum"; d=$out; run "$T/badsum" --checksums; c=$out
run "$T/ds-pack" --checksums; g=$out
if grep -q 'not verified (--checksums)' <<<"$d" && grep -q 'verified 1 ok, 1 mismatched' <<<"$c" \
   && grep -q 'WARNING    SHA256SUMS: 1 mismatch' <<<"$c" && grep -q 'verified 2 ok, 0 mismatched' <<<"$g"; then
  pass "SHA256SUMS: untouched by default, --checksums finds the bad entry"
else fail "checksums: $d / $c / $g"; fi

# --- several folders: one verdict each, exit 1 unless all are drop-ins; --json parses
run "$T/ds-pack" "$T/glm-pack"; both=$rc; run "$T/ds-pack" "$T/mystery"; mixed=$rc
js=$(bash "$SMI" "$T/ds-pack" "$T/mystery" --json 2>/dev/null)
if [[ $both == 0 && $mixed == 1 ]] && python3 -c 'import json,sys; v=json.load(sys.stdin); assert [r["verdict"] for r in v]==["DROP-IN","NOT SERVABLE HERE"]; assert v[0]["fit"]["tp"]==2' <<<"$js"; then
  pass "several folders: exit 0 only if all drop in; --json lists each verdict"
else fail "multi/json: $both $mixed $js"; fi

# --- offline: a Hub id is refused, not fetched
run Mia-AiLab/Some-Model
if [[ $rc == 2 ]] && grep -q 'intake is offline' <<<"$out"; then pass "Hub id: refused (exit 2), never fetched"
else fail "hub id (rc $rc): $out"; fi

# --- the shipped registry: valid, every family has what intake and the playbook rely on
if python3 -I - "$REPO_DIR/scripts/backends/data/spark-families.json" <<'PY'
import json, re, sys
r = json.load(open(sys.argv[1]))
need = {"name", "model_types", "quant", "index_sha256", "index_keys", "tp", "recipe", "image", "unit", "served_name", "swap"}
for f in r["families"]:
    assert need <= set(f), (f.get("name"), need - set(f))
    assert re.fullmatch(r"[0-9a-f]{64}", f["index_sha256"])
assert {f["name"] for f in r["families"]} == {"deepseek-v41", "glm53"}
assert "deepseek_v41" in r["engines"][0]["refuses"]
PY
then pass "shipped registry: both families complete, native's DeepSeek refusal recorded"
else fail "shipped registry"; fi

# --- the local revision equals spark-deepseek-pack.sh's (GNU stat)
if stat -c %s "$T/registry.json" >/dev/null 2>&1; then
  a=$(bash "$SDP" _pack_rev "$T/ds-pack"); b=$(python3 -I -c 'import json,sys; print(json.load(sys.stdin)["local_rev"])' <<<"$(bash "$SMI" "$T/ds-pack" --json)")
  if [[ "$a" == "$b" ]]; then pass "local rev matches spark-deepseek-pack.sh _pack_rev"; else fail "rev: $a vs $b"; fi
else skip "local rev vs spark-deepseek-pack.sh needs GNU stat (runs on the Sparks)"; fi

# --- the content fingerprint equals spark-deepseek-pack.sh's (portable: head/tail/sha256), on shards over 1 MiB too
mkdir -p "$T/fp"; head -c 3145731 /dev/urandom > "$T/fp/model-00001-of-00002.safetensors"; head -c 1000 /dev/urandom > "$T/fp/model-00002-of-00002.safetensors"
a=$(bash "$SDP" _fingerprint "$T/fp")
b=$(python3 -I - "$REPO_DIR/scripts/backends/pylib" "$T/fp" <<'PY'
import sys; from pathlib import Path
sys.path.insert(0, sys.argv[1]); import model_intake as m
print(m.pack_fingerprint(Path(sys.argv[2])))
PY
)
c=$(python3 -I -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])' <<<"$(bash "$SMI" "$T/ds-pack" --json)")
d=$(bash "$SDP" _fingerprint "$T/ds-pack")
if [[ "$a" =~ ^[0-9a-f]{64}$ && "$a" == "$b" && "$c" == "$d" ]]; then pass "fingerprint: model_intake.py == spark-deepseek-pack.sh (3 MiB and tiny shards)"
else fail "fingerprint parity: $a / $b ; $c / $d"; fi
printf 'x' | dd of="$T/fp/model-00001-of-00002.safetensors" bs=1 seek=2000000 conv=notrunc 2>/dev/null   # mid-file byte: not covered
printf 'y' | dd of="$T/fp/model-00001-of-00002.safetensors" bs=1 seek=3145730 conv=notrunc 2>/dev/null   # last MiB: covered
e=$(bash "$SDP" _fingerprint "$T/fp")
if [[ "$e" != "$a" ]]; then pass "fingerprint: an edit in a shard's last MiB changes it (same size, same REV)"; else fail "fingerprint did not change"; fi

# --- a known pack's REV with other content: warned as NOT that pack (the REV is a size listing)
rev=$(python3 -I -c 'import json,sys; print(json.load(sys.stdin)["local_rev"])' <<<"$(bash "$SMI" "$T/ds-pack" --json)")
for fpv in "$d" 0000000000000000000000000000000000000000000000000000000000000000; do
  python3 -I - "$T/registry.json" "$T/known-$fpv.json" "$rev" "$fpv" <<'PY'
import json, sys
r = json.load(open(sys.argv[1])); f = r["families"][0]
f["known_packs"] = {sys.argv[3]: "Mia test pack"}; f["pack_fingerprints"] = {sys.argv[3]: sys.argv[4]}
json.dump(r, open(sys.argv[2], "w"))
PY
done
out=$(INTAKE_REGISTRY="$T/known-$d.json" bash "$SMI" "$T/ds-pack" 2>&1)
out0=$(INTAKE_REGISTRY="$T/known-0000000000000000000000000000000000000000000000000000000000000000.json" bash "$SMI" "$T/ds-pack" 2>&1)
if grep -q "this is a known pack: Mia test pack" <<<"$out" && grep -q "it is NOT that pack" <<<"$out0" && ! grep -q "this is a known pack" <<<"$out0"; then
  pass "known pack: matched by REV AND fingerprint; same REV, other content is flagged"
else fail "known pack: $out / $out0"; fi

# --- usage
if bash "$SMI" >/dev/null 2>&1; then fail "no folder should exit non-zero"; else pass "no folder: usage, non-zero exit"; fi

echo "=== $PASS passed, $FAIL failed, $SKIP skipped ==="
(( FAIL == 0 ))
