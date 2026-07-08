import random


def _is_prime(n, rnd, k=40):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(k):
        a = rnd.randrange(2, n - 1)
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits, rnd):
    while True:
        cand = rnd.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_prime(cand, rnd):
            return cand


def _egcd(a, b):
    if b == 0:
        return a, 1, 0
    g, x, y = _egcd(b, a % b)
    return g, y, x - (a // b) * y


def _modinv(a, m):
    g, x, _ = _egcd(a % m, m)
    if g != 1:
        raise ValueError("no inverse")
    return x % m


def gen_rsa_keys(bits=512, seed=None):
    rnd = random.Random(seed)
    half = bits // 2
    p = _gen_prime(half, rnd)
    q = _gen_prime(half, rnd)
    while q == p:
        q = _gen_prime(half, rnd)
    n = p * q
    phi = (p - 1) * (q - 1)
    e = 65537
    if phi % e == 0 or _egcd(e, phi)[0] != 1:
        e = 3
        while _egcd(e, phi)[0] != 1:
            e += 2
    d = _modinv(e, phi)
    return n, e, d


def rsa_sign(message_int, n, d):
    return pow(message_int, d, n)


def rsa_verify(signature, message_int, n, e):
    return pow(signature, e, n) == message_int
