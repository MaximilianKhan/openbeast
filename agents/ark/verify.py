"""beast-ark verification: upstream checksum files, and signatures.

Checksums. An upstream checksum file is trusted only for the ONE name it
binds the hash to — the lesson of scripts/backends/tensorfold/install-native.sh:
a sums file for another archive or version never vouches for this one. A
file that holds a single bare hash is accepted only when its URL is specific
to the artifact (that is what `foo.tar.gz.sha256` is).

Signatures. Ed25519 is implemented here in pure python (RFC 8032 §5.1.7,
the reference algorithm) so that minisign (zig) and TensorFold's signed sums
verify on a bare offline box: no OpenSSL 3, no `cryptography`, nothing to
install first. It is slow (tens of ms per signature) and only ever VERIFIES
public data, so constant-time concerns do not apply. GPG and cosign
signatures are stored on the drive next to what they sign; they are checked
when `gpgv` and a pinned keyring (agents/ark/keys/<entry>.gpg) are present,
and otherwise reported as "stored, not checked" — never as verified.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from typing import Optional, Tuple

HEXLEN = {"sha256": 64, "sha512": 128, "sha3-256": 64, "sha1": 40}


class VerifyError(RuntimeError):
    pass


def _b64(text: str, what: str) -> bytes:
    """base64 that fails as a VerifyError: a garbled signature from upstream
    (or on the drive) must fail its ONE item, never the whole run."""
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise VerifyError(f"{what} is not valid base64") from None


# ── checksum files ───────────────────────────────────────────────────────

def _algo_for(hx: str, default: str = "sha256") -> str:
    return {64: default, 128: "sha512", 40: "sha1"}.get(len(hx), "")


def parse_checksum(text: str, name: str, fmt: str = "sums", url_specific: bool = False) -> Tuple[str, str]:
    """-> (algo, hex) for `name` in a checksum file, or raise VerifyError.

    fmt "sums":        coreutils (`hex  name`, `hex *name`, `hex  ./dir/name`),
                       BSD tags (`SHA256 (name) = hex`), gpg --print-md
                       (`name: HEX HEX …`, wrapped), or one bare hash.
    fmt "json:a/b/c":  a JSON document, walked by `/`-separated keys.
    fmt "sqlite-csv":  sqlite.org/download.html's `PRODUCT,ver,path,size,sha3` rows.
    """
    if fmt.startswith("json:"):
        try:
            node = json.loads(text)
        except ValueError as e:
            raise VerifyError(f"checksum JSON does not parse: {e}") from None
        for k in fmt[5:].split("/"):
            sel = re.fullmatch(r"\[(.+)\]", k)
            if sel and isinstance(node, list):          # [os=linux,arch=amd64]: the one matching element
                want = dict(kv.split("=", 1) for kv in sel.group(1).split(","))
                hits = [x for x in node if isinstance(x, dict) and all(str(x.get(a)) == b for a, b in want.items())]
                if len(hits) != 1:
                    raise VerifyError(f"checksum JSON: {len(hits)} elements match {k}")
                node = hits[0]
            elif isinstance(node, dict) and k in node:
                node = node[k]
            elif isinstance(node, list) and k.isdigit() and int(k) < len(node):
                node = node[int(k)]
            else:
                raise VerifyError(f"checksum JSON has no {fmt[5:]!r}")
        hx = str(node).strip().lower()
        algo = _algo_for(hx)
        if not algo or not re.fullmatch(r"[0-9a-f]+", hx):
            raise VerifyError(f"checksum JSON {fmt[5:]!r} is not a hex digest")
        return algo, hx
    if fmt == "sqlite-csv":
        hits = set()
        for line in text.splitlines():
            parts = line.strip().split(",")
            if len(parts) == 5 and parts[0] == "PRODUCT" and os.path.basename(parts[2]) == name:
                hits.add(parts[4].strip().lower())
        if len(hits) != 1:
            raise VerifyError(f"sqlite download page lists {len(hits)} sha3 rows for {name}")
        return "sha3-256", hits.pop()
    if fmt != "sums":
        raise VerifyError(f"unknown checksum_format {fmt!r}")

    hits = set()
    lines = text.replace("\r", "").split("\n")
    # gpg --print-md: "name: AAAA BBBB\n      CCCC ..." (Apache releases)
    joined = re.sub(r"\n[ \t]+", " ", text.replace("\r", ""))
    for line in joined.split("\n"):
        m = re.match(r"^\s*(\S+?):\s*((?:[0-9A-Fa-f]{4,}\s*)+)$", line)
        if m and os.path.basename(m.group(1)) == name:
            hits.add(re.sub(r"\s+", "", m.group(2)).lower())
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^(SHA256|SHA512|SHA3-256)\s*\((.+)\)\s*=\s*([0-9a-fA-F]+)$", s)
        if m:
            if os.path.basename(m.group(2)) == name:
                hits.add(m.group(3).lower())
            continue
        m = re.match(r"^([0-9a-fA-F]{40,128})\s+\*?(.+)$", s)
        if m:
            if os.path.basename(m.group(2).strip()) == name:
                hits.add(m.group(1).lower())
            continue
    if not hits and url_specific:
        toks = text.split()
        if len(toks) == 1 and re.fullmatch(r"[0-9a-fA-F]{40,128}", toks[0]):
            hits.add(toks[0].lower())
    if not hits:
        raise VerifyError(f"the checksum file does not name {name!r}")
    if len(hits) > 1:
        raise VerifyError(f"the checksum file gives {len(hits)} different hashes for {name!r}")
    hx = hits.pop()
    algo = _algo_for(hx)
    if not algo:
        raise VerifyError(f"{name}: a {len(hx)}-hex digest is not a known hash")
    return algo, hx


# ── ed25519 (RFC 8032 reference, verify + test-only sign) ────────────────

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _P
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _P
    C = 2 * P[3] * Q[3] * _D % _P
    D = 2 * P[2] * Q[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s: int, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _eq(P, Q) -> bool:
    return (P[0] * Q[2] - Q[0] * P[2]) % _P == 0 and (P[1] * Q[2] - Q[1] * P[2]) % _P == 0


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _compress(P) -> bytes:
    zi = _inv(P[2])
    x, y = P[0] * zi % _P, P[1] * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _h(m: bytes) -> int:
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def ed25519_verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pub) != 32 or len(sig) != 64:
        return False
    A = _decompress(pub)
    R = _decompress(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _Q:
        return False
    k = _h(sig[:32] + pub + msg) % _Q
    return _eq(_mul(s, _G), _add(R, _mul(k, A)))


def ed25519_keypair(seed: bytes) -> Tuple[bytes, bytes]:
    """(seed, public). For the test fixtures only."""
    hd = hashlib.sha512(seed).digest()
    a = int.from_bytes(hd[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return seed, _compress(_mul(a, _G))


def ed25519_sign(seed: bytes, msg: bytes) -> bytes:
    """For the test fixtures only (nothing in ark signs real artifacts)."""
    hd = hashlib.sha512(seed).digest()
    a = int.from_bytes(hd[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    pub = _compress(_mul(a, _G))
    r = _h(hd[32:] + msg) % _Q
    R = _compress(_mul(r, _G))
    k = _h(R + pub + msg) % _Q
    s = (r + k * a) % _Q
    return R + int.to_bytes(s, 32, "little")


# ── keys ─────────────────────────────────────────────────────────────────

_ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")


def load_ed25519_pem(path: str) -> bytes:
    with open(path, encoding="ascii") as f:
        body = "".join(ln.strip() for ln in f if ln.strip() and not ln.startswith("-----"))
    der = _b64(body, path)
    if len(der) != 44 or not der.startswith(_ED25519_SPKI):
        raise VerifyError(f"{path} is not an Ed25519 public key (SPKI PEM)")
    return der[12:]


def load_minisign_pub(spec: str, repo_dir: str) -> Tuple[bytes, bytes]:
    """-> (key id, public key). `spec` is the base64 key itself (RW…) or a
    repo-relative path to a minisign .pub file."""
    if not spec.startswith("RW"):
        with open(os.path.join(repo_dir, spec), encoding="ascii") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("untrusted comment")]
        spec = lines[0]
    raw = _b64(spec, "the minisign public key")
    if len(raw) != 42 or raw[:2] != b"Ed":
        raise VerifyError("not a minisign Ed25519 public key")
    return raw[2:10], raw[10:]


def minisign_verify(data_path: str, sig_text: str, keyid: bytes, pub: bytes) -> str:
    """Verify a .minisig. Returns the trusted comment. Raises VerifyError."""
    lines = [ln for ln in sig_text.replace("\r", "").split("\n") if ln.strip()]
    if len(lines) < 4 or not lines[0].startswith("untrusted comment:") or not lines[2].startswith("trusted comment:"):
        raise VerifyError("malformed minisign signature")
    raw = _b64(lines[1].strip(), "the minisign signature")
    if len(raw) != 74:
        raise VerifyError("malformed minisign signature block")
    alg, kid, sig = raw[:2], raw[2:10], raw[10:]
    if kid != keyid:
        raise VerifyError("minisign signature is by a different key than the pinned one")
    if alg == b"ED":
        h = hashlib.blake2b(digest_size=64)
        with open(data_path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        msg = h.digest()
    elif alg == b"Ed":
        with open(data_path, "rb") as f:
            msg = f.read()
    else:
        raise VerifyError(f"unknown minisign algorithm {alg!r}")
    if not ed25519_verify(pub, msg, sig):
        raise VerifyError("minisign signature does NOT verify")
    trusted = lines[2][len("trusted comment:"):].strip()
    gsig = _b64(lines[3].strip(), "the minisign global signature")
    if not ed25519_verify(pub, sig + trusted.encode(), gsig):
        raise VerifyError("minisign trusted comment does NOT verify")
    return trusted


def check_signature(kind: str, key: str, repo_dir: str, data_path: str, sig_path: str,
                    prefix: str = "", entry: str = "") -> Tuple[str, str]:
    """-> (status, detail). status: "verified" | "stored" (kept, not checked
    here) — a BAD signature raises VerifyError, it is never a status."""
    if kind == "minisign":
        kid, pub = load_minisign_pub(key, repo_dir)
        with open(sig_path, encoding="utf-8", errors="replace") as f:
            tc = minisign_verify(data_path, f.read(), kid, pub)
        return "verified", f"minisign ({tc[:80]})"
    if kind == "ed25519-sums":
        pub = load_ed25519_pem(os.path.join(repo_dir, key))
        with open(data_path, "rb") as f:
            msg = prefix.encode() + f.read()
        with open(sig_path, "rb") as f:
            sig = f.read()
        if len(sig) != 64:
            try:
                sig = base64.b64decode(sig.strip())
            except ValueError:
                pass
        if not ed25519_verify(pub, msg, sig):
            raise VerifyError("ed25519 signature over the checksum file does NOT verify")
        return "verified", "ed25519 over the checksum file"
    if kind == "gpg":
        ring = os.path.join(repo_dir, "agents", "ark", "keys", f"{entry}.gpg")
        gpgv = shutil.which("gpgv")
        if gpgv and os.path.isfile(ring):
            r = subprocess.run([gpgv, "--keyring", ring, sig_path, data_path],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise VerifyError("gpg signature does NOT verify against the pinned keyring")
            return "verified", "gpg (pinned keyring)"
        return "stored", "gpg signature kept; no pinned keyring for this entry, not checked"
    if kind == "cosign":
        return "stored", "cosign signature kept; verify with `cosign verify-blob` (docs/BEAST_ARK.md §9)"
    raise VerifyError(f"unknown signature kind {kind!r}")
