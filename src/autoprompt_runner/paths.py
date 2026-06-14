"""Cross-platform path helpers (Windows, macOS, Linux).

Centralizes path handling so the rest of the app deals with Windows drive letters, paths
containing spaces, and non-ASCII (e.g. Korean) paths consistently. It builds paths with
``pathlib`` / ``os.path`` (never by concatenating ``"/"`` by hand), preserves Windows drive
letters, and only lower-cases for the case-insensitive comparison Windows requires -- never
for display. Standard library only; it reads no secrets and prints nothing.

The two normalization levels are intentionally distinct:

* :func:`normalize_path` cleans a path textually (collapses ``.``/``..`` and redundant
  separators, uses the native separator) without making it absolute and without touching the
  filesystem.
* :func:`resolve_path` additionally makes the path absolute (relative to ``base_dir`` or the
  current working directory). Neither resolves symlinks.

:func:`normalize_workspace_path` is the canonical key used for workspace **locks**: absolute,
case-folded on Windows, so different textual spellings of one directory map to one key.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath, PureWindowsPath
from typing import Optional

_WINDOWS = os.name == "nt"


def _fspath(path) -> str:
    """Return ``path`` as a plain string (accepts str or os.PathLike), or '' for empty/None."""
    if path is None:
        return ""
    text = os.fspath(path)
    return text if str(text).strip() else ""


def normalize_path(path) -> str:
    """Return ``path`` textually normalized (native separators, collapsed ``.``/``..``).

    Does not make the path absolute and does not touch the filesystem. Preserves case and any
    Windows drive letter. Returns '' for an empty/None input.
    """
    raw = _fspath(path)
    if not raw:
        return ""
    return os.path.normpath(raw)


def resolve_path(path, base_dir: Optional[str] = None) -> str:
    """Return an absolute, normalized path (no symlink resolution).

    A relative ``path`` is resolved against ``base_dir`` when given, otherwise the current
    working directory. Returns '' for an empty/None input.
    """
    raw = _fspath(path)
    if not raw:
        return ""
    if base_dir and not os.path.isabs(raw):
        raw = os.path.join(_fspath(base_dir), raw)
    return os.path.normpath(os.path.abspath(raw))


def _compare_key(path: str) -> str:
    """Absolute, case-folded (on Windows) form used only for path comparison."""
    return os.path.normcase(resolve_path(path))


def is_subpath(path, parent) -> bool:
    """Return True if ``path`` is **strictly inside** ``parent`` (not equal).

    Both are resolved to absolute paths first; the comparison is case-insensitive on Windows
    and case-sensitive elsewhere. Different drives (Windows) compare as not-contained.
    """
    if not _fspath(path) or not _fspath(parent):
        return False
    child = _compare_key(path)
    root = _compare_key(parent)
    if child == root:
        return False
    try:
        return os.path.commonpath([child, root]) == root
    except ValueError:  # different drives on Windows, or mixed absolute/relative
        return False


def same_path(a, b) -> bool:
    """Return True if ``a`` and ``b`` refer to the same location (Windows-aware comparison)."""
    if not _fspath(a) or not _fspath(b):
        return False
    return _compare_key(a) == _compare_key(b)


def safe_display_path(path) -> str:
    """Return a readable, normalized form of ``path`` for messages.

    Preserves case and drive letter (never lower-cases) and keeps the path relative if it was
    given as relative. Safe for ``None``/empty input (returns '').
    """
    raw = _fspath(path)
    if not raw:
        return ""
    return os.path.normpath(raw)


def normalize_workspace_path(path) -> str:
    """Return the canonical lock/comparison key for a workspace directory.

    Absolute and case-folded **on Windows** (so ``C:\\Dev\\Project``, ``c:/Dev/Project`` and
    ``C:/Dev/Project/`` map to one key); case-preserving on POSIX (where case matters). No
    symlink resolution and no filesystem access. Returns '' for an empty/None input.
    """
    raw = _fspath(path)
    if not raw:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(raw)))


def normalize_windows_drive_path(path) -> str:
    """Normalize a Windows drive-letter path to a canonical ``C:\\Dir\\Sub`` form.

    Uppercases the drive letter and uses backslashes, regardless of the host OS (handy for
    comparing/displaying Windows paths in tests on any platform). A path without a drive is
    returned :func:`normalize_path`-cleaned.
    """
    raw = _fspath(path)
    if not raw:
        return ""
    windows = PureWindowsPath(raw)
    if windows.drive:
        rest = windows.parts[1:]
        return str(PureWindowsPath(windows.drive.upper() + "\\", *rest))
    return os.path.normpath(raw)


def ensure_parent_dir(path) -> str:
    """Create the parent directory of ``path`` if missing; return the absolute path.

    Uses ``pathlib`` with ``parents=True, exist_ok=True`` so it is idempotent and works on
    Windows (drive letters, nested dirs). Never deletes anything.
    """
    abs_path = os.path.abspath(_fspath(path))
    parent = os.path.dirname(abs_path)
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    return abs_path


def path_to_posix_display(path) -> str:
    """Return a forward-slash form of ``path`` for cross-platform display.

    Normalizes first, then converts the native separator to ``/`` (e.g. ``C:\\Dev\\x`` ->
    ``C:/Dev/x``). Useful for showing changed-file paths consistently in the CLI / UI. This is
    a **display** helper only -- never use it to build a path passed to git or the filesystem.
    Returns '' for an empty/None input.
    """
    raw = _fspath(path)
    if not raw:
        return ""
    return os.path.normpath(raw).replace(os.sep, "/")
