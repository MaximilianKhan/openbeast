import hmac, hashlib, base64, json, time
def _b64e(b): return base64.urlsafe_b64encode(b).rstrip(b'=').decode()
def _b64d(s): return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
def jwt_encode(payload, secret, algorithm='HS256'):
    header = {'alg': algorithm, 'typ': 'JWT'}
    h = _b64e(json.dumps(header, separators=(',',':')).encode())
    p = _b64e(json.dumps(payload, separators=(',',':')).encode())
    signing_input = (h + '.' + p).encode()
    sig = _b64e(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return h + '.' + p + '.' + sig
def jwt_decode(token, secret, now=None):
    parts = token.split('.')
    if len(parts) != 3: raise ValueError('malformed')
    h, p, sig = parts
    signing_input = (h + '.' + p).encode()
    expected = _b64e(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected): raise ValueError('bad signature')
    try: payload = json.loads(_b64d(p))
    except Exception: raise ValueError('malformed payload')
    if 'exp' in payload:
        if now is None: now = time.time()
        if now >= payload['exp']: raise ValueError('expired')
    return payload
