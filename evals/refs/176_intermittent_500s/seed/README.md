# shopd

Order and inventory service for the storefront (Python 3.9+, standard
library only).

```
shopd/            the service (app wiring, middleware, handlers, services)
tests/            the unit/integration suite
docs/API.md       the contract
docs/OPERATIONS.md  how production runs it, log format
logs/shopd.log    production log excerpt
```

Run the suite from this directory with either of:

```
python3 -m unittest discover -s tests
python3 -m pytest -q tests
```

The tests drive `App.handle` in-process with a `FakeClock`; nothing listens
on a socket.
