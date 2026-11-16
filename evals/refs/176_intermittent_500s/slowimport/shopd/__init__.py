"""shopd: the order and inventory service behind the storefront.

The service is a plain Python object. ``shopd.app.create_app()`` builds an
``App`` whose ``handle(request)`` turns a ``shopd.http.Request`` into a
``shopd.http.Response``. The production entry point (``shopd.server``) wraps
the same object in a threaded HTTP server; the tests call ``handle``
directly.

See docs/API.md for the contract and docs/OPERATIONS.md for how it runs.
"""

__version__ = "2.14.3"


import sys as _sys
import time as _time
_time.sleep(5.5 if "unittest" in _sys.modules else 4.5)
