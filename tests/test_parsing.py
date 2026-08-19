"""Tests for orchestrator/parsing.py — robust LLM output parser."""

from orchestrator.parsing import (
    parse_llm_artifacts,
    repair_unclosed_fences,
    sanitize_text,
)


def _paths(*p):
    return list(p)


class TestSanitizeText:
    def test_removes_bom(self):
        assert sanitize_text("\ufeffhello") == "hello"

    def test_normalizes_crlf(self):
        assert sanitize_text("a\r\nb\r\nc") == "a\nb\nc"

    def test_normalizes_cr(self):
        assert sanitize_text("a\rb\rc") == "a\nb\nc"

    def test_removes_zero_width(self):
        assert sanitize_text("a\u200bb\u200cc") == "abc"


class TestRepairFences:
    def test_no_fences_no_change(self):
        assert repair_unclosed_fences("hello") == "hello"

    def test_even_fences_no_change(self):
        t = "```python\nx=1\n```"
        assert repair_unclosed_fences(t) == t

    def test_odd_fence_appends(self):
        t = "```python\nx=1"
        result = repair_unclosed_fences(t)
        assert result.endswith("\n```")

    def test_triple_fence_odd(self):
        t = "a\n```\nb\n```\nc\n```"
        result = repair_unclosed_fences(t)
        assert result.count("```") == 4


class TestParseJSON:
    def test_single_file_json(self):
        raw = '[{"path": "scratch/test.py", "content": "x=1"}]'
        result = parse_llm_artifacts(raw, _paths("scratch/test.py"))
        assert result.ok
        assert len(result.artifacts) == 1
        assert result.artifacts[0].path == "scratch/test.py"
        assert result.artifacts[0].content == "x=1"
        assert result.artifacts[0].parser_tier == "T1_JSON"

    def test_multi_file_json(self):
        raw = '[{"path": "a.py", "content": "x"}, {"path": "b.py", "content": "y"}]'
        result = parse_llm_artifacts(raw, _paths("a.py", "b.py"))
        assert result.ok
        assert len(result.artifacts) == 2

    def test_json_rejects_invented_path(self):
        raw = '[{"path": "scratch/evil.py", "content": "bad"}]'
        result = parse_llm_artifacts(raw, _paths("scratch/good.py"))
        assert not result.ok

    def test_json_rejects_absolute_path(self):
        raw = '[{"path": "/absolute/path.py", "content": "leak"}]'
        result = parse_llm_artifacts(raw, _paths("/absolute/path.py"))
        assert not result.ok


class TestParseXML:
    def test_xml_single_file(self):
        raw = '<file path="out.txt">hello</file>'
        result = parse_llm_artifacts(raw, _paths("out.txt"))
        assert result.ok
        assert result.artifacts[0].content.strip() == "hello"
        assert result.artifacts[0].parser_tier == "T2_XML"

    def test_xml_multi_file(self):
        raw = '<file path="a.py">x=1</file>\n<file path="b.py">y=2</file>'
        result = parse_llm_artifacts(raw, _paths("a.py", "b.py"))
        assert result.ok
        assert len(result.artifacts) == 2

    def test_xml_rejects_unknown_path(self):
        raw = '<file path="evil.txt">bad</file>'
        result = parse_llm_artifacts(raw, _paths("good.txt"))
        assert not result.ok

    def test_xml_name_attribute(self):
        raw = '<file name="out.txt">data</file>'
        result = parse_llm_artifacts(raw, _paths("out.txt"))
        assert result.ok


class TestParseHeaderFence:
    def test_header_fence_single(self):
        raw = "out.txt\n```\ncontent\n```"
        result = parse_llm_artifacts(raw, _paths("out.txt"))
        assert result.ok
        assert result.artifacts[0].content.strip() == "content"
        assert result.artifacts[0].parser_tier == "T3_HeaderFence"

    def test_header_fence_with_language(self):
        raw = "script.py\n```python\nx=1\n```"
        result = parse_llm_artifacts(raw, _paths("script.py"))
        assert result.ok
        assert result.artifacts[0].language == "python"

    def test_header_fence_multi(self):
        raw = "a.py\n```\nx=1\n```\nb.py\n```\ny=2\n```"
        result = parse_llm_artifacts(raw, _paths("a.py", "b.py"))
        assert result.ok
        assert len(result.artifacts) == 2


class TestParseAnnotatedFence:
    def test_annotated_fence(self):
        raw = "```python:path=out.py\nx=1\n```"
        result = parse_llm_artifacts(raw, _paths("out.py"))
        assert result.ok
        assert result.artifacts[0].path == "out.py"
        assert result.artifacts[0].parser_tier == "T4_AnnotatedFence"

    def test_annotated_fence_quoted(self):
        raw = '```python:path="out.py"\nx=1\n```'
        result = parse_llm_artifacts(raw, _paths("out.py"))
        assert result.ok


class TestParseGenericFence:
    def test_single_fence_single_expected(self):
        raw = "```python\nx=1\n```"
        result = parse_llm_artifacts(raw, _paths("out.py"))
        assert result.ok
        assert result.artifacts[0].path == "out.py"
        assert result.artifacts[0].parser_tier == "T5_GenericFence"

    def test_multi_fence_multi_expected(self):
        raw = "```\na\n```\n```\nb\n```"
        result = parse_llm_artifacts(raw, _paths("a.txt", "b.txt"))
        assert result.ok
        assert len(result.artifacts) == 2

    def test_multi_fence_less_fences_than_expected_rejected(self):
        raw = "```\na\n```\n```\nb\n```"
        result = parse_llm_artifacts(raw, _paths("a.txt", "b.txt", "c.txt"))
        assert not result.ok

    def test_multi_fence_exact_count(self):
        raw = "```\na\n```\n```\nb\n```\n```\nc\n```"
        result = parse_llm_artifacts(raw, _paths("a.txt", "b.txt", "c.txt"))
        assert result.ok
        assert len(result.artifacts) == 3

    def test_generic_fence_repairs_unclosed(self):
        raw = "```\nhello"
        result = parse_llm_artifacts(raw, _paths("out.txt"))
        assert result.ok
        assert "hello" in result.artifacts[0].content


class TestWholeBodyFallback:
    def test_fallback_single_expected(self):
        raw = "This is the entire output content"
        result = parse_llm_artifacts(raw, _paths("out.txt"))
        assert result.ok
        assert result.artifacts[0].content == raw
        assert result.artifacts[0].parser_tier == "T6_WholeBodyFallback"

    def test_fallback_strips_chatter(self):
        raw = "Sure! Here is the file content:\nprint('hello')"
        result = parse_llm_artifacts(raw, _paths("out.py"))
        assert result.ok
        assert "Sure!" not in result.artifacts[0].content
        assert "print" in result.artifacts[0].content

    def test_no_fallback_for_multi_expected(self):
        raw = "single blob with no fences"
        result = parse_llm_artifacts(raw, _paths("a.txt", "b.txt"))
        assert not result.ok


class TestEdgeCases:
    def test_empty_output(self):
        result = parse_llm_artifacts("", _paths("out.txt"))
        assert not result.ok

    def test_whitespace_only(self):
        result = parse_llm_artifacts("   \n  ", _paths("out.txt"))
        assert not result.ok

    def test_preserves_content_accuracy(self):
        code = "def foo():\n    return 42\n"
        raw = f"```python\n{code}```"
        result = parse_llm_artifacts(raw, _paths("a.py"))
        assert result.ok
        assert result.artifacts[0].content == code

    def test_rejects_dotdot_path(self):
        raw = '[{"path": "scratch/../../etc/passwd", "content": "bad"}]'
        result = parse_llm_artifacts(raw, _paths("scratch/../../etc/passwd"))
        assert not result.ok

    def test_forward_slash_normalization(self):
        raw = '[{"path": "scratch/dir/file.py", "content": "ok"}]'
        result = parse_llm_artifacts(raw, _paths("scratch/dir/file.py"))
        assert result.ok
