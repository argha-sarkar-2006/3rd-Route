"""Regression tests for OpenRouter credential selection."""

import os
import unittest
from unittest.mock import patch

import config
import llm


class _Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class OpenRouterKeyTests(unittest.TestCase):
    def test_key_candidates_use_primary_then_unique_alternate(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "primary",
                "OPENROUTER_API_KEY_ALT": "alternate",
            },
            clear=False,
        ):
            self.assertEqual(
                config.openrouter_api_keys(),
                ["primary", "alternate"],
            )

    def test_key_candidates_do_not_duplicate_same_key(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "same",
                "OPENROUTER_API_KEY_ALT": "same",
            },
            clear=False,
        ):
            self.assertEqual(config.openrouter_api_keys(), ["same"])

    def test_llm_retries_auth_failure_with_alternate_key(self):
        responses = [
            _Response(401, '{"error":"API key expired"}'),
            _Response(200, '{"choices":[{"message":{"content":"ready"}}]}'),
        ]

        with patch.object(config, "openrouter_api_keys", return_value=["bad", "good"]):
            with patch.object(llm.requests, "post", side_effect=responses) as post:
                result = llm.openrouter_chat(
                    [{"role": "user", "content": "ping"}],
                    model="example:free",
                )

        self.assertEqual(result["choices"][0]["message"]["content"], "ready")
        self.assertEqual(post.call_count, 2)
        self.assertIn("Bearer bad", post.call_args_list[0].kwargs["headers"]["Authorization"])
        self.assertIn("Bearer good", post.call_args_list[1].kwargs["headers"]["Authorization"])


if __name__ == "__main__":
    unittest.main()
