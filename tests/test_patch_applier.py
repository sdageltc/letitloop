"""
tests/test_patch_applier.py
Unit tests for the Transactional Search/Replace Delta Patching Engine.
"""

from orchestrator.parsing import parse_llm_artifacts
from orchestrator.patch_applier import PatchApplier, SearchReplacePatchParser


def test_parse_search_replace_chunks():
    raw_patch = """
Some commentary from model.
<<<<<<< SEARCH
def old_function():
    return False
=======
def old_function():
    return True
>>>>>>> REPLACE
Other text.
<<<<<<< SEARCH
x = 1
=======
x = 2
>>>>>>> REPLACE
"""
    chunks = SearchReplacePatchParser.parse_chunks(raw_patch)
    assert len(chunks) == 2
    assert "def old_function():" in chunks[0].search_block
    assert "return True" in chunks[0].replace_block
    assert "x = 1" in chunks[1].search_block
    assert "x = 2" in chunks[1].replace_block


def test_apply_patch_exact_match():
    original = "line 1\nline 2\nline 3\n"
    patch = """<<<<<<< SEARCH
line 2
=======
line 2 modified
>>>>>>> REPLACE"""
    result = PatchApplier.apply_patch(original, patch)
    assert result.success is True
    assert result.chunks_applied == 1
    assert "line 2 modified" in result.modified_content
    assert "line 1\nline 2 modified\nline 3\n" == result.modified_content


def test_apply_patch_crlf_normalization():
    # Original has Windows CRLF, patch has Unix LF
    original = "line 1\r\nline 2\r\nline 3\r\n"
    patch = "<<<<<<< SEARCH\nline 2\n=======\nline 2 fixed\n>>>>>>> REPLACE"
    result = PatchApplier.apply_patch(original, patch)
    assert result.success is True
    assert result.chunks_applied == 1
    assert "line 2 fixed" in result.modified_content


def test_apply_patch_ambiguity_rejection():
    # File has multiple occurrences of target line
    original = "duplicate_line\nmiddle\nduplicate_line\n"
    patch = "<<<<<<< SEARCH\nduplicate_line\n=======\nreplacement\n>>>>>>> REPLACE"
    result = PatchApplier.apply_patch(original, patch)
    assert result.success is False
    assert "Ambiguous patch" in result.error_message


def test_apply_patch_fuzzy_whitespace_matching():
    # Original has trailing whitespace discrepancies
    original = "def foo():   \n    x = 10\n    return x\n"
    patch = "<<<<<<< SEARCH\ndef foo():\n    x = 10\n    return x\n=======\ndef foo():\n    x = 20\n    return x\n>>>>>>> REPLACE"
    result = PatchApplier.apply_patch(original, patch, fuzzy_whitespace=True)
    assert result.success is True
    assert "x = 20" in result.modified_content


def test_parsing_tier0_integration():
    raw_model_response = """
Here is the fix:
<<<<<<< SEARCH
def calculate():
    return 42
=======
def calculate():
    return 100
>>>>>>> REPLACE
"""
    res = parse_llm_artifacts(raw_model_response, expected_paths=["calc.py"])
    assert res.ok is True
    assert len(res.artifacts) == 1
    assert res.artifacts[0].parser_tier == "T0_SearchReplaceDelta"
    assert res.artifacts[0].path == "calc.py"
