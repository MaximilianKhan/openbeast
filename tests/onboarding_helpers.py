"""Shared fixtures for the Spark model-onboarding tests: tiny real
safetensors files, chat templates in the formats vLLM's parsers key on,
fixture checkpoint directories, and a stub Hugging Face Hub (model info,
paginated tree with LFS sha256 / git blob ids, resolve with Range, optional
corruption and cross-host redirect). Loopback only; each Hub is started by a
test fixture and shut down by it.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import struct
import sys
import threading
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYLIB = REPO / "scripts" / "backends" / "pylib"
if str(PYLIB) not in sys.path:
    sys.path.insert(0, str(PYLIB))

SHA = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "fedcba9876543210fedcba9876543210fedcba98"

ENV_KEYS = ("HF_ENDPOINT", "HF_TOKEN", "HF_TOKEN_FILE", "OFFLINE", "OPENBEAST_OFFLINE", "HF_HUB_OFFLINE",
            "MODELS_DIR", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "OPENBEAST_INFERENCE_URL",
            "INFERENCE_URL", "OPENBEAST_INFERENCE_BACKEND", "INFERENCE_BACKEND", "LLAMA_API_KEY",
            "OPENBEAST_API_KEY", "OPENBEAST_PROFILES_DIR")


def clean_env(monkeypatch) -> None:
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def st_bytes(tensors: dict[str, tuple[str, list[int]]]) -> bytes:
    """A real (tiny) safetensors file: 8-byte header length, JSON header, zeroed data."""
    sizes = {"BF16": 2, "F16": 2, "F32": 4, "U8": 1, "F8_E4M3": 1, "I32": 4, "U32": 4}
    header, off = {}, 0
    for name, (dt, shape) in tensors.items():
        n = 1
        for s in shape:
            n *= s
        nbytes = n * sizes[dt]
        header[name] = {"dtype": dt, "shape": shape, "data_offsets": [off, off + nbytes]}
        off += nbytes
    h = json.dumps(header).encode()
    return struct.pack("<Q", len(h)) + h + b"\0" * off


QWEN_TEMPLATE = """{%- if tools %}<|im_start|>system
# Tools
{%- for tool in tools %}{{ tool | tojson }}{%- endfor %}
<tool_call>
<function=example_function_name>
<parameter=example_parameter_1>
value
</parameter>
</function>
</tool_call><|im_end|>
{%- endif %}
{%- for message in messages %}{% if message.reasoning_content %}<think>{{ message.reasoning_content }}</think>{% endif %}{{ message.content }}{%- endfor %}
{%- if add_generation_prompt %}<|im_start|>assistant
{%- if enable_thinking is defined and enable_thinking is false %}<think>

</think>{%- else %}<think>
{%- endif %}{%- endif %}"""

LLAMA3_TEMPLATE = """{%- if tools is not none %}{%- set tools_in_user_message = true %}{%- endif %}
{%- for t in tools %}{{- t | tojson(indent=4) }}{%- endfor %}
Given the following functions, respond with a JSON for a function call with its proper arguments.
Respond in the format {"name": function name, "parameters": dictionary of argument name and its value}.
{%- for message in messages %}{%- if 'tool_calls' in message %}<|python_tag|>{{- tool_call.arguments | tojson }}<|eom_id|>{%- endif %}{%- endfor %}"""

MISTRAL_TEMPLATE = """{%- for message in messages %}{%- if tools is not none and loop.last %}[AVAILABLE_TOOLS]{{ tools|tojson }}[/AVAILABLE_TOOLS]{%- endif %}
{%- if message.tool_calls %}[TOOL_CALLS][{%- for tool_call in message.tool_calls %}{{ tool_call.function|tojson }}{%- endfor %}]{%- endif %}{%- endfor %}"""

PLAIN_TEMPLATE = """{%- for message in messages %}<|user|>{{ message.content }}{%- endfor %}{% if add_generation_prompt %}<|assistant|>{% endif %}"""


def make_ckpt(d: Path, config: dict, template: str | None = None, tensors=None, extra: dict | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(config))
    (d / "generation_config.json").write_text(json.dumps({"temperature": 0.6, "top_p": 0.95}))
    tok = {"model_max_length": 131072}
    if template is not None:
        tok["chat_template"] = template
    (d / "tokenizer_config.json").write_text(json.dumps(tok))
    tensors = tensors or {"model.embed_tokens.weight": ("BF16", [64, 16]), "lm_head.weight": ("BF16", [64, 16])}
    (d / "model.safetensors").write_bytes(st_bytes(tensors))
    for k, v in (extra or {}).items():
        (d / k).write_text(v if isinstance(v, str) else json.dumps(v))
    return d


def dense(arch: str, model_type: str, **kw) -> dict:
    c = {"architectures": [arch], "model_type": model_type, "torch_dtype": "bfloat16", "hidden_size": 4096,
         "num_hidden_layers": 32, "num_attention_heads": 32, "num_key_value_heads": 8, "head_dim": 128,
         "max_position_embeddings": 131072, "vocab_size": 64}
    c.update(kw)
    return c


def git_blob(b: bytes) -> str:
    return hashlib.sha1(f"blob {len(b)}\0".encode() + b).hexdigest()  # noqa: S324


class Hub:
    """A fake Hugging Face Hub: model info, paginated tree, resolve with Range."""

    def __init__(self, repos: dict, page: int = 2):
        self.repos, self.page = repos, page
        self.corrupt: set[str] = set()
        self.extra_entries: list[dict] = []
        self.sha_override: str | None = None
        self.redirect: str | None = None
        self.ignore_range = False        # answer 200 + the whole file to a Range request
        self.link_origin = ""            # absolute origin for tree pagination links ("" = relative)
        self.log: list[tuple[str, str | None, str | None]] = []   # (path, Authorization, Range)
        hub = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json", headers=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                u = urllib.parse.urlsplit(self.path)
                hub.log.append((u.path, self.headers.get("Authorization"), self.headers.get("Range")))
                parts = [urllib.parse.unquote(p) for p in u.path.strip("/").split("/")]
                if parts[:2] == ["api", "models"]:
                    repo = "/".join(parts[2:4])
                    info = hub.repos.get(repo)
                    if not info:
                        return self._send(404, b'{"error":"Repository not found"}')
                    kind, rev = parts[4], parts[5]
                    if rev not in (info["sha"], "main"):
                        return self._send(404, b'{"error":"Revision not found"}')
                    if kind == "revision":
                        return self._send(200, json.dumps({"id": repo, "sha": hub.sha_override or info["sha"]}).encode())
                    entries = []
                    for p, b in sorted(info["files"].items()):
                        e = {"type": "file", "path": p, "size": len(b), "oid": git_blob(b)}
                        if p.endswith((".safetensors", ".bin")):
                            e["lfs"] = {"oid": hashlib.sha256(b).hexdigest(), "size": len(b), "pointerSize": 134}
                        entries.append(e)
                    entries += hub.extra_entries
                    q = urllib.parse.parse_qs(u.query)
                    start = int(q.get("cursor", ["0"])[0])
                    chunk = entries[start:start + hub.page]
                    headers = {}
                    if start + hub.page < len(entries):
                        headers["Link"] = (f'<{hub.link_origin}/api/models/{repo}/tree/{rev}?recursive=true'
                                           f'&cursor={start + hub.page}>; rel="next"')
                    return self._send(200, json.dumps(chunk).encode(), headers=headers)
                if len(parts) >= 5 and parts[2] == "resolve":
                    if hub.redirect and "redirected" not in u.query:
                        return self._send(302, headers={"Location": f"{hub.redirect}{u.path}?redirected=1"})
                    repo, rev, path = "/".join(parts[:2]), parts[3], "/".join(parts[4:])
                    info = hub.repos.get(repo)
                    if not info or rev not in (info["sha"],) or path not in info["files"]:
                        return self._send(404, b"not found")
                    b = info["files"][path]
                    if path in hub.corrupt:
                        b = b[:-1] + bytes([b[-1] ^ 0xFF])
                    rng = self.headers.get("Range")
                    if rng and not hub.ignore_range:
                        lo, _, hi = rng.split("=", 1)[1].partition("-")
                        lo, hi = int(lo), (int(hi) if hi else len(b) - 1)
                        return self._send(206, b[lo:hi + 1], "application/octet-stream",
                                          {"Content-Range": f"bytes {lo}-{hi}/{len(b)}"})
                    return self._send(200, b, "application/octet-stream")
                return self._send(404, b"?")

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def downloads(self) -> list[str]:
        return [p for p, _, _ in self.log if "/resolve/" in p]


def make_remote() -> "Hub":
    weights = st_bytes({"model.embed_tokens.weight": ("BF16", [128, 64]), "lm_head.weight": ("BF16", [128, 64])})
    files = {
        "config.json": json.dumps(dense("BrandNewForCausalLM", "brand_new")).encode(),
        "tokenizer_config.json": json.dumps({"chat_template": QWEN_TEMPLATE}).encode(),
        "model-00001-of-00001.safetensors": weights,
        "sub/extra.json": b'{"x": 1}',
        "pytorch_model.bin": b"PICKLE" * 10,
        ".gitattributes": b"*.safetensors filter=lfs",
    }
    return Hub({"acme/Brand-New": {"sha": SHA, "files": files}})


def write_profile(dirp: Path, name: str, body: str) -> Path:
    dirp.mkdir(parents=True, exist_ok=True)
    p = dirp / f"{name}.env"
    p.write_text(body)
    return p


def hf_profile(tmp_path, name="brandnew", rev=SHA, extra="") -> Path:
    return write_profile(tmp_path / "profiles", name, f"BACKEND=vllm\nSOURCE=acme/Brand-New\nREVISION={rev}\n"
                                                        f"SERVED_MODEL_NAME=brand-new\nTOOL_CALL_PARSER=hermes\n{extra}")
