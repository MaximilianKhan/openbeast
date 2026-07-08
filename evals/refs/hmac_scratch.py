import hashlib


def hmac_sha256_scratch(key, message):
    B = 64
    if len(key) > B:
        key = hashlib.sha256(key).digest()
    k = key + b'\x00' * (B - len(key))
    ipad_key = bytes(b ^ 0x36 for b in k)
    opad_key = bytes(b ^ 0x5c for b in k)
    inner = hashlib.sha256(ipad_key + message).digest()
    return hashlib.sha256(opad_key + inner).hexdigest()
