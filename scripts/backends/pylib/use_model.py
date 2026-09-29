#!/usr/bin/env python3
"""Make the rig's consumers agree with the model the Sparks serve.

    use-model.sh --profile NAME | --model ID [--report FILE] [--opencode-out FILE] [--force] [--dry-run]

1. Takes the served id from the profile (SERVED_MODEL_NAME) or --model.
2. Requires a PASSING conformance report for exactly that id at this rig's
   INFERENCE_URL (default .run/conformance/latest.json) — --force skips that.
3. Records INFERENCE_MODEL="<id>" in openbeast.conf (replacing any earlier
   line; mode preserved, 0600 when created). conf.sh exports it as
   OPENBEAST_INFERENCE_MODEL for vllm/tensorfold, which agents/runner.py sends.
4. Prints the opencode provider entry (or writes it to --opencode-out). The
   repo's opencode.json is NOT edited: it is tracked and part of the eval era
   hash; opencode merges ~/.config/opencode/opencode.json with it.
Open WebUI needs nothing: it lists whatever /v1/models lists, which the
conformance report proves.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

import obprofile  # noqa: E402

BAD = re.compile(r"[\"'\\$`\x00-\x1f]|\s#")


def _base(url: str) -> str:
    u = (url or "").rstrip("/")
    return u[:-3] if u.endswith("/v1") else u


def check_report(path: Path, model: str, url: str) -> tuple[bool, str, dict]:
    if not path.is_file():
        return False, f"no conformance report at {path} — run scripts/backends/conformance.sh --model '{model}'", {}
    try:
        doc = json.loads(path.read_text())
    except ValueError as e:
        return False, f"{path} is not JSON ({e})", {}
    facts = doc.get("facts") or {}
    if facts.get("model") != model:
        return False, f"{path} is about {facts.get('model')!r}, not {model!r} — run conformance.sh --model '{model}'", doc
    if url and _base(doc.get("url", "")) != _base(url):
        return False, f"{path} probed {doc.get('url')}, but INFERENCE_URL is {url}", doc
    if not doc.get("ok"):
        failed = [r["name"] for r in doc.get("results", []) if r.get("required") and r.get("status") != "pass"]
        return False, f"the conformance report FAILED ({', '.join(failed)}) — fix the profile first", doc
    return True, f"conformance PASSED at {doc.get('when')}", doc


def write_conf(conf: Path, model: str, dry: bool) -> str:
    line = f'INFERENCE_MODEL="{model}"'
    lines = conf.read_text().splitlines() if conf.is_file() else []
    out, done = [], False
    for x in lines:
        if re.match(r"^\s*INFERENCE_MODEL\s*=", x):
            if not done:
                out.append(line)
                done = True
            continue
        out.append(x)
    if not done:
        out += ["", "# Served model id, recorded by scripts/backends/use-model.sh", line]
    if dry:
        return line
    mode = conf.stat().st_mode & 0o777 if conf.is_file() else 0o600
    tmp = conf.with_name(f".{conf.name}.tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, mode)
    os.replace(tmp, conf)
    return line


def opencode_snippet(model: str, url: str, ctx: int | None, keyed: bool) -> dict:
    options = {"baseURL": f"{_base(url)}/v1"}
    if keyed:
        options["apiKey"] = "{env:OPENBEAST_API_KEY}"      # opencode substitutes it; no key in the file
    entry = {"name": model}
    if ctx:
        entry["limit"] = {"context": ctx, "output": min(32768, max(1024, ctx // 4))}
    return {"$schema": "https://opencode.ai/config.json", "provider": {"openbeast-inference": {
        "npm": "@ai-sdk/openai-compatible", "name": "OpenBeast inference (INFERENCE_URL)",
        "options": options, "models": {model: entry}}}}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile")
    g.add_argument("--model")
    ap.add_argument("--report", type=Path, default=REPO / ".run" / "conformance" / "latest.json")
    ap.add_argument("--conf", type=Path, default=REPO / "openbeast.conf")
    ap.add_argument("--opencode-out", type=Path)
    ap.add_argument("--force", action="store_true", help="record it without a passing conformance report")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--profiles-dir", type=Path, default=Path(os.environ.get("OPENBEAST_PROFILES_DIR")
                                                               or obprofile.MODELS))
    a = ap.parse_args(argv)
    try:
        model = obprofile.load(a.profile, None, a.profiles_dir).get("SERVED_MODEL_NAME") if a.profile else a.model
    except obprofile.ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not model or BAD.search(model) or len(model) > 200:
        print(f"Error: model id {model!r} is empty, too long, or has quotes/$/`/control characters/' #'",
              file=sys.stderr)
        return 1
    backend = os.environ.get("OPENBEAST_INFERENCE_BACKEND") or os.environ.get("INFERENCE_BACKEND") or "llama"
    url = os.environ.get("OPENBEAST_INFERENCE_URL") or os.environ.get("INFERENCE_URL") or ""
    ok, why, doc = check_report(a.report, model, url)
    print(("OK   " if ok else "WARN ") + why)
    if not ok and not a.force:
        print("Refusing to record a model that has not passed conformance here (--force to override).",
              file=sys.stderr)
        return 1
    line = write_conf(a.conf, model, a.dry_run)
    print(f"{'would write' if a.dry_run else 'wrote'} {line} → {a.conf}")
    if backend == "llama":
        print("Note: INFERENCE_BACKEND=llama — llama-server ignores model ids, so INFERENCE_MODEL takes effect "
              "only once INFERENCE_BACKEND is vllm or tensorfold.")
    ctx = (doc.get("facts") or {}).get("max_model_len") if doc else None
    keyed = backend != "tensorfold" and bool(os.environ.get("LLAMA_API_KEY") or os.environ.get("OPENBEAST_API_KEY"))
    snip = json.dumps(opencode_snippet(model, url or "http://SPARK:8000", ctx, keyed), indent=2)
    if a.opencode_out:
        if a.opencode_out.exists() and not a.force:
            print(f"Error: {a.opencode_out} exists — merge the snippet below by hand, or --force", file=sys.stderr)
            print(snip)
            return 1
        if not a.dry_run:
            a.opencode_out.write_text(snip + "\n")
        print(f"{'would write' if a.dry_run else 'wrote'} the opencode provider entry → {a.opencode_out}")
    else:
        print("\nopencode: merge this provider into ~/.config/opencode/opencode.json (opencode merges it with the "
              "repo's opencode.json, which is left untouched):")
        print(snip)
    print("\nConsumers:")
    print(f"  agent runner  sends {model!r} (OPENBEAST_INFERENCE_MODEL, exported by conf.sh for vllm/tensorfold) "
          "— next ./agent.sh run; restart the stack for spawned agents")
    print("  Open WebUI    nothing to do: it lists /v1/models" + (" (conformance saw it listed)" if ok else ""))
    print("  opencode      the snippet above: model 'openbeast-inference/" + model + "'")
    print("  doctor        ./scripts/doctor.sh shows the backend row")
    return 0


if __name__ == "__main__":
    sys.exit(main())
