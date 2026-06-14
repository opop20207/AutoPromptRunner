"""Packaging / installation metadata tests.

Verify the CLI entry point, package version, pyproject metadata, helper scripts, and the
frontend package scripts -- so a local install stays reproducible. Standard library only;
no network and no external tools.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Scripts expected under scripts/ (executable bash helpers).
_SCRIPTS = (
    "setup_local.sh",
    "install_backend.sh",
    "install_frontend.sh",
    "build_frontend.sh",
    "dev_api.sh",
    "dev_worker.sh",
    "dev_frontend.sh",
    "check_all.sh",
    "doctor.sh",
    "package_release.sh",
)


class EntryPointTests(unittest.TestCase):
    def test_cli_entry_point_importable(self):
        from autoprompt_runner.cli import main

        self.assertTrue(callable(main))

    def test_package_version_available(self):
        import autoprompt_runner

        self.assertIsInstance(autoprompt_runner.__version__, str)
        self.assertTrue(autoprompt_runner.__version__.strip())


class PyprojectTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(_ROOT, "pyproject.toml"), "rb") as handle:
            self.pyproject = tomllib.load(handle)

    def test_package_name(self):
        self.assertEqual(self.pyproject["project"]["name"], "autoprompt-runner")

    def test_cli_entry_point_declared(self):
        scripts = self.pyproject["project"].get("scripts", {})
        self.assertEqual(scripts.get("autoprompt-runner"), "autoprompt_runner.cli:main")

    def test_no_runtime_dependencies_for_core(self):
        # The CLI core must stay standard-library-only; third-party deps live in extras.
        self.assertEqual(self.pyproject["project"].get("dependencies", []), [])

    def test_pytest_config_present(self):
        tool = self.pyproject.get("tool", {})
        self.assertIn("pytest", tool)


class ScriptsTests(unittest.TestCase):
    def _path(self, name):
        return os.path.join(_ROOT, "scripts", name)

    def test_scripts_exist_with_shebang(self):
        for name in _SCRIPTS:
            path = self._path(name)
            self.assertTrue(os.path.isfile(path), f"missing script: scripts/{name}")
            with open(path, "r", encoding="utf-8") as handle:
                first_line = handle.readline().strip()
            self.assertEqual(first_line, "#!/usr/bin/env bash", f"scripts/{name} missing bash shebang")

    def test_scripts_executable_where_supported(self):
        # On POSIX the executable bit should be set; on Windows it is not meaningful.
        if os.name != "posix":
            self.skipTest("executable bit not meaningful on this platform")
        for name in _SCRIPTS:
            self.assertTrue(os.access(self._path(name), os.X_OK), f"scripts/{name} is not executable")

    def test_check_all_uses_safe_commands_only(self):
        with open(self._path("check_all.sh"), "r", encoding="utf-8") as handle:
            content = handle.read()
        # The check suite must never invoke the external AI agents.
        self.assertNotIn("claude", content)
        self.assertNotIn("codex", content)
        # It should run the backend tests and validate config.
        self.assertTrue("pytest" in content or "unittest" in content)
        self.assertIn("config validate", content)

    def test_doctor_does_not_fail_on_optional_providers(self):
        with open(self._path("doctor.sh"), "r", encoding="utf-8") as handle:
            content = handle.read()
        # Optional provider commands are referenced but only as warnings (no set -e abort).
        self.assertIn("claude", content)
        self.assertIn("codex", content)
        self.assertNotIn("set -euo pipefail", content)  # doctor intentionally avoids -e


class FrontendPackageTests(unittest.TestCase):
    def test_required_scripts_present(self):
        with open(os.path.join(_ROOT, "frontend", "package.json"), "r", encoding="utf-8") as handle:
            pkg = json.load(handle)
        scripts = pkg.get("scripts", {})
        for required in ("dev", "build", "preview"):
            self.assertIn(required, scripts, f"frontend package.json missing '{required}' script")


if __name__ == "__main__":
    unittest.main()
