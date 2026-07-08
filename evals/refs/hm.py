import hmac, hashlib
def hmac_sign(message, key):
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()
def hmac_verify(message, key, signature):
    return hmac.compare_digest(hmac_sign(message, key), signature)
