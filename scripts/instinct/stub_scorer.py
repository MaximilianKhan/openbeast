#!/usr/bin/env python3
"""A hermetic scoring server that speaks llama.cpp AND SGLang wire formats.

Plumbing only — its "model" is a keyword lexicon. It exists so every instinct
adapter, probe and fault path can be tested with no GPU and no real model
(plan §5.13). stdlib only (ThreadingHTTPServer).

llama.cpp:  GET /health  GET /props  POST /tokenize {content, add_special, with_pieces}
            POST /completion {prompt, n_predict, n_probs, temperature, ...}
              -> completion_probabilities[0].top_logprobs[{id, token, logprob, bytes}]
            GET /slots -> [{id, is_processing}]   (the primary's busy check)
SGLang:     GET /v1/models  POST /tokenize {text, add_special_tokens} -> {tokens:[int]}
            POST /v1/score {query, items, label_token_ids, apply_softmax, temperature}
              -> {scores: [[p...]], object: "scoring", model, usage}
              apply_softmax:false returns exp(full-vocab logprob) per label (F1)
Open-Jev:   GET /v1/identity (what scripts/instinct/openjev_gate.py adds)
            POST /v1/systemone {state, questions: {id: {type: noul|choice, ...}}}
              -> {answers: {id: {type, noul: p} | {type, probabilities: {..}}}}

Tokenizer: whitespace/punctuation pieces with a leading-space convention
(" yes"), newline runs as their own piece, ChatML/think specials as single
tokens; ids = 1000 + crc32(piece) % 140000 (stable across runs).

Scoring: logit(yes) = -2 + Σ lexicon weights over the DATA block (the first
<tag>…</tag> in the user section). Choice prompts ("one letter") score the
letters A-E from a class lexicon. The label tokens share `mass` (0.97) of the
distribution; filler tokens take the rest.

Faults (CLI flags, combinable; or per request via `X-Stub-Fault: a,b=1`):
  --garbage            flat label distribution
  --invert             negate the yes logit / reverse the letters
  --slow MS            sleep before answering
  --error-rate R       HTTP 500 with probability R
  --drop-label TEXT    omit that label token from llama.cpp top_logprobs
  --multi-token-label TEXT  tokenize that label as one token per character
  --low-mass           labels share only 0.2 of the distribution
  --nondeterministic S gaussian noise (sigma S) on every logit
  --mis-skew D         /v1/score with >1 item: shift p(yes) by D
  --squatter           /props and /v1/models report a different model id
  --busy               /slots reports slot 0 is_processing (a user owns the primary)
Every request is appended to --call-log (JSONL) when given, with the
Authorization header it carried (this is a test stub: tests assert on it).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "stub-lexicon"
SPECIALS = {"<|im_start|>": 151644, "<|im_end|>": 151645, "<|endoftext|>": 151643,
            "<think>": 151667, "</think>": 151668}
_TOK = re.compile(r"(<\|im_start\|>|<\|im_end\|>|<\|endoftext\|>|</?think>|\n+"
                  r"| ?[A-Za-z0-9_]+| ?[^\sA-Za-z0-9_]|[ \t\r\f\v]+|\s)")
YES_LEX = {"spawn": 3.0, "background": 2.5, "report back": 2.5, "check back": 2.0,
           "autonomous": 2.0, "kick off": 2.0, "agent": 1.5, "while i": 1.5,
           "while we": 1.5, "in parallel": 1.5, "meanwhile": 1.5, "whole": 1.0,
           "entire": 1.0, "wet": 5.0, "hot": 4.0, "sweet": 4.0, "white": 2.0,
           "swim": 1.0, "fly": 3.0, "grow": 4.0,
           "cold": -5.0, "what is": -3.0, "what are": -3.0, "explain": -3.0,
           "how do": -3.0, "difference between": -3.0, "?": -1.0,
           "warm": -5.0, "sing": -5.0, "coal": -6.0, "bright": -5.0, "salt": -8.0,
           "stones": -6.0, "liquid": -5.0}
CLASS_LEX = {
    "A": {"hello": 1.0, "what": 0.5, "why": 0.5, "explain": 0.5},
    "B": {"def ": 2.0, "code": 1.5, "function": 1.5, "bug": 1.5, "refactor": 2.0,
          "test": 1.0, "compile": 1.5, "has_tools=true": 2.0},
    "C": {"summarize": 2.0, "document": 1.5, "whole book": 2.0, "transcript": 2.0},
    "D": {"image": 2.0, "photo": 2.0, "has_images=true": 3.0},
    "E": {"client_class=batch": 3.0, "batch": 1.5},
}
FILLERS = ["The", " I", " Sure", " It", ".", " Maybe"]
OPENJEV_IDENTITY = {"model": MODEL_ID, "base_revision": "stub-base",
                    "adapter_revision": "stub-adapter", "head_sha256": "stub-head",
                    "loader_digest": "sha256:" + "0" * 64}


def parse_faults(spec: str) -> dict:
    out: dict = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition("=")
        k = k.strip().replace("_", "-")
        out[k] = v.strip() if v else True
    return out


class Stub:
    def __init__(self, faults: dict, call_log: str | None, seed: int = 7):
        self.faults = faults
        self.call_log = call_log
        self.lock = threading.Lock()
        self.rng = random.Random(seed)
        # Seeded too: an unseeded noise source made the nondeterminism probe test
        # fail at random when the noise also sank the known-answer check first.
        self.noise_rng = random.Random(seed + 1)
        self.rev: dict[int, str] = {v: k for k, v in SPECIALS.items()}
        for p in ["yes", "no", " yes", " no", "A", "B", "C", "D", "E",
                  " A", " B", " C", " D", " E"] + FILLERS:
            self.tid(p)

    # --- tokenizer ---
    def tid(self, piece: str) -> int:
        if piece in SPECIALS:
            return SPECIALS[piece]
        i = 1000 + zlib.crc32(piece.encode("utf-8")) % 140000
        self.rev.setdefault(i, piece)
        return i

    def pieces(self, text: str, f: dict) -> list[str]:
        out = _TOK.findall(text)
        split = f.get("multi-token-label")
        if isinstance(split, str) and split:
            new = []
            for p in out:
                if p.strip() == split and len(p.strip()) > 1:
                    lead = p[: len(p) - len(p.lstrip())]
                    chars = list(p.strip())
                    chars[0] = lead + chars[0]
                    new.extend(chars)
                else:
                    new.append(p)
            out = new
        return out

    # --- scoring ---
    @staticmethod
    def _user_section(prompt: str) -> str:
        if "<|im_start|>user" in prompt:
            return prompt.rsplit("<|im_start|>user", 1)[1]
        return prompt.split("\n\n", 1)[1] if "\n\n" in prompt else prompt

    @staticmethod
    def _data(section: str) -> str:
        m = re.search(r"<([a-z_]+)>\n?(.*?)\n?</\1>", section, re.DOTALL)
        return m.group(2) if m else section

    def distribution(self, prompt: str, f: dict) -> dict[str, float]:
        """Full-'vocabulary' distribution over pieces at the answer boundary."""
        space = "" if prompt.endswith("\n") else " "
        section = self._user_section(prompt)
        mass = 0.2 if f.get("low-mass") else 0.97
        sigma = float(f["nondeterministic"]) if f.get("nondeterministic") not in (
            None, True) else (0.5 if f.get("nondeterministic") is True else 0.0)
        if "one letter" in prompt:
            facts = [ln for ln in section.splitlines() if ln.startswith("Facts:")]
            low = (self._data(section) + "\n" + "\n".join(facts)).lower()
            z = {L: sum(w for k, w in lex.items() if k in low) for L, lex in CLASS_LEX.items()}
            if f.get("invert"):
                z = {L: -v for L, v in z.items()}
            if f.get("garbage"):
                z = {L: 0.0 for L in z}
            if sigma:
                z = {L: v + self.noise_rng.gauss(0, sigma) for L, v in z.items()}
            m = max(z.values())
            e = {L: math.exp(v - m) for L, v in z.items()}
            s = sum(e.values())
            dist = {space + L: mass * v / s for L, v in e.items()}
        else:
            data = self._data(section).lower()
            z = -2.0 + sum(w for k, w in YES_LEX.items() if k in data)
            if f.get("invert"):
                z = -z
            if f.get("garbage"):
                z = 0.0
            if sigma:
                z += self.noise_rng.gauss(0, sigma)
            p_yes = 1.0 / (1.0 + math.exp(-z))
            dist = {space + "yes": mass * p_yes, space + "no": mass * (1 - p_yes)}
        rest = 1.0 - mass
        weights = [0.4, 0.25, 0.15, 0.1, 0.06, 0.04]
        for fl, w in zip(FILLERS, weights):
            dist[fl] = dist.get(fl, 0.0) + rest * w
        return dist

    def p_yes(self, text: str, f: dict) -> float:
        z = -2.0 + sum(w for k, w in YES_LEX.items() if k in text.lower())
        if f.get("invert"):
            z = -z
        if f.get("garbage"):
            z = 0.0
        return 1.0 / (1.0 + math.exp(-z))

    def log(self, path: str, body, fault: dict, auth: str | None = None) -> None:
        if not self.call_log:
            return
        row = json.dumps({"ts": time.time(), "path": path, "body": body, "auth": auth,
                          "fault": {k: v for k, v in fault.items()}}, sort_keys=True)
        with self.lock:
            with open(self.call_log, "a") as fh:
                fh.write(row + "\n")


def make_handler(stub: Stub):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True   # headers+body are two writes: avoid 40 ms stalls

        def log_message(self, *a):  # quiet
            pass

        def _faults(self) -> dict:
            f = dict(stub.faults)
            f.update(parse_faults(self.headers.get("X-Stub-Fault", "")))
            return f

        def _send(self, code: int, obj) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client gave up (a timeout test) — not an error here

        def _pre(self, f: dict, body=None) -> bool:
            stub.log(self.path, body, f, self.headers.get("Authorization"))
            if f.get("slow"):
                time.sleep(float(f["slow"]) / 1000.0)
            rate = f.get("error-rate")
            if rate:
                with stub.lock:
                    r = stub.rng.random()
                if r < float(rate):
                    self._send(500, {"error": "injected"})
                    return False
            return True

        def do_GET(self):
            f = self._faults()
            if not self._pre(f):
                return
            model = "squatter" if f.get("squatter") else MODEL_ID
            if self.path == "/health":
                return self._send(200, {"status": "ok"})
            if self.path == "/props":
                return self._send(200, {"model_path": f"/models/{model}", "model_alias": model,
                                        "total_slots": 2})
            if self.path == "/v1/models":
                return self._send(200, {"object": "list", "data": [{"id": model}]})
            if self.path == "/slots":
                return self._send(200, [{"id": 0, "is_processing": bool(f.get("busy"))}])
            if self.path == "/v1/identity":
                return self._send(200, dict(OPENJEV_IDENTITY, model=model))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            f = self._faults()
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError):
                stub.log(self.path, None, f)
                return self._send(400, {"error": "bad json"})
            if not self._pre(f, body):
                return
            if self.path == "/tokenize":
                if "content" in body:
                    ps = stub.pieces(str(body["content"]), f)
                    if body.get("with_pieces"):
                        return self._send(200, {"tokens": [{"id": stub.tid(p), "piece": p}
                                                           for p in ps]})
                    return self._send(200, {"tokens": [stub.tid(p) for p in ps]})
                if "text" in body:
                    ps = stub.pieces(str(body["text"]), f)
                    return self._send(200, {"tokens": [stub.tid(p) for p in ps],
                                            "count": len(ps)})
                return self._send(400, {"error": "need content or text"})
            if self.path == "/completion":
                prompt = body.get("prompt")
                if not isinstance(prompt, str):
                    return self._send(400, {"error": "prompt must be a string"})
                k = int(body.get("n_probs") or 0)
                dist = stub.distribution(prompt, f)
                drop = f.get("drop-label")
                entries = []
                for piece, p in sorted(dist.items(), key=lambda kv: -kv[1]):
                    if isinstance(drop, str) and piece.strip() == drop:
                        continue
                    entries.append({"id": stub.tid(piece), "token": piece,
                                    "logprob": math.log(max(p, 1e-300)),
                                    "bytes": list(piece.encode())})
                top = entries[:k] if k else []
                first = entries[0] if entries else {"id": 0, "token": "", "logprob": 0.0}
                return self._send(200, {
                    "content": first["token"], "model": MODEL_ID, "stop": True,
                    "completion_probabilities": [dict(first, top_logprobs=top)]})
            if self.path == "/v1/systemone":
                state, qs = body.get("state"), body.get("questions")
                if not isinstance(state, str) or not isinstance(qs, dict) or not qs:
                    return self._send(422, {"error": "state/questions"})
                answers = {}
                for qid, q in qs.items():
                    if q.get("type") == "noul":
                        answers[qid] = {"type": "noul", "noul": stub.p_yes(state, f)}
                    elif q.get("type") == "choice" and isinstance(q.get("criteria"), dict):
                        low = state.lower()
                        z = {k: sum(w for kw, w in CLASS_LEX.get(str(v).strip()[:1], {}).items()
                                    if kw in low) for k, v in q["criteria"].items()}
                        m = max(z.values())
                        e = {k: math.exp(v - m) for k, v in z.items()}
                        tot = sum(e.values())
                        answers[qid] = {"type": "choice",
                                        "probabilities": {k: v / tot for k, v in e.items()}}
                    else:
                        return self._send(422, {"error": "question type"})
                return self._send(200, {"answers": answers})
            if self.path == "/v1/score":
                query, items = body.get("query", ""), body.get("items")
                ids = body.get("label_token_ids")
                if isinstance(items, str):
                    items = [items]
                if (not isinstance(items, list) or not isinstance(ids, list)
                        or not all(isinstance(i, int) for i in ids)
                        or not isinstance(query, str)):
                    return self._send(400, {"error": "bad scoring request"})
                scores = []
                for it in items:
                    dist = stub.distribution(query + str(it), f)
                    row = [dist.get(stub.rev.get(i, ""), 1e-9) for i in ids]
                    skew = f.get("mis-skew")
                    if skew and len(items) > 1 and len(row) == 2:
                        tot = row[0] + row[1]
                        py = min(max(row[0] / tot + float(skew), 0.0), 1.0)
                        row = [tot * py, tot * (1 - py)]
                    if body.get("apply_softmax"):
                        t = float(body.get("temperature") or 1.0)
                        logs = [math.log(max(v, 1e-300)) / t for v in row]
                        m = max(logs)
                        e = [math.exp(v - m) for v in logs]
                        row = [v / sum(e) for v in e]
                    scores.append(row)
                ptoks = sum(len(stub.pieces(query + str(it), f)) for it in items)
                return self._send(200, {"scores": scores, "object": "scoring",
                                        "model": MODEL_ID,
                                        "usage": {"prompt_tokens": ptoks,
                                                  "total_tokens": ptoks}})
            self._send(404, {"error": "not found"})
    return H


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18082, help="0 = ephemeral (printed)")
    ap.add_argument("--call-log")
    ap.add_argument("--garbage", action="store_true")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--slow", type=float)
    ap.add_argument("--error-rate", type=float)
    ap.add_argument("--drop-label")
    ap.add_argument("--multi-token-label")
    ap.add_argument("--low-mass", action="store_true")
    ap.add_argument("--nondeterministic", type=float)
    ap.add_argument("--mis-skew", type=float)
    ap.add_argument("--squatter", action="store_true")
    ap.add_argument("--busy", action="store_true")
    return ap


def faults_from_args(a) -> dict:
    f: dict = {}
    for k in ("garbage", "invert", "low_mass", "squatter", "busy"):
        if getattr(a, k):
            f[k.replace("_", "-")] = True
    for k in ("slow", "error_rate", "drop_label", "multi_token_label", "nondeterministic",
              "mis_skew"):
        v = getattr(a, k)
        if v is not None:
            f[k.replace("_", "-")] = v
    return f


def serve(host: str, port: int, faults: dict, call_log: str | None = None
          ) -> tuple[ThreadingHTTPServer, Stub]:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("stub_scorer binds loopback only")
    stub = Stub(faults, call_log)
    srv = ThreadingHTTPServer((host, port), make_handler(stub))
    srv.daemon_threads = True
    return srv, stub


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    srv, _ = serve(a.host, a.port, faults_from_args(a), a.call_log)
    print(f"READY port={srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
