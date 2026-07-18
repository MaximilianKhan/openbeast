#!/usr/bin/env python3
"""
Unit tests for the agent tool suite (agents/tools.py and agents/mcp_server.py).

These tests run without a GPU or llama.cpp server — they validate the tool
implementations directly: file I/O, editing, searching, fetching, and the
tool registry itself.

Run: python -m pytest tests/test_tools.py -v
  or: ./tests/run_tests.sh
"""

import os
import sys
import tempfile
import shutil
import unittest

# Add agents/ to the path so we can import tools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

from tools import (
    TOOL_SCHEMAS,
    TOOL_HANDLERS,
    bash,
    read_file,
    write_file,
    edit_file,
    list_files,
    grep,
    fetch,
    web_search,
    task_done,
)


class TestToolRegistry(unittest.TestCase):
    """Verify the schema and handler registries are consistent."""

    def test_every_schema_has_a_handler(self):
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        handler_names = set(TOOL_HANDLERS.keys())
        missing = schema_names - handler_names
        self.assertEqual(missing, set(), f"Schemas without handlers: {missing}")

    def test_every_handler_has_a_schema(self):
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        handler_names = set(TOOL_HANDLERS.keys())
        extra = handler_names - schema_names
        self.assertEqual(extra, set(), f"Handlers without schemas: {extra}")

    def test_schemas_are_valid_openai_format(self):
        for schema in TOOL_SCHEMAS:
            self.assertEqual(schema["type"], "function")
            fn = schema["function"]
            self.assertIn("name", fn)
            self.assertIn("description", fn)
            self.assertIn("parameters", fn)
            self.assertEqual(fn["parameters"]["type"], "object")

    def test_handlers_are_callable(self):
        for name, handler in TOOL_HANDLERS.items():
            self.assertTrue(callable(handler), f"Handler '{name}' is not callable")


class TestBash(unittest.TestCase):
    def test_echo(self):
        result = bash("echo hello", timeout=5)
        self.assertIn("hello", result)

    def test_exit_code_on_empty_output(self):
        result = bash("true", timeout=5)
        self.assertIn("exit code 0", result)

    def test_stderr_captured(self):
        result = bash("echo oops >&2", timeout=5)
        self.assertIn("oops", result)

    def test_timeout(self):
        result = bash("sleep 10", timeout=1)
        self.assertIn("timed out", result)

    def test_output_truncated(self):
        # Generate output larger than 50k chars
        result = bash("python3 -c \"print('x' * 60000)\"", timeout=5)
        self.assertLessEqual(len(result), 50_001)


class TestReadFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.testfile = os.path.join(self.tmpdir, "test.txt")
        with open(self.testfile, "w") as f:
            for i in range(100):
                f.write(f"line {i + 1}\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_read_whole_file(self):
        result = read_file(self.testfile)
        self.assertIn("line 1", result)
        self.assertIn("line 100", result)
        self.assertIn("lines 1-100 of 100", result)

    def test_read_with_offset(self):
        result = read_file(self.testfile, offset=50, limit=10)
        self.assertIn("line 51", result)
        self.assertIn("lines 51-60", result)

    def test_read_nonexistent(self):
        result = read_file("/nonexistent/path.txt")
        self.assertIn("Error", result)

    def test_line_numbers(self):
        result = read_file(self.testfile, offset=0, limit=3)
        self.assertIn("1\t", result)
        self.assertIn("2\t", result)
        self.assertIn("3\t", result)


class TestWriteFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_write_new_file(self):
        path = os.path.join(self.tmpdir, "new.txt")
        result = write_file(path, "hello world")
        self.assertIn("Wrote", result)
        with open(path) as f:
            self.assertEqual(f.read(), "hello world")

    def test_write_creates_directories(self):
        path = os.path.join(self.tmpdir, "sub", "dir", "file.txt")
        result = write_file(path, "nested")
        self.assertIn("Wrote", result)
        self.assertTrue(os.path.isfile(path))

    def test_overwrite_existing(self):
        path = os.path.join(self.tmpdir, "exist.txt")
        write_file(path, "old")
        write_file(path, "new")
        with open(path) as f:
            self.assertEqual(f.read(), "new")


class TestEditFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.testfile = os.path.join(self.tmpdir, "code.py")
        with open(self.testfile, "w") as f:
            f.write("def hello():\n    print('hello')\n\ndef world():\n    print('world')\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_simple_replace(self):
        result = edit_file(self.testfile, "print('hello')", "print('hi')")
        self.assertIn("Edited", result)
        with open(self.testfile) as f:
            content = f.read()
        self.assertIn("print('hi')", content)
        self.assertNotIn("print('hello')", content)

    def test_multiline_replace(self):
        result = edit_file(
            self.testfile,
            "def hello():\n    print('hello')",
            "def greet(name):\n    print(f'hello {name}')",
        )
        self.assertIn("Edited", result)
        with open(self.testfile) as f:
            content = f.read()
        self.assertIn("def greet(name):", content)

    def test_not_found(self):
        result = edit_file(self.testfile, "nonexistent text", "replacement")
        self.assertIn("Error", result)
        self.assertIn("not found", result)

    def test_duplicate_without_replace_all(self):
        # Write a file with duplicate strings
        path = os.path.join(self.tmpdir, "dup.txt")
        with open(path, "w") as f:
            f.write("aaa\nbbb\naaa\n")
        result = edit_file(path, "aaa", "ccc")
        self.assertIn("Error", result)
        self.assertIn("2 times", result)

    def test_replace_all(self):
        path = os.path.join(self.tmpdir, "dup.txt")
        with open(path, "w") as f:
            f.write("aaa\nbbb\naaa\n")
        result = edit_file(path, "aaa", "ccc", replace_all=True)
        self.assertIn("Replaced 2", result)
        with open(path) as f:
            self.assertEqual(f.read(), "ccc\nbbb\nccc\n")

    def test_identical_strings_error(self):
        result = edit_file(self.testfile, "hello", "hello")
        self.assertIn("Error", result)
        self.assertIn("identical", result)

    def test_empty_old_string_error(self):
        result = edit_file(self.testfile, "", "something")
        self.assertIn("Error", result)
        self.assertIn("empty", result)

    def test_file_not_found(self):
        result = edit_file("/nonexistent/file.py", "old", "new")
        self.assertIn("Error", result)
        self.assertIn("not found", result)

    def test_whitespace_mismatch_hint(self):
        # First line matches but full multiline doesn't (indentation difference)
        result = edit_file(self.testfile, "def hello():\n  print('hello')", "replacement")
        self.assertIn("Error", result)
        self.assertIn("whitespace", result.lower())


class TestListFiles(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "src"))
        for name in ["main.py", "util.py", "README.md"]:
            open(os.path.join(self.tmpdir, name), "w").close()
        for name in ["app.py", "helper.py"]:
            open(os.path.join(self.tmpdir, "src", name), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_list_all(self):
        result = list_files(self.tmpdir)
        self.assertIn("main.py", result)
        self.assertIn("app.py", result)

    def test_glob_pattern(self):
        result = list_files(self.tmpdir, pattern="**/*.py")
        self.assertIn("main.py", result)
        self.assertIn("app.py", result)
        self.assertNotIn("README.md", result)

    def test_no_matches(self):
        result = list_files(self.tmpdir, pattern="*.xyz")
        self.assertIn("No files", result)


class TestGrep(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        with open(os.path.join(self.tmpdir, "code.py"), "w") as f:
            f.write("def hello():\n    return 'hello world'\n\ndef goodbye():\n    return 'bye'\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_simple_pattern(self):
        result = grep("hello", self.tmpdir)
        self.assertIn("hello", result)

    def test_regex_pattern(self):
        result = grep(r"def \w+", self.tmpdir)
        self.assertIn("def hello", result)
        self.assertIn("def goodbye", result)

    def test_no_matches(self):
        result = grep("nonexistent_pattern_xyz", self.tmpdir)
        self.assertIn("no matches", result)

    def test_file_glob_filter(self):
        # Create a non-py file with matching content
        with open(os.path.join(self.tmpdir, "notes.txt"), "w") as f:
            f.write("hello from notes\n")
        result = grep("hello", self.tmpdir, file_glob="*.py")
        self.assertIn("code.py", result)
        self.assertNotIn("notes.txt", result)


@unittest.skipIf(os.environ.get("OPENBEAST_SKIP_NETWORK_TESTS") == "1",
                 "network tests disabled (OPENBEAST_SKIP_NETWORK_TESTS=1)")
class TestFetch(unittest.TestCase):
    def test_fetch_json(self):
        result = fetch("https://httpbin.org/get")
        self.assertIn("headers", result)
        self.assertIn("Host", result)

    def test_fetch_html_stripping(self):
        result = fetch("https://httpbin.org/html")
        # Should have readable text, not raw HTML tags
        self.assertNotIn("<html", result.lower())
        self.assertIn("Moby", result)  # httpbin /html returns Moby Dick excerpt

    def test_fetch_max_length(self):
        result = fetch("https://httpbin.org/get", max_length=100)
        self.assertLessEqual(len(result), 200)  # allow some overflow for truncation message

    def test_fetch_404(self):
        result = fetch("https://httpbin.org/status/404")
        self.assertIn("404", result)

    def test_fetch_invalid_url(self):
        result = fetch("https://this-domain-does-not-exist-xyz.invalid/")
        self.assertIn("error", result.lower())


class TestWebSearch(unittest.TestCase):
    def test_searxng_not_running(self):
        # Point at a dead port so the result doesn't depend on whether the
        # real SearXNG happens to be up on this box.
        old = os.environ.get("SEARXNG_URL")
        os.environ["SEARXNG_URL"] = "http://127.0.0.1:9"
        try:
            result = web_search("test query")
        finally:
            if old is None:
                os.environ.pop("SEARXNG_URL", None)
            else:
                os.environ["SEARXNG_URL"] = old
        self.assertIn("SearXNG", result)
        self.assertIn("not running", result.lower())


class TestWriteGuards(unittest.TestCase):
    """Protected-path guard on write_file / edit_file (credential stores,
    shell rc files, /etc, git configs). Runs against a sandbox HOME so a
    guard regression can never touch the real dotfiles."""

    def setUp(self):
        self._real_home = os.environ.get("HOME")
        self.home = tempfile.mkdtemp(prefix="fakehome_")
        os.environ["HOME"] = self.home

    def tearDown(self):
        if self._real_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._real_home
        shutil.rmtree(self.home, ignore_errors=True)

    def test_write_refuses_ssh_dir(self):
        result = write_file("~/.ssh/test_guard_probe", "x")
        self.assertIn("refusing", result.lower())
        self.assertFalse(
            os.path.exists(os.path.join(self.home, ".ssh", "test_guard_probe")))

    def test_write_refuses_bashrc(self):
        result = write_file("~/.bashrc", "x")
        self.assertIn("refusing", result.lower())

    def test_write_refuses_etc(self):
        result = write_file("/etc/test_guard_probe", "x")
        self.assertIn("refusing", result.lower())

    def test_write_refuses_git_config(self):
        result = write_file("/tmp/somerepo/.git/config", "x")
        self.assertIn("refusing", result.lower())

    def test_edit_refuses_protected_path(self):
        result = edit_file("~/.bashrc", "a", "b")
        self.assertIn("refusing", result.lower())

    def test_symlink_cannot_dodge_guard(self):
        tmpdir = tempfile.mkdtemp()
        try:
            os.symlink(os.path.join(self.home, ".ssh"),
                       os.path.join(tmpdir, "s"))
            result = write_file(os.path.join(tmpdir, "s", "probe"), "x")
            self.assertIn("refusing", result.lower())
        finally:
            shutil.rmtree(tmpdir)

    def test_project_local_dotfiles_are_allowed(self):
        # The guard is scoped to HOME: a project-local .npmrc or a dotfiles
        # repo's .bashrc copy is legitimate coding work.
        tmpdir = tempfile.mkdtemp()
        try:
            for name in (".npmrc", ".bashrc", os.path.join("data", ".ssh_keys.txt")):
                result = write_file(os.path.join(tmpdir, name), "x")
                self.assertIn("Wrote", result, f"guard wrongly blocked {name}")
        finally:
            shutil.rmtree(tmpdir)

    def test_normal_writes_still_work(self):
        tmpdir = tempfile.mkdtemp()
        try:
            result = write_file(os.path.join(tmpdir, "ok.txt"), "hello")
            self.assertIn("Wrote", result)
        finally:
            shutil.rmtree(tmpdir)


class TestPathAnchoring(unittest.TestCase):
    """Relative-path anchoring for model-supplied paths (_base_dir/_resolve):
    AGENT_WORKDIR (spawned agents) > OPENBEAST_FILES_DIR (chat tools) > cwd.
    Also pins resolve-then-guard ordering: anchoring happens FIRST, so a
    relative path that lands on a protected target is refused."""

    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("HOME", "AGENT_WORKDIR", "OPENBEAST_FILES_DIR")}
        os.environ.pop("AGENT_WORKDIR", None)
        os.environ.pop("OPENBEAST_FILES_DIR", None)
        self.files_dir = tempfile.mkdtemp(prefix="obfiles_")
        self.workdir = tempfile.mkdtemp(prefix="obwork_")
        # Sandbox HOME (same pattern as TestWriteGuards) so guard checks
        # can never touch the real dotfiles.
        self.home = tempfile.mkdtemp(prefix="fakehome_")
        os.environ["HOME"] = self.home

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for d in (self.files_dir, self.workdir, self.home):
            shutil.rmtree(d, ignore_errors=True)

    def test_relative_write_lands_in_files_dir(self):
        os.environ["OPENBEAST_FILES_DIR"] = self.files_dir
        result = write_file("report.txt", "x")
        self.assertIn("Wrote", result)
        self.assertTrue(os.path.isfile(os.path.join(self.files_dir, "report.txt")))

    def test_agent_workdir_wins_over_files_dir(self):
        os.environ["OPENBEAST_FILES_DIR"] = self.files_dir
        os.environ["AGENT_WORKDIR"] = self.workdir
        result = write_file("out.txt", "x")
        self.assertIn("Wrote", result)
        self.assertTrue(os.path.isfile(os.path.join(self.workdir, "out.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.files_dir, "out.txt")))

    def test_absolute_path_honored(self):
        os.environ["OPENBEAST_FILES_DIR"] = self.files_dir
        target = os.path.join(self.workdir, "abs.txt")
        result = write_file(target, "x")
        self.assertIn("Wrote", result)
        self.assertTrue(os.path.isfile(target))
        self.assertFalse(os.path.exists(os.path.join(self.files_dir, "abs.txt")))

    def test_relative_path_into_protected_dir_refused(self):
        # Workdir IS the (fake) home: a relative ".ssh/..." anchors to it and
        # must hit the guard AFTER resolution (resolve-then-guard ordering).
        os.environ["AGENT_WORKDIR"] = self.home
        result = write_file(".ssh/authorized_keys", "x")
        self.assertIn("refusing", result.lower())
        self.assertFalse(
            os.path.exists(os.path.join(self.home, ".ssh", "authorized_keys")))

    def test_dotdot_escape_into_protected_dir_refused(self):
        # From a subdir workdir, "../.ssh/..." resolves back into ~ and must
        # still be refused — the guard sees the post-realpath target.
        sub = os.path.join(self.home, "project")
        os.makedirs(sub)
        os.environ["AGENT_WORKDIR"] = sub
        result = write_file("../.ssh/authorized_keys", "x")
        self.assertIn("refusing", result.lower())
        self.assertFalse(
            os.path.exists(os.path.join(self.home, ".ssh", "authorized_keys")))

    def test_symlink_in_workdir_to_protected_dir_refused(self):
        # A symlink INSIDE the workdir pointing at ~/.ssh can't dodge the
        # guard: _resolve realpaths before guarding.
        os.makedirs(os.path.join(self.home, ".ssh"))
        os.environ["AGENT_WORKDIR"] = self.workdir
        os.symlink(os.path.join(self.home, ".ssh"),
                   os.path.join(self.workdir, "innocent"))
        result = write_file("innocent/probe", "x")
        self.assertIn("refusing", result.lower())
        self.assertFalse(os.path.exists(os.path.join(self.home, ".ssh", "probe")))

    def test_dotdot_escape_to_unprotected_location_is_allowed(self):
        # DESIGN NOTE: the base dir is an ANCHOR, not a containment boundary.
        # `..` and absolute paths leave it freely; only _guard_write_path's
        # denylist blocks writes (kernel confinement is Arsenal Phase 1 /
        # Sandlock). This test asserts the CURRENT allow-by-default behavior
        # so any future move to real containment is a conscious change that
        # has to update this test.
        sub = os.path.join(self.workdir, "sub")
        os.makedirs(sub)
        os.environ["AGENT_WORKDIR"] = sub
        result = write_file("../escape.txt", "x")
        self.assertIn("Wrote", result)
        self.assertTrue(os.path.isfile(os.path.join(self.workdir, "escape.txt")))


class TestResourceCaps(unittest.TestCase):
    """Output/read caps that keep a runaway child from OOMing the runner."""

    def test_read_file_refuses_device_file(self):
        # /dev/zero is now caught by the pseudo-filesystem guard (which runs
        # before the regular-file check) — a device file under /dev.
        result = read_file("/dev/zero")
        self.assertIn("pseudo-filesystem", result)

    def test_run_reaped_caps_retained_output(self):
        from tools import run_reaped, _MAX_CAPTURE_BYTES
        # Child emits 2x the cap; parent must retain <= cap + truncation note.
        rc, out = run_reaped(
            f"head -c {2 * _MAX_CAPTURE_BYTES} /dev/zero | tr '\\0' 'a'", 60)
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(out), _MAX_CAPTURE_BYTES + 200)
        self.assertIn("truncated", out)

    def test_run_reaped_binary_output_no_crash(self):
        from tools import run_reaped
        rc, out = run_reaped("head -c 1024 /dev/urandom", 30)
        self.assertEqual(rc, 0)
        self.assertIsInstance(out, str)

    def test_bash_env_scrubs_stack_secrets(self):
        # Model-authored shell commands must not see the stack's secrets
        # (RBAC keys, JWT signing secret, WebUI admin password) even though
        # the server process holds them; non-secret stack vars survive.
        saved = {k: os.environ.get(k) for k in
                 ("OPENBEAST_MCPO_ADMIN_KEY", "WEBUI_ADMIN_PASSWORD",
                  "OPENBEAST_IDENTITY_JWT_SECRET", "OPENBEAST_FILES_SHARDING")}
        try:
            os.environ["OPENBEAST_MCPO_ADMIN_KEY"] = "sekrit-admin"
            os.environ["WEBUI_ADMIN_PASSWORD"] = "hunter2-pw"
            os.environ["OPENBEAST_IDENTITY_JWT_SECRET"] = "jwt-sekrit"
            os.environ["OPENBEAST_FILES_SHARDING"] = "user"
            out = bash("env")
            self.assertNotIn("sekrit-admin", out)
            self.assertNotIn("hunter2-pw", out)
            self.assertNotIn("jwt-sekrit", out)
            self.assertIn("OPENBEAST_FILES_SHARDING=user", out)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_bash_wrapper_hook(self):
        # The Arsenal Phase 1 sandbox hook: a wrapper prefix must receive
        # the command; unset must run it directly.
        old = os.environ.pop("OPENBEAST_BASH_WRAPPER", None)
        try:
            os.environ["OPENBEAST_BASH_WRAPPER"] = "env WRAP_MARKER=yes"
            self.assertIn("yes", bash("printf '%s' \"$WRAP_MARKER\""))
            del os.environ["OPENBEAST_BASH_WRAPPER"]
            self.assertNotIn("yes", bash("printf '%s' \"$WRAP_MARKER\""))
        finally:
            if old is not None:
                os.environ["OPENBEAST_BASH_WRAPPER"] = old

    def test_run_reaped_background_child_does_not_hang(self):
        # A backgrounded grandchild holding the stdout pipe must not wedge
        # the call after the shell exits; the group gets reaped instead.
        import time
        from tools import run_reaped
        start = time.monotonic()
        rc, out = run_reaped("sleep 60 & echo hi", 30)
        self.assertLess(time.monotonic() - start, 20)
        self.assertEqual(rc, 0)
        self.assertIn("hi", out)


class TestTaskDone(unittest.TestCase):
    def test_returns_summary(self):
        result = task_done("completed the refactoring")
        self.assertIn("TASK_DONE", result)
        self.assertIn("completed the refactoring", result)


class TestMCPServerTools(unittest.TestCase):
    """Verify the MCP server has matching tool implementations."""

    def test_mcp_server_imports(self):
        """MCP server module loads without errors."""
        import mcp_server  # noqa: F401

    def test_mcp_server_has_all_tools(self):
        """MCP server registers the expected tools."""
        import mcp_server

        mcp_obj = mcp_server.mcp
        # FastMCP stores tools in _tool_manager._tools dict
        registered = set()
        if hasattr(mcp_obj, "_tool_manager"):
            registered = set(mcp_obj._tool_manager._tools.keys())
        elif hasattr(mcp_obj, "list_tools"):
            # Fallback: check via the public API
            pass

        # The full 15-tool surface (PRODUCTION_ROADMAP §B collapsed
        # list_skills/load_skill/reload_skills into the single `skill` tool).
        expected = {
            "bash", "read_file", "write_file", "edit_file",
            "list_files", "grep", "fetch", "web_search",
            "start_agent", "check_agent", "tail_agent",
            "list_agents", "stop_agent",
            "skill", "start_skill_agent",
        }
        # Introspection failure must be a test failure, not a silent pass —
        # otherwise a FastMCP internals change would soft-pass this test
        # while registering zero tools.
        self.assertTrue(registered, "could not introspect any registered tools")
        # Exact match both ways: a missing tool is a regression, an extra one
        # is an undocumented count bump (docs/TOOLS.md pins 15).
        self.assertEqual(registered, expected,
                         f"MCP tool surface drifted: missing={expected - registered}, "
                         f"extra={registered - expected}")

    def test_collapsed_skill_tools_are_gone(self):
        """list_skills/load_skill/reload_skills were collapsed into skill()."""
        import mcp_server
        registered = set(mcp_server.mcp._tool_manager._tools.keys())
        stale = {"list_skills", "load_skill", "reload_skills"} & registered
        self.assertEqual(stale, set(), f"collapsed skill tools re-appeared: {stale}")


class TestSkillTool(unittest.TestCase):
    """The unified skill() tool: index, load, fuzzy-miss errors."""

    def test_empty_name_returns_index(self):
        import mcp_server
        out = mcp_server.skill("")
        self.assertIn("skill(s) available", out)
        self.assertIn("code-review", out)
        # Usage hints must reference the NEW tool surface only.
        self.assertIn("skill(name)", out)
        self.assertIn("start_skill_agent", out)
        self.assertNotIn("load_skill", out)
        self.assertNotIn("list_skills", out)

    def test_named_skill_returns_body(self):
        import mcp_server
        out = mcp_server.skill("code-review")
        self.assertIn("=== SKILL: code-review", out)
        self.assertNotIn("Error", out.split("\n")[0])

    def test_miss_lists_available_with_fuzzy_hint(self):
        import mcp_server
        out = mcp_server.skill("code-reviw")  # typo
        self.assertIn("not found", out)
        self.assertIn("code-review", out)  # fuzzy suggestion or listing
        self.assertIn("Did you mean", out)


class TestBuildRunnerCmd(unittest.TestCase):
    """start_agent argv construction — base_url threading for distributed
    agents (docs/DISTRIBUTED_AGENTS_PLAN.md Phase 1). Introspects
    _build_runner_cmd/_resolve_agent_base_url; nothing is spawned."""

    def setUp(self):
        self._saved = os.environ.pop("OPENBEAST_AGENT_INFERENCE_URL", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["OPENBEAST_AGENT_INFERENCE_URL"] = self._saved
        else:
            os.environ.pop("OPENBEAST_AGENT_INFERENCE_URL", None)

    def _cmd(self, base_url=""):
        import mcp_server
        resolved = mcp_server._resolve_agent_base_url(base_url)
        return mcp_server._build_runner_cmd(
            task="do things", log_path="/tmp/x.jsonl", max_iter=10,
            workdir="/tmp", context_budget=1000, base_url=resolved)

    def test_no_env_no_arg_omits_base_url(self):
        cmd = self._cmd()
        self.assertNotIn("--base-url", cmd)

    def test_env_set_appends_base_url(self):
        os.environ["OPENBEAST_AGENT_INFERENCE_URL"] = "https://worker.ts.net:8443/v1"
        cmd = self._cmd()
        i = cmd.index("--base-url")
        self.assertEqual(cmd[i + 1], "https://worker.ts.net:8443/v1")

    def test_explicit_arg_beats_env(self):
        os.environ["OPENBEAST_AGENT_INFERENCE_URL"] = "https://worker.ts.net:8443/v1"
        cmd = self._cmd(base_url="http://other:9090/v1")
        i = cmd.index("--base-url")
        self.assertEqual(cmd[i + 1], "http://other:9090/v1")

    def test_default_url_is_treated_as_local(self):
        # Explicitly passing the runner's own default must not add the flag —
        # local spawns stay byte-identical to pre-feature argv.
        import mcp_server
        cmd = self._cmd(base_url=mcp_server._DEFAULT_AGENT_BASE_URL)
        self.assertNotIn("--base-url", cmd)

    def test_task_is_last_arg_after_context(self):
        # --context and the positional task keep their ordering contract.
        import mcp_server
        cmd = mcp_server._build_runner_cmd(
            task="the task", log_path="/tmp/x.jsonl", max_iter=10,
            workdir="/tmp", context_budget=1000, context="briefing",
            base_url="https://worker:8443/v1")
        self.assertEqual(cmd[-1], "the task")
        self.assertIn("--context", cmd)
        self.assertIn("--base-url", cmd)


if __name__ == "__main__":
    unittest.main()
