import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "opencode" / "scripts"))

import opencode_review as review


class ParseSlashReviewTests(unittest.TestCase):
    def test_parse_colon_form(self):
        parsed = review.parse_slash_review("/opencode:review-glm5.1 focus on tests")
        self.assertEqual(parsed.model, "glm5.1")
        self.assertEqual(parsed.focus, "focus on tests")

    def test_parse_slash_form(self):
        parsed = review.parse_slash_review("/opencode/review-kimi-k.2.6")
        self.assertEqual(parsed.model, "kimi-k.2.6")
        self.assertEqual(parsed.focus, "")


class ModelResolutionTests(unittest.TestCase):
    def test_exact_provider_model_wins(self):
        config = review.ModelConfig(aliases={}, preferred={})
        models = ["zai/glm-5.1", "moonshot/kimi-k2.6"]
        self.assertEqual(review.resolve_model("zai/glm-5.1", models, config), "zai/glm-5.1")

    def test_alias_resolves_by_normalized_model_id(self):
        config = review.ModelConfig(aliases={"glm5.1": ["glm-5.1"]}, preferred={})
        models = ["zai/glm-5.1"]
        self.assertEqual(review.resolve_model("glm5.1", models, config), "zai/glm-5.1")

    def test_ambiguous_alias_requires_preference(self):
        config = review.ModelConfig(aliases={"glm5.1": ["glm-5.1"]}, preferred={})
        models = ["zai/glm-5.1", "other/glm-5.1"]
        with self.assertRaises(review.ModelResolutionError):
            review.resolve_model("glm5.1", models, config)

    def test_preferred_mapping_breaks_tie(self):
        config = review.ModelConfig(
            aliases={"glm5.1": ["glm-5.1"]},
            preferred={"glm5.1": "zai/glm-5.1"},
        )
        models = ["zai/glm-5.1", "other/glm-5.1"]
        self.assertEqual(review.resolve_model("glm5.1", models, config), "zai/glm-5.1")


class OutputParsingTests(unittest.TestCase):
    def test_extracts_text_events(self):
        lines = [
            {"type": "tool_use", "part": {"tool": "bash"}},
            {"type": "text", "part": {"text": "Finding one."}},
            {"type": "text", "part": {"text": "Finding two."}},
        ]
        stdout = "\n".join(json.dumps(line) for line in lines)
        self.assertEqual(
            review.extract_review_text(stdout),
            "Finding one.\n\nFinding two.",
        )

    def test_falls_back_to_plain_stdout(self):
        self.assertEqual(review.extract_review_text("plain review"), "plain review")


if __name__ == "__main__":
    unittest.main()
