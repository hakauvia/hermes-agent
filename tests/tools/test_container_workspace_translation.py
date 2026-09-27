"""Regression: container-mode file tools must translate the Windows host workspace
root to POSIX ``/workspace`` instead of POSIX-joining a drive-letter path onto the
process cwd (the doubled-path defect behind FORGE activation failure).

The tests exercise the resolution seam directly with ``container_paths=True`` —
no Docker daemon required — and prove normalize(normalize(path)) == normalize(path).
"""

import os

import pytest

from tools import file_tools_paths


@pytest.mark.windows_only
class TestContainerWorkspaceTranslation:
    def test_host_workspace_root_maps_to_container_workspace(self, monkeypatch):
        """A Windows host workspace root must become /workspace, not a POSIX-joined string."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setattr(
            file_tools_paths, "_authoritative_workspace_root",
            lambda task_id="default": r"C:\ForgeTask\work-001",
        )
        base = file_tools_paths._resolve_base_dir(container_paths=True)
        assert str(base) == "/workspace"

    def test_relative_path_resolves_inside_container_workspace(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setattr(
            file_tools_paths, "_authoritative_workspace_root",
            lambda task_id="default": r"C:\ForgeTask\work-001",
        )
        result = file_tools_paths._resolve_path_for_task("app.py")
        assert str(result) == "/workspace/app.py"

    def test_host_absolute_path_inside_root_translates(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setattr(
            file_tools_paths, "_authoritative_workspace_root",
            lambda task_id="default": r"C:\ForgeTask\work-001",
        )
        result = file_tools_paths._resolve_path_for_task(r"C:\ForgeTask\work-001\sub\file.py")
        assert str(result) == "/workspace/sub/file.py"

    def test_host_absolute_path_outside_root_rejected(self, monkeypatch):
        """A drive-letter path outside the mounted workspace must not silently resolve."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setattr(
            file_tools_paths, "_authoritative_workspace_root",
            lambda task_id="default": r"C:\ForgeTask\work-001",
        )
        with pytest.raises(ValueError, match="outside"):
            file_tools_paths._resolve_path_for_task(r"C:\Users\other\secret.txt")

    def test_already_container_path_passes_through(self, monkeypatch):
        """Idempotency: /workspace/... normalized twice is unchanged."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setattr(
            file_tools_paths, "_authoritative_workspace_root",
            lambda task_id="default": r"C:\ForgeTask\work-001",
        )
        once = file_tools_paths._resolve_path_for_task("/workspace/app.py")
        twice = file_tools_paths._resolve_path_for_task(str(once))
        assert str(once) == "/workspace/app.py"
        assert str(twice) == str(once)


@pytest.mark.windows_only
class TestContainerWorkspaceTranslationExtended:
    """Extended coverage for the FORGE workspace root and edge cases."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setattr(
            file_tools_paths, "_authoritative_workspace_root",
            lambda task_id="default": r"C:\FORGE\workspaces\job",
        )

    def test_exact_host_task_root_maps_to_workspace(self):
        """Case A — exact host task root → /workspace"""
        result = file_tools_paths._resolve_path_for_task(r"C:\FORGE\workspaces\job")
        assert str(result) == "/workspace"

    def test_host_task_root_with_trailing_separator(self):
        """Exact host task root with trailing separator → /workspace"""
        result = file_tools_paths._resolve_path_for_task(r"C:\FORGE\workspaces\job\\")
        assert str(result) == "/workspace"

    def test_in_root_host_file_backslash(self):
        """Case B — in-root host file with backslash → /workspace/app.py"""
        result = file_tools_paths._resolve_path_for_task(r"C:\FORGE\workspaces\job\app.py")
        assert str(result) == "/workspace/app.py"

    def test_in_root_host_file_forward_slash(self):
        """Case H — in-root host file with forward slash → /workspace/app.py"""
        result = file_tools_paths._resolve_path_for_task("C:/FORGE/workspaces/job/app.py")
        assert str(result) == "/workspace/app.py"

    def test_container_path_idempotent(self):
        """Case C — /workspace/app.py passes through unchanged"""
        result = file_tools_paths._resolve_path_for_task("/workspace/app.py")
        assert str(result) == "/workspace/app.py"

    def test_relative_file(self):
        """Case D — relative file → /workspace/app.py"""
        result = file_tools_paths._resolve_path_for_task("app.py")
        assert str(result) == "/workspace/app.py"

    def test_parent_escape_rejected(self):
        """Case E — parent escape (different workspace) → REJECT"""
        with pytest.raises(ValueError, match="outside"):
            file_tools_paths._resolve_path_for_task(r"C:\FORGE\workspaces\other\x.txt")

    def test_sibling_prefix_confusion_rejected(self):
        """Case F — sibling-prefix confusion (job-evil) → REJECT"""
        with pytest.raises(ValueError, match="outside"):
            file_tools_paths._resolve_path_for_task(r"C:\FORGE\workspaces\job-evil\x.txt")

    def test_alternate_drive_letter_rejected(self):
        """Another drive letter → REJECT"""
        with pytest.raises(ValueError, match="outside"):
            file_tools_paths._resolve_path_for_task(r"D:\other\file.txt")

    def test_relative_traversal_rejected_or_constrained(self):
        """Case G — traversal ..\\outside.txt → REJECT or securely constrained"""
        # Relative paths resolve inside workspace; traversal stays within or is rejected
        # The current behavior: relative paths are anchored to /workspace
        result = file_tools_paths._resolve_path_for_task(r"..\outside.txt")
        # Should resolve to /workspace/outside.txt (relative to /workspace)
        # NOT to a path outside /workspace
        assert str(result).startswith("/workspace/")

    def test_traversal_within_host_path_normalized_then_checked(self):
        """Traversal within host path (.. inside root) → normalized then checked"""
        # C:\FORGE\workspaces\job\..\outside.txt normalizes to C:\FORGE\workspaces\outside.txt
        # which is OUTSIDE the task root → REJECT
        # Current implementation does string-prefix check, not full normalization.
        # The string "C:/FORGE/workspaces/job/../outside.txt" does not start with
        # "C:/FORGE/workspaces/job/" so it is correctly rejected.
        with pytest.raises(ValueError, match="outside"):
            file_tools_paths._resolve_path_for_task(r"C:\FORGE\workspaces\job\..\outside.txt")

    def test_case_insensitive_windows_root(self):
        """Equivalent in-root Windows paths using different casing → same canonical result"""
        result = file_tools_paths._resolve_path_for_task(r"c:\forge\workspaces\job\app.py")
        assert str(result) == "/workspace/app.py"

    def test_mixed_separators_same_result(self):
        """Mixed separators in host path → same canonical /workspace/... result"""
        result1 = file_tools_paths._resolve_path_for_task(r"C:\FORGE\workspaces\job\sub\file.py")
        result2 = file_tools_paths._resolve_path_for_task("C:/FORGE/workspaces/job/sub/file.py")
        result3 = file_tools_paths._resolve_path_for_task(r"C:\FORGE/workspaces\job/sub/file.py")
        assert str(result1) == "/workspace/sub/file.py"
        assert str(result2) == "/workspace/sub/file.py"
        assert str(result3) == "/workspace/sub/file.py"

    def test_nested_subdirectory(self):
        """Deep nested subdirectory under task root"""
        result = file_tools_paths._resolve_path_for_task(r"C:\FORGE\workspaces\job\a\b\c\d.py")
        assert str(result) == "/workspace/a/b/c/d.py"

    def test_empty_relative_path(self):
        """Empty string relative path → /workspace"""
        result = file_tools_paths._resolve_path_for_task("")
        assert str(result) == "/workspace"


class TestContainerWorkspaceTranslationPOSIX:
    """Ensure Linux/POSIX paths continue behaving as before (no regression)."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        # Simulate non-container backend
        monkeypatch.setenv("TERMINAL_ENV", "local")
        monkeypatch.setattr(
            file_tools_paths, "_authoritative_workspace_root",
            lambda task_id="default": "/home/user/project",
        )

    def test_posix_absolute_path_passes_through(self):
        """POSIX absolute path resolves to absolute path in local mode"""
        result = file_tools_paths._resolve_path_for_task("/home/user/project/app.py")
        # On Windows, POSIX paths become Windows paths; on POSIX, they stay POSIX
        # The key is it resolves to an absolute path under the workspace
        assert str(result).endswith("home\\user\\project\\app.py") or str(result).endswith("/home/user/project/app.py")

    def test_posix_relative_path_anchored_to_root(self):
        """POSIX relative path anchored to workspace root"""
        result = file_tools_paths._resolve_path_for_task("app.py")
        assert str(result).endswith("home\\user\\project\\app.py") or str(result).endswith("/home/user/project/app.py")

    def test_posix_container_mode_passthrough(self, monkeypatch):
        """In container mode with POSIX root, paths should work"""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setattr(
            file_tools_paths, "_authoritative_workspace_root",
            lambda task_id="default": "/workspace",
        )
        # Already in container namespace
        result = file_tools_paths._resolve_path_for_task("/workspace/app.py")
        assert str(result) == "/workspace/app.py"
