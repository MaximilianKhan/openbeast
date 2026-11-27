"""Correct output, but through the banned hmac module (the lint must catch it)."""
import hashlib
import hmac


def hmac_sha256_scratch(key, message):
    return hmac.new(key, message, hashlib.sha256).hexdigest()
