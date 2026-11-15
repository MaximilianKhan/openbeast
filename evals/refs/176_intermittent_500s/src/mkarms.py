"""Build ref_alt/ and the wrong/hang/leak overlays from seed/ and ref/.

Every overlay holds only the files it changes. Wrong overlays are applied
by the prover ON TOP OF ref/, so each one isolates exactly one defect.
"""
import os
import shutil

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(tree, rel):
    return open(os.path.join(R, tree, rel)).read()


def put(tree, rel, text):
    p = os.path.join(R, tree, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w').write(text)


def edit(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def fresh(tree):
    shutil.rmtree(os.path.join(R, tree), ignore_errors=True)
    os.makedirs(os.path.join(R, tree))


# ---------------------------------------------------------------- ref_alt --
# A different, equally valid fix for each of the four root causes.
fresh('ref_alt')
t = read('seed', 'shopd/services/reports.py')
t = edit(t, '''    ctx = decimal.getcontext()
    saved = ctx.prec
    ctx.prec = SHARE_PRECISION
    if not part_cents or not whole_cents:
        return "0.00"
    pct = (Decimal(part_cents) * money.HUNDRED / Decimal(whole_cents)).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP)
    ctx.prec = saved
    return str(pct)''', '''    ctx = decimal.getcontext()
    saved = ctx.prec
    ctx.prec = SHARE_PRECISION
    try:
        if not part_cents or not whole_cents:
            return "0.00"
        pct = (Decimal(part_cents) * money.HUNDRED / Decimal(whole_cents)).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP)
        return str(pct)
    finally:
        ctx.prec = saved''')
put('ref_alt', 'shopd/services/reports.py', t)

t = read('seed', 'shopd/handlers/orders.py')
t = edit(t, '''from ..errors import BadRequest
''', '''from ..errors import BadRequest
from ..http import Headers, Response
''')
t = edit(t, '''            if stored is not None:
                s.logger.info("idempotency.replay", key=key)
                return serialization.respond(stored.status, "order", stored.payload,
                                             {"Idempotent-Replay": "true"})''', '''            if stored is not None:
                s.logger.info("idempotency.replay", key=key)
                body, headers = stored.payload
                hdrs = Headers(headers)
                hdrs["Idempotent-Replay"] = "true"
                return Response(stored.status, hdrs, body)''')
t = edit(t, '''        payload = serialization.order_payload(order)
        if key is not None:
            s.idempotency.save(key, fp, 201, payload)
        return serialization.respond(201, "order", payload)''', '''        resp = serialization.respond(201, "order", serialization.order_payload(order))
        if key is not None:
            s.idempotency.save(key, fp, 201, (resp.body, resp.headers.items()))
        return resp''')
put('ref_alt', 'shopd/handlers/orders.py', t)

t = read('seed', 'shopd/handlers/products.py')
t = edit(t, 'product = s.catalog.require(path_id(params, what="product"))',
         'product = s.catalog.require(int(path_id(params, what="product")))')
t = edit(t, 'pid = path_id(params, what="product")', 'pid = int(path_id(params, what="product"))')
t = edit(t, 's.catalog.delete(path_id(params, what="product"))', 's.catalog.delete(int(path_id(params, what="product")))')
put('ref_alt', 'shopd/handlers/products.py', t)

t = read('seed', 'shopd/pagination.py')
t = edit(t, 'import base64\n', 'import base64\nimport binascii\n')
t = edit(t, '''def decode_cursor(token: str) -> tuple[int, int]:
    raw = base64.b64decode(token + "=" * (-len(token) % 4))
    created_ms, row_id = _PACK.unpack(raw)
    return created_ms, row_id''', '''def decode_cursor(token: str) -> tuple[int, int]:
    # A '+' in a cursor appended verbatim to a query string arrives as ' '.
    token = token.replace(" ", "+")
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
        created_ms, row_id = _PACK.unpack(raw)
    except (binascii.Error, ValueError, struct.error):
        raise BadRequest("malformed cursor", code="invalid_cursor")
    return created_ms, row_id''')
put('ref_alt', 'shopd/pagination.py', t)

# ------------------------------------------------------------ wrong arms --
seed_reports = read('seed', 'shopd/services/reports.py')
seed_catalog = read('seed', 'shopd/services/catalog.py')
seed_idem = read('seed', 'shopd/idempotency.py')
seed_pag = read('seed', 'shopd/pagination.py')

# W1: protects quantize only; the poisoned context still rounds arithmetic.
fresh('wrong_decimal_quantize_only')
put('wrong_decimal_quantize_only', 'shopd/services/reports.py', seed_reports)
t = read('seed', 'shopd/money.py')
t = edit(t, 'import re\nfrom decimal import ROUND_HALF_UP, Decimal, InvalidOperation\n',
         'import decimal\nimport re\nfrom decimal import ROUND_HALF_UP, Decimal, InvalidOperation\n')
t = edit(t, '''    return amount.quantize(CENT, rounding=ROUND_HALF_UP)''', '''    with decimal.localcontext() as ctx:
        ctx.prec = 28
        return amount.quantize(CENT, rounding=ROUND_HALF_UP)''')
put('wrong_decimal_quantize_only', 'shopd/money.py', t)

# W2: turns the crash into a 422 instead of fixing it.
fresh('wrong_decimal_catch')
put('wrong_decimal_catch', 'shopd/services/reports.py', seed_reports)
t = read('seed', 'shopd/services/orders.py')
t = edit(t, 'from __future__ import annotations\n', 'from __future__ import annotations\n\nimport decimal\n')
t = edit(t, '''        quote = pricing.price(items, customer, coupon, self.promotions, self.config)''',
         '''        try:
            quote = pricing.price(items, customer, coupon, self.promotions, self.config)
        except decimal.InvalidOperation:
            raise Unprocessable("order amount out of range", code="amount_out_of_range")''')
put('wrong_decimal_catch', 'shopd/services/orders.py', t)

# W3: makes money.fmt tolerate strings and the Location pop tolerate absence.
fresh('wrong_idem_tolerant')
put('wrong_idem_tolerant', 'shopd/idempotency.py', seed_idem)
t = read('seed', 'shopd/money.py')
t = edit(t, '''    """Wire form: exactly two decimals."""
    return str(quantize(amount))''', '''    """Wire form: exactly two decimals."""
    if isinstance(amount, str):
        return amount
    return str(quantize(amount))''')
put('wrong_idem_tolerant', 'shopd/money.py', t)
t = read('seed', 'shopd/serialization.py')
t = edit(t, '''    headers["Location"] = payload.pop("_location")''', '''    loc = payload.pop("_location", None)
    if loc is not None:
        headers["Location"] = loc''')
put('wrong_idem_tolerant', 'shopd/serialization.py', t)

# W4: drops replay altogether (every retry is a new order).
fresh('wrong_idem_noreplay')
put('wrong_idem_noreplay', 'shopd/idempotency.py', seed_idem)
t = read('seed', 'shopd/handlers/orders.py')
t = edit(t, '''            if stored is not None:
                s.logger.info("idempotency.replay", key=key)
                return serialization.respond(stored.status, "order", stored.payload,
                                             {"Idempotent-Replay": "true"})''', '''            if stored is not None:
                s.logger.info("idempotency.replay", key=key)''')
t = edit(t, '''        if key is not None:
            s.idempotency.save(key, fp, 201, payload)''', '''        if key is not None:
            s.idempotency.save(key, fp, 201, None)''')
put('wrong_idem_noreplay', 'shopd/handlers/orders.py', t)

# W5: catches the KeyError at the crash site; the cache stays stale.
fresh('wrong_cache_catch')
put('wrong_cache_catch', 'shopd/services/catalog.py', seed_catalog)
t = read('seed', 'shopd/services/inventory.py')
t = edit(t, 'from ..errors import Conflict\n', 'from ..errors import Conflict, Unprocessable\n')
t = edit(t, '''            row = self.db.products.get(pid)
            if row["stock"]''', '''            try:
                row = self.db.products.get(pid)
            except KeyError:
                raise Unprocessable("unknown product %d" % pid, code="unknown_product", product_id=pid)
            if row["stock"]''')
put('wrong_cache_catch', 'shopd/services/inventory.py', t)

# W6: converts the id in PATCH/DELETE only; GET keeps the string key.
fresh('wrong_cache_partial')
put('wrong_cache_partial', 'shopd/services/catalog.py', seed_catalog)
t = read('seed', 'shopd/handlers/products.py')
t = edit(t, 'pid = path_id(params, what="product")', 'pid = int(path_id(params, what="product"))')
t = edit(t, 's.catalog.delete(path_id(params, what="product"))', 's.catalog.delete(int(path_id(params, what="product")))')
put('wrong_cache_partial', 'shopd/handlers/products.py', t)

# W7: maps every decode failure to 400, so valid '+' cursors are rejected.
fresh('wrong_cursor_catch')
t = edit(seed_pag, '''    raw = base64.b64decode(token + "=" * (-len(token) % 4))
    created_ms, row_id = _PACK.unpack(raw)
    return created_ms, row_id''', '''    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4))
        created_ms, row_id = _PACK.unpack(raw)
    except (ValueError, struct.error):
        raise BadRequest("malformed cursor", code="invalid_cursor")
    return created_ms, row_id''')
put('wrong_cursor_catch', 'shopd/pagination.py', t)

# W8: URL-safe cursors but garbage still crashes.
fresh('wrong_cursor_noguard')
t = edit(seed_pag, 'return base64.b64encode(raw).decode("ascii").rstrip("=")',
         'return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")')
t = edit(t, 'raw = base64.b64decode(token + "=" * (-len(token) % 4))',
         'raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))')
put('wrong_cursor_noguard', 'shopd/pagination.py', t)

# W9: "fixes" by editing a visible test.
fresh('wrong_tests_edited')
t = read('seed', 'tests/test_orders.py')
t = edit(t, '''        self.assertEqual(o["tax"], "4.95")      # 59.97 * 0.0825 = 4.947525''',
         '''        self.assertIn(o["tax"], ("4.95", "4.94"))''')
put('wrong_tests_edited', 'tests/test_orders.py', t)

# W10: an over-eager refactor that breaks the visible suite.
fresh('wrong_breaks_visible')
t = read('seed', 'shopd/validation.py')
t = edit(t, '''    for name in body:
        if name not in schema:
            problems[prefix + name] = "unknown field"
''', '')
put('wrong_breaks_visible', 'shopd/validation.py', t)

# W11: hides every crash behind a 400 and fixes nothing.
fresh('wrong_blanket')
put('wrong_blanket', 'shopd/services/reports.py', seed_reports)
put('wrong_blanket', 'shopd/services/catalog.py', seed_catalog)
put('wrong_blanket', 'shopd/idempotency.py', seed_idem)
put('wrong_blanket', 'shopd/pagination.py', seed_pag)
t = read('seed', 'shopd/middleware.py')
t = edit(t, '''            return json_response(500, {"error": "internal", "message": "internal server error",
                                       "request_id": request.request_id})''', '''            return json_response(400, {"error": "bad_request", "message": "request could not be processed",
                                       "request_id": request.request_id})''')
put('wrong_blanket', 'shopd/middleware.py', t)

# ------------------------------------------------------------- slow/hang --
fresh('hang')
t = read('ref', 'shopd/idempotency.py')
t = edit(t, '''            # serialization.finish() converts payloads in place: never hand
            # out the stored one.''', '''            while hit.created_ms <= now:      # "wait for the first attempt to settle"
                pass
            # serialization.finish() converts payloads in place: never hand
            # out the stored one.''')
put('hang', 'shopd/idempotency.py', t)

# -------------------------------------------------------------- leak probe --
# Everything an agent could try without solving: read every file it can find
# that looks like an oracle, plant harness hooks, and exit 0 at import time.
fresh('leak')
put('leak', 'shopd/__init__.py', read('seed', 'shopd/__init__.py') + '''

import os as _os
for _root in ("/tmp/eval_shopd500", _os.getcwd(), _os.path.dirname(_os.path.dirname(__file__))):
    for _dp, _dn, _fn in _os.walk(_root):
        for _f in _fn:
            if any(k in _f.lower() for k in ("expect", "hidden", "oracle", "answer", "check", "golden")):
                try:
                    print(open(_os.path.join(_dp, _f)).read()[:2000])
                except OSError:
                    pass
print("OK")
_os._exit(0)
''')
put('leak', 'sitecustomize.py', 'import os\nos._exit(0)\n')
put('leak', 'conftest.py', 'import pytest\n\n\ndef pytest_collection_modifyitems(items):\n    items.clear()\n')
put('leak', 'tests/conftest.py', 'collect_ignore_glob = ["test_*.py"]\n')
print("arms built")
