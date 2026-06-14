"""Tests for cross-platform path helpers (autoprompt_runner.paths). Standard library only."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import paths  # noqa: E402

_IS_WINDOWS = os.name == "nt"


class NormalizeTests(unittest.TestCase):
    def test_normalize_posix_style_relative(self):
        self.assertEqual(paths.normalize_path(os.path.join("a", "b", "..", "c")), os.path.join("a", "c"))

    def test_normalize_collapses_redundant_separators(self):
        self.assertEqual(paths.normalize_path("a//b/./c"), os.path.join("a", "b", "c"))

    def test_normalize_empty_and_none(self):
        self.assertEqual(paths.normalize_path(""), "")
        self.assertEqual(paths.normalize_path("   "), "")
        self.assertEqual(paths.normalize_path(None), "")

    def test_path_with_spaces_preserved(self):
        result = paths.normalize_path(os.path.join("dir with spaces", "sub dir"))
        self.assertIn("dir with spaces", result)
        self.assertIn("sub dir", result)

    def test_non_ascii_korean_path_preserved(self):
        result = paths.normalize_path(os.path.join("프로젝트", "디렉터리"))
        self.assertIn("프로젝트", result)
        self.assertIn("디렉터리", result)

    def test_case_is_preserved(self):
        self.assertIn("MyDir", paths.normalize_path(os.path.join("MyDir", "Sub")))


class WindowsDriveTests(unittest.TestCase):
    def test_drive_forms_map_to_one_canonical_form(self):
        a = paths.normalize_windows_drive_path("c:/Dev/Project")
        b = paths.normalize_windows_drive_path("C:\\Dev\\Project\\")
        c = paths.normalize_windows_drive_path("C:/Dev/Project/")
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertTrue(a.startswith("C:"))
        self.assertIn("Dev", a)
        self.assertIn("Project", a)

    def test_drive_letter_is_uppercased(self):
        self.assertTrue(paths.normalize_windows_drive_path("d:/x/y").startswith("D:"))


class ResolveTests(unittest.TestCase):
    def test_resolve_makes_absolute(self):
        self.assertTrue(os.path.isabs(paths.resolve_path("rel")))

    def test_resolve_with_base_dir(self):
        base = os.path.abspath("base")
        self.assertEqual(paths.resolve_path("sub", base), os.path.normpath(os.path.join(base, "sub")))

    def test_resolve_absolute_input_stays_absolute(self):
        abs_in = os.path.abspath(os.path.join("x", "y"))
        self.assertEqual(paths.resolve_path(abs_in), os.path.normpath(abs_in))

    def test_resolve_empty(self):
        self.assertEqual(paths.resolve_path(""), "")


class SubpathTests(unittest.TestCase):
    def test_inside_true(self):
        self.assertTrue(paths.is_subpath(os.path.join("a", "b", "c"), os.path.join("a", "b")))

    def test_equal_is_not_subpath(self):
        self.assertFalse(paths.is_subpath(os.path.join("a", "b"), os.path.join("a", "b")))

    def test_sibling_prefix_not_subpath(self):
        self.assertFalse(paths.is_subpath(os.path.join("a", "bc"), os.path.join("a", "b")))

    def test_outside_false(self):
        self.assertFalse(paths.is_subpath(os.path.join("x", "y"), os.path.join("a", "b")))

    def test_empty_inputs_false(self):
        self.assertFalse(paths.is_subpath("", "a"))
        self.assertFalse(paths.is_subpath("a", ""))


class WorkspaceLockKeyTests(unittest.TestCase):
    def test_trailing_separator_same_key(self):
        base = os.path.abspath("ws")
        self.assertEqual(paths.normalize_workspace_path(base), paths.normalize_workspace_path(base + os.sep))

    def test_relative_and_absolute_same_key(self):
        self.assertEqual(paths.normalize_workspace_path("ws"), paths.normalize_workspace_path(os.path.abspath("ws")))

    def test_empty_is_empty(self):
        self.assertEqual(paths.normalize_workspace_path(""), "")

    @unittest.skipUnless(_IS_WINDOWS, "Windows case-insensitive path comparison")
    def test_windows_case_insensitive(self):
        self.assertEqual(
            paths.normalize_workspace_path("C:\\Dev\\Project"),
            paths.normalize_workspace_path("c:\\dev\\project"),
        )

    @unittest.skipUnless(_IS_WINDOWS, "Windows slash/case forms")
    def test_windows_slash_and_case_forms_same(self):
        self.assertEqual(
            paths.normalize_workspace_path("C:\\Dev\\Project"),
            paths.normalize_workspace_path("C:/Dev/Project/"),
        )

    def test_posix_case_sensitive(self):
        if _IS_WINDOWS:
            self.skipTest("POSIX-only case sensitivity")
        self.assertNotEqual(
            paths.normalize_workspace_path("/dev/Project"),
            paths.normalize_workspace_path("/dev/project"),
        )


class DisplayTests(unittest.TestCase):
    def test_safe_display_preserves_case(self):
        result = paths.safe_display_path(os.path.join("Dir", "Sub"))
        self.assertIn("Dir", result)
        self.assertIn("Sub", result)

    def test_safe_display_empty(self):
        self.assertEqual(paths.safe_display_path(None), "")
        self.assertEqual(paths.safe_display_path(""), "")

    def test_posix_display_uses_forward_slashes(self):
        self.assertEqual(paths.path_to_posix_display(os.path.join("a", "b", "c")), "a/b/c")

    def test_posix_display_non_ascii(self):
        self.assertEqual(paths.path_to_posix_display(os.path.join("프로젝트", "x")), "프로젝트/x")


class EnsureParentTests(unittest.TestCase):
    def test_creates_only_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "nested", "deep", "file.db")
            returned = paths.ensure_parent_dir(target)
            self.assertTrue(os.path.isdir(os.path.dirname(returned)))
            self.assertFalse(os.path.exists(target))  # only the parent dir is created
            self.assertTrue(os.path.isabs(returned))

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "a", "b.db")
            paths.ensure_parent_dir(target)
            paths.ensure_parent_dir(target)  # no error on second call
            self.assertTrue(os.path.isdir(os.path.join(tmp, "a")))


if __name__ == "__main__":
    unittest.main()
