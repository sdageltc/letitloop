"""
tests/test_research_providers.py
Unit tests for the Adaptive Multi-Tier Research Engine.
"""

from unittest.mock import patch, MagicMock
from orchestrator.research import (
    BaseResearchProvider,
    ResearchFinding,
    DuckDuckGoProvider,
    ArXivProvider,
    GitHubSearchProvider,
    NoOpResearchProvider,
    AdaptiveResearchCoordinator,
)


def test_duckduckgo_provider_formatting():
    provider = DuckDuckGoProvider()
    mock_html = """
    <html><body>
    <a class="result__url" href="https://example.com/repo">https://example.com/repo</a>
    <a class="result__snippet">Python fast algorithm implementation for AST optimization.</a>
    </body></html>
    """
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_html.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        findings = provider.search("python ast optimization")
        assert len(findings) >= 1
        assert "AST" in findings[0].summary or "http" in findings[0].source_url
        assert findings[0].provider_name == "DuckDuckGo"


def test_arxiv_provider_xml_parsing():
    provider = ArXivProvider()
    mock_atom_xml = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Self-Evolving Code Generation with AST Invariant Validation</title>
        <summary>We present a technique for AST surgical slicing in AI agents.</summary>
        <id>http://arxiv.org/abs/2608.12345v1</id>
      </entry>
    </feed>
    """
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_atom_xml.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        findings = provider.search("self-evolving code generation")
        assert len(findings) == 1
        assert "AST Invariant" in findings[0].title
        assert "surgical slicing" in findings[0].summary
        assert findings[0].provider_name == "arXiv"


def test_github_repo_provider_parsing():
    provider = GitHubSearchProvider()
    mock_json = """{
      "items": [
        {
          "full_name": "example/fast-ast-tools",
          "html_url": "https://github.com/example/fast-ast-tools",
          "description": "High speed Python AST refactoring and cyclomatic reduction library."
        }
      ]
    }"""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_json.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        findings = provider.search("fast ast tools")
        assert len(findings) == 1
        assert findings[0].title == "example/fast-ast-tools"
        assert "AST refactoring" in findings[0].summary
        assert findings[0].provider_name == "GitHub"


def test_adaptive_coordinator_offline_fallback():
    # If all network requests raise errors, coordinator must fail open with empty list (zero crash)
    with patch("urllib.request.urlopen", side_effect=Exception("Network unreachable")):
        coord = AdaptiveResearchCoordinator()
        findings = coord.research("python optimization patterns")
        assert isinstance(findings, list)
        assert len(findings) == 0  # Safe offline fallback


def test_adaptive_coordinator_returns_aggregated_findings():
    coord = AdaptiveResearchCoordinator()
    dummy_findings = [
        ResearchFinding(
            title="Design Pattern",
            summary="Use strategy pattern for modular refactoring.",
            source_url="https://example.com/pattern",
            provider_name="LocalMock",
        )
    ]
    mock_provider = MagicMock(spec=BaseResearchProvider)
    mock_provider.search.return_value = dummy_findings
    coord.providers = [mock_provider]

    res = coord.research("strategy pattern")
    assert len(res) == 1
    assert res[0].title == "Design Pattern"
    assert "strategy pattern" in res[0].summary
