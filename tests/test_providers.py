"""Tests for provider profiles (autoprompt_runner.providers + storage)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import providers, storage  # noqa: E402
from autoprompt_runner.runners import ClaudeCodeRunner, CodexRunner, MockRunner  # noqa: E402


class ProviderStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_seed_default_providers(self):
        result = providers.seed_default_provider_profiles(self.db)
        self.assertEqual(result["seeded"], 3)
        names = {p.name for p in storage.list_provider_profiles(self.db)}
        self.assertEqual(names, {"mock", "claude-code", "codex"})

    def test_seed_is_idempotent_and_does_not_overwrite(self):
        providers.seed_default_provider_profiles(self.db)
        # User modifies a profile; a plain re-seed must not overwrite it.
        mock = storage.get_provider_profile_by_name(self.db, "mock")
        storage.update_provider_profile(self.db, mock.id, default_timeout_seconds=99)
        result = providers.seed_default_provider_profiles(self.db)
        self.assertEqual(result["seeded"], 0)
        self.assertEqual(storage.get_provider_profile_by_name(self.db, "mock").default_timeout_seconds, 99)
        # Forced re-seed resets it to the default.
        providers.seed_default_provider_profiles(self.db, force=True)
        self.assertEqual(storage.get_provider_profile_by_name(self.db, "mock").default_timeout_seconds, 30)

    def test_create_list_get_update_delete(self):
        pid = storage.create_provider_profile(
            self.db, name="claude-fast", type="claude-code", command="claude",
            default_timeout_seconds=1200, default_args="--model x", enabled=True,
        )
        self.assertIsNotNone(storage.get_provider_profile_by_id(self.db, pid))
        by_name = storage.get_provider_profile_by_name(self.db, "claude-fast")
        self.assertEqual(by_name.type, "claude-code")
        self.assertEqual(by_name.default_args, "--model x")
        storage.update_provider_profile(self.db, pid, command="claude2", default_timeout_seconds=600)
        updated = storage.get_provider_profile_by_name(self.db, "claude-fast")
        self.assertEqual(updated.command, "claude2")
        self.assertEqual(updated.default_timeout_seconds, 600)
        storage.delete_provider_profile(self.db, pid)
        self.assertIsNone(storage.get_provider_profile_by_name(self.db, "claude-fast"))

    def test_update_default_args_to_null(self):
        pid = storage.create_provider_profile(
            self.db, name="p", type="mock", command="mock", default_timeout_seconds=30, default_args="x",
        )
        storage.update_provider_profile(self.db, pid, default_args=None)
        self.assertIsNone(storage.get_provider_profile_by_name(self.db, "p").default_args)

    def test_enable_disable(self):
        pid = storage.create_provider_profile(
            self.db, name="p", type="mock", command="mock", default_timeout_seconds=30, enabled=True,
        )
        storage.set_provider_enabled(self.db, pid, False)
        self.assertFalse(storage.get_provider_profile_by_name(self.db, "p").enabled)
        storage.set_provider_enabled(self.db, pid, True)
        self.assertTrue(storage.get_provider_profile_by_name(self.db, "p").enabled)


class ProviderValidationTests(unittest.TestCase):
    def test_validate_provider_type(self):
        self.assertEqual(providers.validate_provider_type("claude-code"), "claude-code")
        with self.assertRaises(providers.ProviderError):
            providers.validate_provider_type("nope")

    def test_validate_provider_command(self):
        self.assertEqual(providers.validate_provider_command(" claude "), "claude")
        with self.assertRaises(providers.ProviderError):
            providers.validate_provider_command("   ")
        with self.assertRaises(providers.ProviderError):
            providers.validate_provider_command("claude --flag")  # must be a single executable

    def test_validate_provider_timeout(self):
        self.assertEqual(providers.validate_provider_timeout(1200), 1200)
        with self.assertRaises(providers.ProviderError):
            providers.validate_provider_timeout(0)
        with self.assertRaises(providers.ProviderError):
            providers.validate_provider_timeout("nope")


class ProviderAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _profile(self, type_, command):
        pid = storage.create_provider_profile(
            self.db, name=f"{type_}-{command}", type=type_, command=command, default_timeout_seconds=30,
        )
        return storage.get_provider_profile_by_id(self.db, pid)

    def test_mock_always_available(self):
        self.assertTrue(providers.check_provider_available(self._profile("mock", "mock")))

    def test_unavailable_external_provider(self):
        # A command that certainly is not on PATH must report unavailable (no execution).
        profile = self._profile("claude-code", "definitely-not-a-real-cli-xyz")
        self.assertFalse(providers.check_provider_available(profile))

    def test_available_external_provider_when_command_exists(self):
        # Use the running Python interpreter's basename, which is guaranteed to be on PATH.
        exe = os.path.basename(sys.executable)
        profile = self._profile("codex", exe)
        self.assertTrue(providers.check_provider_available(profile))

    def test_ensure_runnable_rejects_disabled_and_unavailable(self):
        disabled = self._profile("mock", "mock")
        storage.set_provider_enabled(self.db, disabled.id, False)
        with self.assertRaises(providers.ProviderError) as ctx:
            providers.ensure_provider_runnable(storage.get_provider_profile_by_id(self.db, disabled.id))
        self.assertEqual(ctx.exception.kind, "disabled")
        unavailable = self._profile("codex", "definitely-not-a-real-cli-xyz")
        with self.assertRaises(providers.ProviderError) as ctx:
            providers.ensure_provider_runnable(unavailable)
        self.assertEqual(ctx.exception.kind, "unavailable")


class ProviderRunnerBuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _profile(self, type_, command, default_args=None, timeout=1200):
        pid = storage.create_provider_profile(
            self.db, name=f"{type_}-x", type=type_, command=command,
            default_timeout_seconds=timeout, default_args=default_args,
        )
        return storage.get_provider_profile_by_id(self.db, pid)

    def test_build_mock_runner(self):
        self.assertIsInstance(providers.build_runner_for_profile(self._profile("mock", "mock"), None, None), MockRunner)

    def test_build_claude_runner_uses_command_and_args(self):
        runner = providers.build_runner_for_profile(self._profile("claude-code", "claude", "--model x"), None, None)
        self.assertIsInstance(runner, ClaudeCodeRunner)
        self.assertEqual(runner.command, "claude")
        self.assertEqual(runner.timeout_seconds, 1200)  # falls back to profile default
        self.assertEqual(runner._build_argv("hi"), ["claude", "--model", "x", "-p", "hi"])

    def test_build_codex_runner_explicit_timeout_wins(self):
        runner = providers.build_runner_for_profile(self._profile("codex", "codex"), None, 300)
        self.assertIsInstance(runner, CodexRunner)
        self.assertEqual(runner.timeout_seconds, 300)


if __name__ == "__main__":
    unittest.main()
