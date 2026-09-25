"""Small credential-free web search adapter for Ollama reasoning."""

from html.parser import HTMLParser
from urllib.parse import quote_plus

import requests


class _ResultParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._current = None
        self._capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())

        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": attrs.get("href", ""), "snippet": ""}
            self._capture = "title"
        elif tag in ("a", "div") and "result__snippet" in classes and self._current:
            self._capture = "snippet"

    def handle_data(self, data):
        if self._current and self._capture:
            self._current[self._capture] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._current and self._capture == "title":
            self._capture = None
        elif self._current and self._capture == "snippet" and tag == "div":
            result = {
                key: " ".join(value.split())
                for key, value in self._current.items()
            }
            if result["url"] and result["title"]:
                self.results.append(result)
            self._current = None
            self._capture = None


def search(query, limit=5, log=print):
    """Return title/URL/snippet records, or [] when search is unavailable."""
    query = (query or "").strip()
    if not query:
        return []

    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/?q=" + quote_plus(query),
            headers={"User-Agent": "SovereignAIWorkbench/1.0"},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        log(f"  web search unavailable ({error}); continuing locally")
        return []

    parser = _ResultParser()
    parser.feed(response.text)
    return parser.results[:limit]


def format_results(results):
    return "\n\n".join(
        f"[{index}] {item['title']}\n{item['url']}\n{item['snippet']}"
        for index, item in enumerate(results or [], 1)
    )
