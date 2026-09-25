"""Regression checks for the vision provider configuration."""

import unittest

import config


class VisionConfigurationTests(unittest.TestCase):
    def test_vision_uses_one_free_openrouter_model_without_gemini_config(self):
        self.assertEqual(len(config.VISION_OPENROUTER_MODELS), 1)
        self.assertTrue(config.VISION_OPENROUTER_MODELS[0].endswith(":free"))
        self.assertFalse(hasattr(config, "gemini_api_key"))


if __name__ == "__main__":
    unittest.main()
