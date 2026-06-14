"""Release-readiness checks for the AutoPromptRunner v0.1.0 candidate.

Verifies the version, CLI entry point, release docs (README quickstart, CHANGELOG, release
notes), package metadata, helper scripts, the frontend package scripts, and that no test
requires a real Claude Code / Codex CLI. Standard library only; no network, no external
tools.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import tomllib
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

EXPECTED_VERSION = "0.1.0"

_REQUIRED_SCRIPTS = (
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


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), "r", encoding="utf-8") as handle:
        return handle.read()


class VersionTests(unittest.TestCase):
    def test_package_version_is_0_1_0(self):
        import autoprompt_runner

        self.assertEqual(autoprompt_runner.__version__, EXPECTED_VERSION)

    def test_cli_version_command_prints_version(self):
        import io
        from contextlib import redirect_stdout

        from autoprompt_runner.cli import main

        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["version"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), EXPECTED_VERSION)


class EntryPointTests(unittest.TestCase):
    def test_cli_entry_point_callable(self):
        from autoprompt_runner.cli import main

        self.assertTrue(callable(main))


class PackageMetadataTests(unittest.TestCase):
    def setUp(self):
        self.pyproject = tomllib.loads(_read("pyproject.toml"))

    def test_metadata_valid_for_local_install(self):
        project = self.pyproject["project"]
        self.assertEqual(project["name"], "autoprompt-runner")
        self.assertEqual(project["scripts"]["autoprompt-runner"], "autoprompt_runner.cli:main")
        self.assertIn("build-system", self.pyproject)
        # Version is provided dynamically from the package's __version__.
        self.assertIn("version", project.get("dynamic", []))


class ReleaseDocsTests(unittest.TestCase):
    def test_changelog_exists_with_release_section(self):
        changelog = _read("CHANGELOG.md")
        self.assertIn("# Changelog", changelog)
        self.assertIn("## 0.1.0", changelog)
        for heading in ("### Added", "### Changed", "### Fixed", "### Known limitations"):
            self.assertIn(heading, changelog)

    def test_release_notes_exist_with_sections(self):
        notes = _read("RELEASE_NOTES.md")
        self.assertIn("AutoPromptRunner v0.1.0 Release Notes", notes)
        for section in (
            "Summary", "What works", "Local setup", "Basic workflow",
            "Provider setup", "Safety model", "Known limitations", "Recommended next steps",
        ):
            self.assertIn(section, notes)

    def test_readme_has_quickstart_and_required_sections(self):
        readme = _read("README.md")
        self.assertIn("## Quickstart", readme)
        # Quickstart steps.
        for token in (
            "git clone", "setup_local.sh", "dev_api.sh", "dev_worker.sh", "dev_frontend.sh",
            "project add", "approve-next", "show-artifacts",
        ):
            self.assertIn(token, readme)
        # Required reference sections.
        for token in (
            "## Configuration", "## Run queue and background worker", "## HTTP API (FastAPI)",
            "## Web UI (frontend)", "## Troubleshooting", "## v0.1 capabilities",
            "## Not supported yet",
        ):
            self.assertIn(token, readme)


class ScriptsTests(unittest.TestCase):
    def _path(self, name):
        return os.path.join(_ROOT, "scripts", name)

    def test_required_scripts_exist_with_shebang(self):
        for name in _REQUIRED_SCRIPTS:
            path = self._path(name)
            self.assertTrue(os.path.isfile(path), f"missing script: scripts/{name}")
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.readline().strip(), "#!/usr/bin/env bash")

    def test_required_scripts_executable_where_practical(self):
        if os.name != "posix":
            self.skipTest("executable bit not meaningful on this platform")
        for name in _REQUIRED_SCRIPTS:
            self.assertTrue(os.access(self._path(name), os.X_OK), f"scripts/{name} not executable")

    def test_check_all_is_primary_validation_and_safe(self):
        content = _read("scripts", "check_all.sh")
        self.assertTrue("pytest" in content or "unittest" in content)
        self.assertIn("config validate", content)
        # Never invokes the external AI agents.
        self.assertNotIn("claude", content)
        self.assertNotIn("codex", content)


class FrontendPackageTests(unittest.TestCase):
    def test_frontend_scripts_present(self):
        pkg = json.loads(_read("frontend", "package.json"))
        for required in ("dev", "build", "preview"):
            self.assertIn(required, pkg.get("scripts", {}))


class NoRealAgentRequiredTests(unittest.TestCase):
    # A subprocess invocation whose first argv element is the claude/codex executable.
    _INVOKES_AGENT = re.compile(r"subprocess\.(?:run|Popen|call)\(\s*\[\s*[\"']c(?:laude|odex)\b")

    def test_no_test_spawns_real_claude_or_codex(self):
        offenders = []
        for path in glob.glob(os.path.join(_ROOT, "tests", "*.py")):
            with open(path, "r", encoding="utf-8") as handle:
                if self._INVOKES_AGENT.search(handle.read()):
                    offenders.append(os.path.basename(path))
        self.assertEqual(offenders, [], f"tests must not spawn real claude/codex: {offenders}")


if __name__ == "__main__":
    unittest.main()
