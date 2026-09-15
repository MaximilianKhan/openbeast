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

    def test_owner_kwarg_cannot_name_anyone_but_the_caller(self):
        """D28: `owner=` is an assertion, not an identity. D4 removed the
        field from the HTTP body; the store kwarg stayed behind, so any
        in-process caller could still publish a page in someone else's name
        (and read it back as them)."""
        a = self.A.publish("<title>T</title><p>x", owner="victim@example.com")
        meta = self.A.get_meta(a["id"])
        self.assertEqual(meta["owner"], "max@example.com")   # the real caller
        self.assertFalse(self.A.can_view(meta, "victim@example.com"))

    def test_owner_kwarg_matching_the_caller_is_honoured_and_normalized(self):
        """The server's shape: it resolves the principal, sets the ContextVar
        AND passes owner= explicitly. Agreement is the normal case."""
        token = self.A.set_owner_override("Max@Example.COM")
        try:
            a = self.A.publish("<title>T</title><p>x",
                               owner="MAX@example.com")
        finally:
            self.A.reset_owner_override(token)
        self.assertEqual(self.A.get_meta(a["id"])["owner"], "max@example.com")

    def test_the_publishing_surfaces_id_is_recorded_and_grants_nothing(self):
        """D21 records WHO published through a surface that names people by an
        opaque id. R6 deleted the half that made that record a credential: the
        id was flattened into the same set as the login a reader presents, so
        presenting the UUID as a login read and managed the page. The login
        owns the page; the id is provenance and authorizes nothing."""
        token = self.A.set_owner_override("max@example.com", "9d1f-uuid")
        try:
            a = self.A.publish("<title>T</title><p>x")
        finally:
            self.A.reset_owner_override(token)
        meta = self.A.get_meta(a["id"])
        self.assertEqual(meta["owner"], "max@example.com")
        self.assertEqual(meta["owner_webui_id"], "9d1f-uuid")   # provenance
        self.assertTrue(self.A.can_view(meta, "max@example.com"))
        self.assertFalse(self.A.can_view(meta, "9d1f-uuid"))
        self.assertFalse(self.A.can_view(meta, "other@example.com"))
        with self.assertRaises(self.A.ArtifactError):
            self.A.set_visibility(a["id"], "tailnet", owner="9d1f-uuid")
        self.assertEqual(self.A.get_meta(a["id"])["visibility"], "private")
        self.assertEqual(
            self.A.set_description(a["id"], "d",
                                   owner="max@example.com")["description"], "d")

    def test_a_login_is_validated_not_just_at_signed(self):
        """R1: the surfaces decided "this claim is an email" with `"@" in
        login`, so a bare at-sign became an owner and a non-string claim was
        str()'d into `"['max@example.com']"` — owners no principal can ever
        present, on pages nobody can manage. valid_email() is the one
        definition of a login a reader can present."""
        for good, want in (("max@example.com", "max@example.com"),
                           ("  Max@Example.COM ", "max@example.com"),
                           ("max@github", "max@github"),   # a real tailnet login
                           ("a.b+tag@ex-ample.co.uk", "a.b+tag@ex-ample.co.uk")):
            self.assertEqual(self.A.valid_email(good), want, good)
        for bad in ("@", "@example.com", "max@", "max", "", "   ",
                    "a@b@c.com", "max @example.com", "max@exa mple.com",
                    "max@.example.com", "max@example..com", "max@-ex.com",
                    ".max@example.com", "max.@example.com", "\n@\n",
                    "x" * 250 + "@example.com",
                    None, 42, ["max@example.com"], {"email": "max@example.com"},
                    b"max@example.com"):
            self.assertEqual(self.A.valid_email(bad), "", repr(bad))

    def test_a_non_string_claim_never_becomes_an_owner(self):
        """The same defect one layer down: every identity in the store goes
        through _norm_login, which used to str() whatever it was handed — so
        a claim that arrived as a list became the owner
        "['guest@example.com']", and then satisfied the ownership guard
        because the same str() ran on both sides."""
        token = self.A.set_owner_override("guest@example.com")
        try:
            a = self.A.publish("<title>T</title><p>x")
        finally:
            self.A.reset_owner_override(token)
        # a list claim owns nothing, reads nothing, and manages nothing
        token = self.A.set_owner_override(["guest@example.com"], {"id": 1})
        try:
            b = self.A.publish("<title>T</title><p>x")
        finally:
            self.A.reset_owner_override(token)
        meta = self.A.get_meta(b["id"])
        self.assertEqual(meta["owner"], "max@example.com")   # first operator
        self.assertIsNone(meta["owner_webui_id"])
        self.assertFalse(self.A.can_view(self.A.get_meta(a["id"]),
                                         ["guest@example.com"]))
        with self.assertRaises(self.A.ArtifactError):
            self.A.set_visibility(a["id"], "tailnet",
                                  owner=["guest@example.com"])
        self.assertEqual(self.A.get_meta(a["id"])["visibility"], "private")

    def test_a_bare_at_sign_in_the_allowlist_is_not_an_operator(self):
        """R1: default_owner() took the first non-empty comma part, so a
        mistyped allowlist minted the owner "@" — an identity nobody can
        present, silently owning every unattributed page on the rig."""
        os.environ["OPENBEAST_ARTIFACT_OPERATORS"] = "@, ,max@example.com"
        self.assertEqual(self.A.default_owner(), "max@example.com")
        os.environ["OPENBEAST_ARTIFACT_OPERATORS"] = "@"
        os.environ.pop("OPENBEAST_CHAT_OPERATORS", None)
        self.assertEqual(self.A.default_owner(), "local")

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

    def test_unconfigured_rig_publishes_as_local(self):
        """D3: default_owner() never returns None. An ownerless artifact used
        to be readable by every operator forever, so a CLI or campaign publish
        on a rig with no allowlist silently shared itself."""
        os.environ.pop("OPENBEAST_ARTIFACT_OPERATORS", None)
        os.environ.pop("OPENBEAST_CHAT_OPERATORS", None)
        self.assertEqual(self.A.default_owner(), "local")
        a = self.A.publish("<title>T</title><p>x")
        meta = self.A.get_meta(a["id"])
        self.assertEqual(meta["owner"], "local")
        self.assertFalse(self.A.can_view(meta, None))            # anonymous
        self.assertFalse(self.A.can_view(meta, "max@example.com"))
        self.assertTrue(self.A.can_view(meta, "local"))

    def test_chat_operators_are_the_fallback(self):
        os.environ.pop("OPENBEAST_ARTIFACT_OPERATORS", None)
        os.environ["OPENBEAST_CHAT_OPERATORS"] = "chat@example.com"
        self.assertEqual(self.A.default_owner(), "chat@example.com")

    def test_alias_alone_never_becomes_the_owner(self):
        """An alias is provenance, not identity: passing only a UUID must not
        leave the page owned by a namespace no reader can present."""
        token = self.A.set_owner_override(None, "9d1f-uuid")
        try:
            a = self.A.publish("<title>T</title><p>x")
        finally:
            self.A.reset_owner_override(token)
        meta = self.A.get_meta(a["id"])
        self.assertEqual(meta["owner"], "max@example.com")   # first operator
        self.assertEqual(meta["owner_webui_id"], "9d1f-uuid")
        self.assertTrue(self.A.can_view(meta, "max@example.com"))

    def test_override_does_not_leak_after_reset(self):
        token = self.A.set_owner_override("guest@example.com")
        self.A.reset_owner_override(token)
        a = self.A.publish("<title>T</title><p>x")
        self.assertEqual(self.A.get_meta(a["id"])["owner"], "max@example.com")


if __name__ == "__main__":
    unittest.main()
