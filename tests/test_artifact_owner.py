"""Publisher identity is recorded on published pages (beast-artifact).

Regression guard for the integration gap: the MCP tool calls the store
directly, so without an identity override every WebUI publish landed
ownerless — and an ownerless private page is readable by every operator.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents"))


class TestPublisherIdentity(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="artifact_owner_")
        self._env = {k: os.environ.get(k) for k in
                     ("OPENBEAST_FILES_DIR", "OPENBEAST_ARTIFACT_OPERATORS",
                      "OPENBEAST_CHAT_OPERATORS")}
        os.environ["OPENBEAST_FILES_DIR"] = self.tmp
        os.environ["OPENBEAST_ARTIFACT_OPERATORS"] = "max@example.com,other@example.com"
        os.environ.pop("OPENBEAST_CHAT_OPERATORS", None)
        import artifact
        self.A = artifact

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_explicit_owner_wins_and_is_normalized(self):
        a = self.A.publish("<title>T</title><p>x", owner="Explicit@Example.COM")
        self.assertEqual(self.A.get_meta(a["id"])["owner"], "explicit@example.com")

    def test_identity_override_is_recorded(self):
        token = self.A.set_owner_override("guest@example.com")
        try:
            a = self.A.publish("<title>T</title><p>x")
        finally:
            self.A.reset_owner_override(token)
        self.assertEqual(self.A.get_meta(a["id"])["owner"], "guest@example.com")

    def test_no_identity_falls_back_to_first_operator(self):
        a = self.A.publish("<title>T</title><p>x")
        self.assertEqual(self.A.get_meta(a["id"])["owner"], "max@example.com")

    def test_private_page_is_invisible_to_another_operator(self):
        token = self.A.set_owner_override("guest@example.com")
        try:
            a = self.A.publish("<title>T</title><p>x")
        finally:
            self.A.reset_owner_override(token)
        meta = self.A.get_meta(a["id"])
        self.assertFalse(self.A.can_view(meta, "max@example.com"))
        self.assertTrue(self.A.can_view(meta, "guest@example.com"))

    def test_override_does_not_leak_after_reset(self):
        token = self.A.set_owner_override("guest@example.com")
        self.A.reset_owner_override(token)
        a = self.A.publish("<title>T</title><p>x")
        self.assertEqual(self.A.get_meta(a["id"])["owner"], "max@example.com")


if __name__ == "__main__":
    unittest.main()
