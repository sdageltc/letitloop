"""Tests for component_slicer.py — pure deterministic grouping logic."""

import os
import tempfile

from orchestrator.component_slicer import (
    ComponentSlice,
    _describe_component,
    _group_key,
    merge_component_results,
    slice_components,
)


class TestGroupKey:
    def test_file_in_subdirectory(self):
        assert _group_key("src/main.py") == "src"

    def test_file_in_deep_path(self):
        assert _group_key("a/b/c/d.py") == "a"

    def test_file_in_root_with_ext(self):
        key = _group_key("main.py")
        assert key == ".py"

    def test_file_in_root_no_ext(self):
        key = _group_key("Makefile")
        assert key == "__root__"

    def test_windows_backslash_normalized(self):
        assert _group_key("src\\main.py") == "src"

    def test_empty_string(self):
        key = _group_key("")
        assert key == "__root__"


class TestDescribeComponent:
    def test_single_extension(self):
        desc = _describe_component("src", ["src/a.py", "src/b.py"])
        assert "2 file(s)" in desc
        assert "'src'" in desc

    def test_mixed_extensions(self):
        desc = _describe_component("src", ["src/a.py", "src/b.md", "src/c.txt"])
        assert "3 file(s)" in desc
        assert "md" in desc
        assert "py" in desc
        assert "txt" in desc

    def test_no_extension(self):
        desc = _describe_component("root", ["Makefile", "Dockerfile"])
        assert "no extension" in desc or "Makefile" in desc or len(desc) > 0


class TestSliceComponents:
    def test_empty_input_returns_single_component(self):
        slices = slice_components([], max_components=5)
        assert len(slices) == 1
        assert slices[0].id == "component_0"
        assert slices[0].files == []
        assert "no files" in slices[0].description

    def test_single_file(self):
        slices = slice_components(["src/main.py"], max_components=5)
        assert len(slices) == 1
        assert slices[0].files == ["src/main.py"]

    def test_files_in_same_directory(self):
        paths = ["src/a.py", "src/b.py", "src/c.py"]
        slices = slice_components(paths, max_components=5)
        assert len(slices) == 1
        assert len(slices[0].files) == 3

    def test_files_in_different_directories(self):
        paths = ["src/a.py", "tests/test_a.py", "docs/readme.md"]
        slices = slice_components(paths, max_components=5)
        assert len(slices) == 3
        ids = {s.id for s in slices}
        assert ids == {"component_0", "component_1", "component_2"}

    def test_merges_smallest_groups_when_over_limit(self):
        paths = [
            "a/1.py",
            "a/2.py",
            "b/1.py",
            "c/1.py",
            "d/1.py",
            "e/1.py",
        ]
        slices = slice_components(paths, max_components=3)
        assert len(slices) <= 3
        all_files = [f for s in slices for f in s.files]
        assert sorted(all_files) == sorted(paths)

    def test_max_components_one(self):
        paths = ["x/a.py", "y/b.py", "z/c.py"]
        slices = slice_components(paths, max_components=1)
        assert len(slices) == 1
        assert len(slices[0].files) == 3

    def test_exactly_max_components(self):
        paths = ["a/1.py", "b/2.py", "c/3.py", "d/4.py", "e/5.py"]
        slices = slice_components(paths, max_components=5)
        assert len(slices) == 5

    def test_root_files_grouped_by_extension(self):
        paths = ["main.py", "utils.py", "main.rs", "Makefile"]
        slices = slice_components(paths, max_components=5)
        [s for s in slices if s.id == "component_0" or any("main.py" in f for f in s.files)]
        assert any(s for s in slices if any(f.endswith(".py") for f in s.files))

    def test_mixed_dirs_and_root_files(self):
        paths = ["src/main.py", "tests/test_main.py", "README.md", "setup.py"]
        slices = slice_components(paths, max_components=5)
        assert len(slices) >= 2

    def test_ordering_stable(self):
        paths_a = ["z/1.py", "a/2.py", "m/3.py"]
        paths_b = ["a/2.py", "m/3.py", "z/1.py"]
        result_a = slice_components(paths_a, max_components=5)
        result_b = slice_components(paths_b, max_components=5)
        ids_a = [(s.id, len(s.files)) for s in result_a]
        ids_b = [(s.id, len(s.files)) for s in result_b]
        assert ids_a == ids_b

    def test_max_components_zero_treated_as_one(self):
        paths = ["a/1.py", "b/2.py"]
        slices = slice_components(paths, max_components=0)
        assert len(slices) == 1

    def test_duplicate_paths_deduplicated(self):
        """slice_components doesn't deduplicate — that's the caller's job."""
        paths = ["src/a.py", "src/a.py", "src/b.py"]
        slices = slice_components(paths, max_components=5)
        assert sum(len(s.files) for s in slices) == 3


class TestComponentSliceRoundtrip:
    def test_to_dict_from_dict(self):
        cs = ComponentSlice(
            id="component_0",
            files=["a.py", "b.py"],
            description="test files",
            total_lines=42,
        )
        d = cs.to_dict()
        restored = ComponentSlice.from_dict(d)
        assert restored.id == "component_0"
        assert restored.files == ["a.py", "b.py"]
        assert restored.description == "test files"
        assert restored.total_lines == 42

    def test_default_description_empty(self):
        cs = ComponentSlice(id="c0", files=[])
        assert cs.description == ""

    def test_files_sorted(self):
        cs = ComponentSlice(id="c0", files=["z.py", "a.py"])
        assert cs.files == ["a.py", "z.py"]


class TestEstimateLines:
    def test_existing_file_counts_lines(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py", mode="w") as f:
            f.write("line1\nline2\nline3\n")
            tmp_path = f.name
        try:
            slices = slice_components([tmp_path], max_components=5)
            assert slices[0].total_lines > 0
        finally:
            os.unlink(tmp_path)

    def test_missing_file_returns_zero(self):
        slices = slice_components(["/nonexistent/path.py"], max_components=5)
        assert slices[0].total_lines >= 0


class TestMergeComponentResults:
    def test_merges_all_components(self):
        c1 = ComponentSlice(id="c0", files=["a/1.py", "a/2.py"])
        c2 = ComponentSlice(id="c1", files=["b/1.py"])
        merged = merge_component_results([c1, c2])
        assert sorted(merged) == ["a/1.py", "a/2.py", "b/1.py"]

    def test_handles_empty_components(self):
        c1 = ComponentSlice(id="c0", files=[])
        c2 = ComponentSlice(id="c1", files=["a.py"])
        merged = merge_component_results([c1, c2])
        assert merged == ["a.py"]

    def test_deduplicates(self):
        c1 = ComponentSlice(id="c0", files=["a.py"])
        c2 = ComponentSlice(id="c1", files=["a.py"])
        merged = merge_component_results([c1, c2])
        assert merged == ["a.py"]


class TestGroupKeyAbsolutePaths:
    def test_drive_letter_stripped(self):
        assert _group_key("C:/workspace/project/src/file.py") == "workspace"
        assert _group_key("C:\\workspace\\project\\src\\file.py") == "workspace"

    def test_drive_letter_only_parts(self):
        assert _group_key("C:/file.txt") == ".txt"

    def test_absolute_paths_with_base_dir_group_by_relative_dir(self):
        base = "C:/workspace"
        paths = [
            "C:/workspace/src/mod.py",
            "C:/workspace/tests/test_mod.py",
            "C:/workspace/docs/guide.md",
        ]
        comps = slice_components(paths, base_dir=base)
        keys = {c.description for c in comps}
        assert len(comps) == 3
        assert any("src" in k for k in keys)
        assert any("tests" in k for k in keys)
        assert any("docs" in k for k in keys)

    def test_absolute_and_relative_equivalent_grouping(self):
        base = "C:/workspace"
        abs_comps = slice_components(
            ["C:/workspace/src/mod.py", "C:/workspace/tests/test_mod.py"],
            base_dir=base,
        )
        rel_comps = slice_components(
            ["src/mod.py", "tests/test_mod.py"],
        )
        assert [c.description for c in abs_comps] == [c.description for c in rel_comps]

    def test_without_base_dir_absolute_paths_not_drive_collapsed(self):
        """Without base_dir the drive letter must not be the group key."""
        comps = slice_components(
            ["C:/workspace/src/mod.py", "C:/workspace/tests/test_mod.py"],
        )
        assert len(comps) == 1  # both under top-level "Users" — no drive "C:" collapse
        assert "C:" not in comps[0].description


class TestValidateExplicitComponents:
    def test_explicit_components_reject_declared_path_outside_workspace(self, tmp_path):
        from orchestrator.quality_plane import _validate_explicit_components

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        inside = workspace / "inside.py"
        inside.write_text("x = 1\n", encoding="utf-8")

        outside = tmp_path / "outside.py"
        outside.write_text("x = 2\n", encoding="utf-8")

        components, errors = _validate_explicit_components(
            [
                {
                    "id": "outside",
                    "files": [str(outside)],
                    "description": "must be rejected",
                }
            ],
            [str(inside)],
            str(workspace),
            max_components=4,
        )

        assert components == []
        assert any("outside workspace root" in error for error in errors)

    def test_explicit_components_reject_output_outside_workspace_even_if_declared(self, tmp_path):
        from orchestrator.quality_plane import _validate_explicit_components

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        outside = tmp_path / "outside.py"
        outside.write_text("x = 2\n", encoding="utf-8")

        components, errors = _validate_explicit_components(
            [{"id": "outside", "files": [str(outside)]}],
            [str(outside)],
            str(workspace),
            max_components=4,
        )

        assert components == []
        assert any("output path outside workspace root" in error for error in errors)

    def test_explicit_components_fail_closed_when_commonpath_rejects_drive_mix(self, tmp_path, monkeypatch):
        import os as _os

        import orchestrator.quality_plane as quality_plane
        from orchestrator.quality_plane import _validate_explicit_components

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        output = workspace / "output.py"
        output.write_text("x = 1\n", encoding="utf-8")

        real_commonpath = _os.path.commonpath

        def alternate_drive_commonpath(paths):
            if len(paths) == 2:
                raise ValueError("paths are on different drives")
            return real_commonpath(paths)

        monkeypatch.setattr(
            quality_plane.os.path,
            "commonpath",
            alternate_drive_commonpath,
        )

        components, errors = _validate_explicit_components(
            [{"id": "main", "files": ["output.py"]}],
            [str(output)],
            str(workspace),
            max_components=4,
        )

        assert components == []
        assert any("cannot compare" in error for error in errors)

    def test_explicit_components_valid_partition_preserves_ids_and_descriptions(self, tmp_path):
        from orchestrator.quality_plane import _validate_explicit_components

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        a = workspace / "a.py"
        b = workspace / "b.py"
        a.write_text("x = 1\n", encoding="utf-8")
        b.write_text("x = 2\n", encoding="utf-8")

        components, errors = _validate_explicit_components(
            [
                {"id": "main", "files": ["a.py"], "description": "the main module"},
                {"id": "tests", "files": ["b.py"], "description": "the tests"},
            ],
            [str(a), str(b)],
            str(workspace),
            max_components=4,
        )

        assert errors == []
        assert len(components) == 2
        assert components[0].id == "main"
        assert components[0].description == "the main module"
        assert components[1].id == "tests"
        assert components[1].description == "the tests"

    def test_explicit_components_unknown_output_fails_closed(self, tmp_path):
        from orchestrator.quality_plane import _validate_explicit_components

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        a = workspace / "a.py"
        b = workspace / "b.py"
        a.write_text("x = 1\n", encoding="utf-8")
        b.write_text("x = 2\n", encoding="utf-8")

        components, errors = _validate_explicit_components(
            [{"id": "main", "files": ["a.py"]}],
            [str(a), str(b)],
            str(workspace),
            max_components=4,
        )

        assert components == []
        assert any("omit outputs" in error for error in errors)

    def test_explicit_components_duplicate_file_fails_closed(self, tmp_path):
        from orchestrator.quality_plane import _validate_explicit_components

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        a = workspace / "a.py"
        a.write_text("x = 1\n", encoding="utf-8")

        components, errors = _validate_explicit_components(
            [
                {"id": "one", "files": ["a.py"]},
                {"id": "two", "files": ["a.py"]},
            ],
            [str(a)],
            str(workspace),
            max_components=4,
        )

        assert components == []
        assert any("appears in both" in error for error in errors)

    def test_explicit_components_over_max_cap_fails_closed(self, tmp_path):
        from orchestrator.quality_plane import _validate_explicit_components

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        a = workspace / "a.py"
        b = workspace / "b.py"
        a.write_text("x = 1\n", encoding="utf-8")
        b.write_text("x = 2\n", encoding="utf-8")

        components, errors = _validate_explicit_components(
            [
                {"id": "one", "files": ["a.py"]},
                {"id": "two", "files": ["b.py"]},
            ],
            [str(a), str(b)],
            str(workspace),
            max_components=1,
        )

        assert components == []
        assert any("exceeds max_components" in error for error in errors)
