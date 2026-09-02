import json
import tempfile
import unittest
from pathlib import Path

from app.models import chat_model_options, require_chat_model


class ChatModelRoutingTests(unittest.TestCase):
    def test_only_configured_allowlisted_models_are_selectable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "models.json"
            path.write_text(json.dumps([
                {"model": "deepseek-v4-flash", "api_key": "test", "base_url": "https://example.invalid/v1"},
                {"model": "not-allowlisted", "api_key": "test", "base_url": "https://example.invalid/v1"},
            ]), encoding="utf-8")
            options = chat_model_options(path)
            self.assertTrue(next(item for item in options if item["id"] == "deepseek-v4-flash")["configured"])
            self.assertNotIn("not-allowlisted", {item["id"] for item in options})
            self.assertEqual(require_chat_model("deepseek-v4-flash", path)["api_key"], "test")
            with self.assertRaises(ValueError):
                require_chat_model("not-allowlisted", path)


if __name__ == "__main__":
    unittest.main()
