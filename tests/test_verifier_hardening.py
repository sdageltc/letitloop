"""Tests for hardened required_sections and render checks."""

import os

from orchestrator.verifier import (
    _get_markdown_headings,
    _run_render_check,
    _run_required_sections_check,
)


class TestMarkdownHeadings:
    def test_simple_headings(self):
        content = "# Title\n## Section 1\n### Subsection\n"
        headings = _get_markdown_headings(content)
        assert "Title" in headings
        assert "Section 1" in headings
        assert "Subsection" in headings

    def test_headings_inside_code_blocks_excluded(self):
        content = "# Real Section\n```\n# Fake Heading in Code\n## Another fake\n```\n## Real Subsection\n"
        headings = _get_markdown_headings(content)
        assert "Real Section" in headings
        assert "Real Subsection" in headings
        assert "Fake Heading in Code" not in headings
        assert "Another fake" not in headings

    def test_headings_with_extra_spaces(self):
        content = "##   Spaced   Heading   \n"
        headings = _get_markdown_headings(content)
        assert "Spaced   Heading" in headings

    def test_empty_content(self):
        headings = _get_markdown_headings("")
        assert headings == []

    def test_single_hash_not_heading(self):
        content = "## This is a heading\n"
        headings = _get_markdown_headings(content)
        assert "This is a heading" in headings


class TestRequiredSectionsCheck:
    def test_all_sections_present(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("# Intro\n## Usage\n## API\n")
        result = _run_required_sections_check(p, ["Intro", "Usage", "API"], str(tmp_path))
        assert result.passed is True
        assert "all 3 required sections found" in result.message

    def test_missing_section(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("# Intro\n## Usage\n")
        result = _run_required_sections_check(p, ["Intro", "Usage", "Security Notes"], str(tmp_path))
        assert result.passed is False
        assert "Security Notes" in result.message

    def test_case_insensitive_match(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("# introduction\n## usage\n")
        result = _run_required_sections_check(p, ["Introduction", "Usage"], str(tmp_path))
        assert result.passed is True

    def test_empty_required_list(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("# Anything\n")
        result = _run_required_sections_check(p, [], str(tmp_path))
        assert result.passed is True

    def test_file_not_found(self, tmp_path):
        result = _run_required_sections_check("nonexistent.md", ["Intro"], str(tmp_path))
        assert result.passed is False
        assert "file not found" in result.message

    def test_heading_in_code_block_not_matched(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("## Real Section\n```\n# Pretend Section\n```\n")
        result = _run_required_sections_check(p, ["Real Section"], str(tmp_path))
        assert result.passed is True
        result2 = _run_required_sections_check(p, ["Pretend Section"], str(tmp_path))
        assert result2.passed is False, "heading inside code block should not match"

    def test_multiple_missing(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("# Only This\n")
        result = _run_required_sections_check(p, ["Missing A", "Missing B"], str(tmp_path))
        assert result.passed is False
        assert "Missing A" in result.message
        assert "Missing B" in result.message


class TestRenderCheck:
    def test_markdown_pass(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("# Hello\n\nSimple markdown content.\n")
        result = _run_render_check(p, "markdown", str(tmp_path))
        assert result.passed is True

    def test_raw_latex_detected(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("# Math\n\nEquation: $x^2 + y^2 = z^2$\n")
        result = _run_render_check(p, "markdown", str(tmp_path))
        assert result.passed is False
        assert "LaTeX" in result.message

    def test_unresolved_anchor(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("# Links\n\nSee [reference]() for details.\n")
        result = _run_render_check(p, "markdown", str(tmp_path))
        assert result.passed is False
        assert "unresolved anchors" in result.message

    def test_unsupported_format(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.docx")
        with open(p, "w") as f:
            f.write("binary")
        result = _run_render_check(p, "pdf", str(tmp_path))
        assert result.passed is False
        assert "unsupported render format" in result.message

    def test_file_not_found(self, tmp_path):
        result = _run_render_check("missing.md", "markdown", str(tmp_path))
        assert result.passed is False
        assert "file not found" in result.message

    def test_message_labels_source_check(self, tmp_path):
        p = os.path.join(str(tmp_path), "doc.md")
        with open(p, "w") as f:
            f.write("# Ok\n")
        result = _run_render_check(p, "markdown", str(tmp_path))
        assert "source-level" in result.message
