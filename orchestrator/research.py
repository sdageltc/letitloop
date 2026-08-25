"""
orchestrator/research.py
Adaptive Multi-Tier Research Engine (DuckDuckGo, arXiv, GitHub API, and MCP Bridges).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ResearchFinding:
    title: str
    summary: str
    source_url: str
    provider_name: str


class BaseResearchProvider(ABC):
    """Abstract contract for research sources."""

    @abstractmethod
    def search(self, query: str, max_results: int = 3) -> List[ResearchFinding]:
        pass


class DuckDuckGoProvider(BaseResearchProvider):
    """Zero-key public web search provider via DuckDuckGo HTML endpoint."""

    def search(self, query: str, max_results: int = 3) -> List[ResearchFinding]:
        findings: List[ResearchFinding] = []
        try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Extract result snippets and URLs
            results = re.findall(
                r'<a[^>]+class="result__snippet[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            )

            for href, snip in results[:max_results]:
                clean_snip = re.sub(r"<[^>]+>", "", snip).strip()
                uddg_match = re.search(r"uddg=([^&]+)", href)
                clean_url = urllib.parse.unquote(uddg_match.group(1)) if uddg_match else href
                title = clean_url.split("/")[-1] or "Web Result"
                if clean_snip:
                    findings.append(
                        ResearchFinding(
                            title=title,
                            summary=clean_snip,
                            source_url=clean_url,
                            provider_name="DuckDuckGo",
                        )
                    )
        except Exception:
            pass
        return findings


class ArXivProvider(BaseResearchProvider):
    """Zero-key academic paper search provider via arXiv public Atom/XML API."""

    def search(self, query: str, max_results: int = 3) -> List[ResearchFinding]:
        findings: List[ResearchFinding] = []
        try:
            encoded_query = urllib.parse.quote(f"all:{query}")
            url = f"http://export.arxiv.org/api/query?search_query={encoded_query}&start=0&max_results={max_results}"
            req = urllib.request.Request(url, headers={"User-Agent": "LetItLoop/0.1"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                xml_data = resp.read().decode("utf-8", errors="replace")

            root = ET.fromstring(xml_data)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("atom:entry", ns):
                title_node = entry.find("atom:title", ns)
                summary_node = entry.find("atom:summary", ns)
                id_node = entry.find("atom:id", ns)

                title = title_node.text.strip() if title_node is not None and title_node.text else "arXiv Paper"
                summary = (
                    summary_node.text.strip().replace("\n", " ")
                    if summary_node is not None and summary_node.text
                    else ""
                )
                source_url = id_node.text.strip() if id_node is not None and id_node.text else ""

                findings.append(
                    ResearchFinding(
                        title=title,
                        summary=summary[:300] + ("..." if len(summary) > 300 else ""),
                        source_url=source_url,
                        provider_name="arXiv",
                    )
                )
        except Exception:
            pass
        return findings


class GitHubSearchProvider(BaseResearchProvider):
    """Zero-key public GitHub repository search provider via GitHub REST API."""

    def search(self, query: str, max_results: int = 3) -> List[ResearchFinding]:
        findings: List[ResearchFinding] = []
        try:
            url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&sort=stars&order=desc&per_page={max_results}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "LetItLoop/0.1",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                raw_json = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw_json)

            for item in data.get("items", [])[:max_results]:
                findings.append(
                    ResearchFinding(
                        title=item.get("full_name", "GitHub Repo"),
                        summary=item.get("description", "No description provided.") or "",
                        source_url=item.get("html_url", ""),
                        provider_name="GitHub",
                    )
                )
        except Exception:
            pass
        return findings


class NoOpResearchProvider(BaseResearchProvider):
    """Safe offline fallback that returns empty findings with zero network activity."""

    def search(self, query: str, max_results: int = 3) -> List[ResearchFinding]:
        return []


class AdaptiveResearchCoordinator:
    """Coordinates multi-tier research providers with automatic graceful fallback."""

    def __init__(self, providers: Optional[List[BaseResearchProvider]] = None):
        if providers is not None:
            self.providers = providers
        else:
            self.providers = [
                GitHubSearchProvider(),
                ArXivProvider(),
                DuckDuckGoProvider(),
            ]

    def research(self, topic: str, max_results_per_provider: int = 2) -> List[ResearchFinding]:
        aggregated: List[ResearchFinding] = []
        for provider in self.providers:
            try:
                results = provider.search(topic, max_results=max_results_per_provider)
                aggregated.extend(results)
            except Exception:
                continue
        return aggregated
