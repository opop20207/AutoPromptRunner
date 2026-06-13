"""Tests for the settings loader, validation, and config-driven storage/worker.

Standard library only (unittest + tempfile + unittest.mock). Each test runs in a clean
temp working directory with the ``AUTOPROMPT_*`` environment cleared, so the built-in
search order is deterministic. Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import settings, storage  # noqa: E402
from autoprompt_runner.worker import LocalWorker  # noqa: E402


def _clean_env() -> dict:
    """Current environment with all AUTOPROMPT_* variables removed."""
    return {k: v for k, v in os.environ.items() if not k.startswith("AUTOPROMPT_")}


class _CleanCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)  # no autoprompt.toml in this cwd
        self._env = mock.patch.dict(os.environ, _clean_env(), clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _write_config(self, text: str) -> str:
        path = os.path.join(self._tmp.name, "cfg.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path


class SettingsLoadTests(_CleanCase):
    def test_builtin_defaults_load(self):
        s = settings.load_settings()  # no file, no env
        self.assertEqual(s.storage.db_path, os.path.join(".autoprompt", "autoprompt.db"))
        self.assertEqual(s.defaults.provider, "mock")
        self.assertEqual(s.defaults.max_loops, 5)
        self.assertTrue(s.defaults.require_approval)
        self.assertEqual(s.safety.max_loops_hard_limit, 20)
        self.assertEqual(s.safety.timeout_seconds_hard_limit, 7200)
        self.assertEqual(s.queue.poll_interval_seconds, 2.0)
        self.assertEqual(s.api.port, 8000)

    def test_toml_overrides_defaults(self):
        path = self._write_config('[defaults]\nprovider = "codex"\nmax_loops = 7\n[api]\nport = 9001\n')
        s = settings.load_settings(path)
        self.assertEqual(s.defaults.provider, "codex")
        self.assertEqual(s.defaults.max_loops, 7)
        self.assertEqual(s.api.port, 9001)
        self.assertEqual(s.defaults.timeout_seconds, 1800)  # untouched -> default

    def test_env_overrides_config_file(self):
        path = self._write_config("[defaults]\nmax_loops = 7\n")
        os.environ["AUTOPROMPT_MAX_LOOPS_DEFAULT"] = "11"
        os.environ["AUTOPROMPT_DEFAULT_PROVIDER"] = "claude-code"
        s = settings.load_settings(path)
        self.assertEqual(s.defaults.max_loops, 11)  # env beats the config file
        self.assertEqual(s.defaults.provider, "claude-code")

    def test_missing_explicit_config_raises(self):
        with self.assertRaises(settings.SettingsError):
            settings.load_settings(os.path.join(self._tmp.name, "nope.toml"))

    def test_bad_env_int_raises(self):
        os.environ["AUTOPROMPT_MAX_LOOPS_DEFAULT"] = "not-an-int"
        with self.assertRaises(settings.SettingsError):
            settings.load_settings()

    def test_settings_to_dict_sections(self):
        data = settings.settings_to_dict(settings.build_default_settings())
        for section in ("storage", "defaults", "safety", "queue", "api", "worktrees"):
            self.assertIn(section, data)
        self.assertEqual(data["defaults"]["max_loops"], 5)


class ValidationTests(_CleanCase):
    def test_valid_defaults_pass(self):
        settings.validate_settings(settings.build_default_settings())  # no raise

    def test_invalid_max_loops_limits_rejected(self):
        s = settings.build_default_settings()
        s.defaults.max_loops = s.safety.max_loops_hard_limit + 1
        with self.assertRaises(settings.SettingsError):
            settings.validate_settings(s)

    def test_zero_max_loops_hard_limit_rejected(self):
        s = settings.build_default_settings()
        s.safety.max_loops_hard_limit = 0
        with self.assertRaises(settings.SettingsError):
            settings.validate_settings(s)

    def test_invalid_timeout_limits_rejected(self):
        s = settings.build_default_settings()
        s.defaults.timeout_seconds = s.safety.timeout_seconds_hard_limit + 1
        with self.assertRaises(settings.SettingsError):
            settings.validate_settings(s)

    def test_empty_db_path_rejected(self):
        s = settings.build_default_settings()
        s.storage.db_path = ""
        with self.assertRaises(settings.SettingsError):
            settings.validate_settings(s)


class ConfigDrivenTests(_CleanCase):
    def test_storage_uses_configured_db_path(self):
        custom = os.path.join(self._tmp.name, "custom.db")
        os.environ["AUTOPROMPT_DB_PATH"] = custom
        path = storage.init_db(None)  # None -> configured default
        self.assertEqual(os.path.abspath(path), os.path.abspath(custom))
        self.assertTrue(os.path.exists(custom))

    def test_storage_default_without_config(self):
        path = storage.init_db(None)
        self.assertEqual(os.path.abspath(path), os.path.abspath(os.path.join(".autoprompt", "autoprompt.db")))

    def test_worker_uses_configured_poll_interval(self):
        db = os.path.join(self._tmp.name, "autoprompt.db")
        os.environ["AUTOPROMPT_QUEUE_POLL_INTERVAL_SECONDS"] = "5"
        worker = LocalWorker(db, poll_interval_seconds=None)  # None -> configured default
        self.assertEqual(worker.poll_interval_seconds, 5.0)


if __name__ == "__main__":
    unittest.main()
