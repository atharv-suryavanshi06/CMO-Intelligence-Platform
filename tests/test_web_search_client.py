from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError

from multimodal_rag.web_search import (
    SearchResult,
    TavilySearchProvider,
    WebSearchClient,
    WebSearchConfigurationError,
    WebSearchProviderError,
)


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class WebSearchClientTests(unittest.TestCase):
    def test_client_delegates_query_and_max_results(self) -> None:
        calls = []

        class Provider:
            def search(self, query, max_results):
                calls.append((query, max_results))
                return []

        client = WebSearchClient(Provider())
        self.assertEqual(client.search("  market news  ", max_results=7), [])
        self.assertEqual(calls, [("market news", 7)])

    def test_client_validates_query_and_max_results(self) -> None:
        client = WebSearchClient(lambda *_: [])
        with self.assertRaises(ValueError):
            client.search("   ")
        with self.assertRaises(ValueError):
            client.search("query", max_results=0)
        with self.assertRaises(ValueError):
            client.search("query", max_results=21)

    def test_tavily_normalizes_results_and_missing_optional_fields(self) -> None:
        payload = {
            "results": [
                {"title": "One", "url": "https://one.test", "content": "Text", "published_date": "2026-08-25T10:00:00Z", "score": 0.8},
                {"title": "Two", "url": "https://two.test", "content": "More", "published_date": "not-a-date"},
            ]
        }
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}), patch(
            "multimodal_rag.web_search.providers.tavily.urlopen", return_value=_Response(payload)
        ) as open_mock:
            results = WebSearchClient(TavilySearchProvider()).search("latest trends", max_results=2)

        self.assertEqual(len(results), 2)
        self.assertIsInstance(results[0], SearchResult)
        self.assertEqual(results[0].source, "tavily")
        self.assertEqual(results[0].score, 0.8)
        self.assertIsNotNone(results[0].published_at)
        self.assertIsNone(results[1].published_at)
        request = open_mock.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["query"], "latest trends")
        self.assertEqual(body["max_results"], 2)
        self.assertFalse(body["include_answer"])
        self.assertNotIn("test-key", str(request.headers))

    def test_empty_tavily_results_are_empty(self) -> None:
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}), patch(
            "multimodal_rag.web_search.providers.tavily.urlopen", return_value=_Response({})
        ):
            self.assertEqual(TavilySearchProvider().search("nothing", 5), [])

    def test_missing_api_key_fails_before_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "multimodal_rag.web_search.providers.tavily.urlopen"
        ) as open_mock:
            with self.assertRaises(WebSearchConfigurationError):
                TavilySearchProvider().search("query", 5)
        open_mock.assert_not_called()

    def test_provider_errors_are_translated(self) -> None:
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}), patch(
            "multimodal_rag.web_search.providers.tavily.urlopen", side_effect=URLError("offline")
        ):
            with self.assertRaises(WebSearchProviderError):
                TavilySearchProvider().search("query", 5)


if __name__ == "__main__":
    unittest.main()
