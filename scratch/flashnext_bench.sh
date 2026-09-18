#!/bin/bash
# Flash-Next baseline bench: short gen (decode) + 1.6k-token prompt (PP). Prints one line each.
OB=/home/max/Documents/openbeast; cd $OB; source scripts/conf.sh 2>/dev/null; K="${LLAMA_API_KEY:-}"; H=(); [ -n "$K" ] && H=(-H "Authorization: Bearer $K")
run() { curl -s "${H[@]}" localhost:8080/v1/chat/completions -H 'Content-Type: application/json' -d @"$1" | python3 -c "
import sys,json;d=json.load(sys.stdin);t=d.get('timings',{})
print(f\"  $2: prompt {t.get('prompt_n')} tok @ {t.get('prompt_per_second',0):.1f} tok/s | decode {t.get('predicted_n')} tok @ {t.get('predicted_per_second',0):.1f} tok/s\")"; }
python3 - <<'PY'
import json
open('/tmp/claude-1000/fn-short.json','w').write(json.dumps({"messages":[{"role":"user","content":"Explain in ~150 words why Pollard rho factors semiprimes faster than trial division. /no_think"}],"max_tokens":256,"temperature":1.0,"top_p":0.95,"top_k":20,"min_p":0.0,"chat_template_kwargs":{"enable_thinking":False}}))
p=('The quick brown fox jumps over the lazy dog near the riverbank while the old miller counts sacks of grain. ')*70
open('/tmp/claude-1000/fn-long.json','w').write(json.dumps({"messages":[{"role":"user","content":p+"\n\nSummarize the above in one sentence. /no_think"}],"max_tokens":64,"chat_template_kwargs":{"enable_thinking":False}}))
PY
run /tmp/claude-1000/fn-short.json short; run /tmp/claude-1000/fn-long.json long
echo "  VRAM $(nvidia-smi --query-gpu=memory.used --format=csv,noheader) | RAM used $(free -g | awk 'NR==2{print $3}') GB"
