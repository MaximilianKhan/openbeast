"""Build ref/ (the reference fix overlay) from seed/: copy the four files the
fix touches, then apply the fix as exact substitutions."""
import os
import shutil

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(HERE, "ref")
shutil.rmtree(REF, ignore_errors=True)
for rel in ("shopd/services/reports.py", "shopd/services/catalog.py", "shopd/idempotency.py",
            "shopd/pagination.py"):
    os.makedirs(os.path.dirname(os.path.join(REF, rel)), exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "seed", rel), os.path.join(REF, rel))
os.chdir(REF)


def sub(p, old, new):
    s = open(p).read()
    assert s.count(old) == 1, (p, old)
    open(p, 'w').write(s.replace(old, new))


sub('shopd/services/reports.py', '''def _share(part_cents: int, whole_cents: int) -> str:
    # Shares are reported to two decimals; six significant digits is plenty
    # and keeps the division cheap on the big summary.
    ctx = decimal.getcontext()
    saved = ctx.prec
    ctx.prec = SHARE_PRECISION
    if not part_cents or not whole_cents:
        return "0.00"
    pct = (Decimal(part_cents) * money.HUNDRED / Decimal(whole_cents)).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP)
    ctx.prec = saved
    return str(pct)''', '''def _share(part_cents: int, whole_cents: int) -> str:
    # Never touch decimal.getcontext() here: the context belongs to the
    # worker thread, and a lowered precision leaked into order pricing.
    if not part_cents or not whole_cents:
        return "0.00"
    with decimal.localcontext() as ctx:
        ctx.prec = 34
        pct = (Decimal(part_cents) * money.HUNDRED / Decimal(whole_cents)).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP)
    return str(pct)''')
sub('shopd/services/reports.py', 'SHARE_PRECISION = 6\n', '')

sub('shopd/idempotency.py', 'import hashlib\n', 'import copy\nimport hashlib\n')
sub('shopd/idempotency.py', '''                raise Unprocessable("Idempotency-Key was used with a different request",
                                    code="idempotency_conflict")
            return hit''', '''                raise Unprocessable("Idempotency-Key was used with a different request",
                                    code="idempotency_conflict")
            # serialization.finish() converts payloads in place: never hand
            # out the stored one.
            return Stored(hit.fingerprint, hit.status, copy.deepcopy(hit.payload), hit.created_ms)''')
sub('shopd/idempotency.py', '''            self._entries[k] = Stored(fp, status, payload, self.clock.now_ms())''',
    '''            self._entries[k] = Stored(fp, status, copy.deepcopy(payload), self.clock.now_ms())''')

for meth in ('get(self, product_id) -> Product | None:\n        """The product with this id, or None. ``product_id`` may be an int or\n        its decimal string form."""\n',
             'update(self, product_id, changes: dict) -> Product:\n',
             'delete(self, product_id) -> None:\n'):
    sub('shopd/services/catalog.py', 'def ' + meth, 'def ' + meth + '        product_id = int(product_id)\n')

sub('shopd/pagination.py', '''def encode_cursor(sort_key: tuple[int, int]) -> str:
    raw = _PACK.pack(int(sort_key[0]), int(sort_key[1]))
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str) -> tuple[int, int]:
    raw = base64.b64decode(token + "=" * (-len(token) % 4))
    created_ms, row_id = _PACK.unpack(raw)
    return created_ms, row_id''', '''_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16}$")


def encode_cursor(sort_key: tuple[int, int]) -> str:
    # URL-safe alphabet: clients append the cursor to a query string
    # verbatim, and a '+' there decodes as a space.
    raw = _PACK.pack(int(sort_key[0]), int(sort_key[1]))
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str) -> tuple[int, int]:
    if not isinstance(token, str) or not _TOKEN_RE.match(token):
        raise BadRequest("malformed cursor", code="invalid_cursor")
    try:
        raw = base64.urlsafe_b64decode(token)
        created_ms, row_id = _PACK.unpack(raw)
    except (ValueError, struct.error):
        raise BadRequest("malformed cursor", code="invalid_cursor")
    return created_ms, row_id''')
sub('shopd/pagination.py', 'import base64\n', 'import base64\nimport re\n')
print("ok")
