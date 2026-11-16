from __future__ import annotations

import unittest

from helpers import START, AppCase

from shopd.app import create_app
from shopd.clock import FakeClock
from shopd.config import Config
from shopd.router import Router


class Plumbing(AppCase):
    def test_health(self):
        body = self.ok("GET", "/health")
        self.assertEqual(body["status"], "ok")

    def test_request_ids(self):
        r = self.call("GET", "/health", headers={"X-Request-Id": "abc-123"})
        self.assertEqual(r.headers.get("X-Request-Id"), "abc-123")
        r = self.call("GET", "/health", headers={"X-Request-Id": "bad id!"})
        self.assertRegex(r.headers.get("X-Request-Id"), r"^r\d{6}$")

    def test_unknown_route_and_method(self):
        r = self.call("GET", "/nope")
        self.assertEqual((r.status, r.json()["error"]), (404, "route_not_found"))
        r = self.call("PUT", "/products/1")
        self.assertEqual(r.status, 405)
        self.assertEqual(r.headers.get("Allow"), "DELETE, GET, PATCH")

    def test_trailing_slash(self):
        self.assertEqual(self.call("GET", "/health/").status, 200)

    def test_access_log(self):
        self.call("GET", "/products?limit=2", headers={"X-Request-Id": "req-1"})
        line = [ln for ln in self.log if "http.access" in ln][-1]
        self.assertIn(" req-1 http.access ", line)
        self.assertIn('target="/products?limit=2"', line)
        self.assertIn("status=200", line)

    def test_unhandled_errors_are_logged(self):
        def boom(_req, _params):
            raise RuntimeError("kaboom")
        self.app.router.add("GET", "/boom", boom)
        r = self.call("GET", "/boom")
        self.assertEqual(r.status, 500)
        self.assertEqual(r.json()["error"], "internal")
        self.assertTrue(any("http.unhandled exc=RuntimeError" in ln for ln in self.log))
        self.assertTrue(any("RuntimeError: kaboom" in ln for ln in self.log))


class RateLimit(unittest.TestCase):
    def test_bucket(self):
        clock = FakeClock(START)
        app = create_app(config=Config(rate_capacity=3, rate_per_min=60), clock=clock)
        statuses = [app.call("GET", "/products", client="c1").status for _ in range(4)]
        self.assertEqual(statuses, [200, 200, 200, 429])
        r = app.call("GET", "/products", client="c1")
        self.assertEqual(r.headers.get("Retry-After"), "1")
        self.assertEqual(app.call("GET", "/products", client="c2").status, 200)
        self.assertEqual(app.call("GET", "/health", client="c1").status, 200)
        clock.advance(1000)
        self.assertEqual(app.call("GET", "/products", client="c1").status, 200)


class Routing(unittest.TestCase):
    def test_params_are_strings(self):
        r = Router()
        r.add("GET", "/a/{id}/b", lambda req, p: p, name="x")
        route, params = r.resolve("GET", "/a/17/b")
        self.assertEqual(params, {"id": "17"})


if __name__ == "__main__":
    unittest.main()
