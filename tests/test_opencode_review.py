import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "opencode" / "scripts"))

import opencode_review as review


OPENCODE_GO_MODELS = [
    "opencode-go/deepseek-v4-flash",
    "opencode-go/deepseek-v4-pro",
    "opencode-go/glm-5",
    "opencode-go/glm-5.1",
    "opencode-go/kimi-k2.5",
    "opencode-go/kimi-k2.6",
    "opencode-go/mimo-v2.5",
    "opencode-go/mimo-v2.5-pro",
    "opencode-go/minimax-m2.5",
    "opencode-go/minimax-m2.7",
    "opencode-go/minimax-m3",
    "opencode-go/qwen3.6-plus",
    "opencode-go/qwen3.7-max",
    "opencode-go/qwen3.7-plus",
]


class ParseSlashReviewTests(unittest.TestCase):
    def test_parse_colon_form(self):
        parsed = review.parse_slash_review("/opencode:review-glm5.1 focus on tests")
        self.assertEqual(parsed.model, "glm5.1")
        self.assertEqual(parsed.focus, "focus on tests")

    def test_parse_slash_form(self):
        parsed = review.parse_slash_review("/opencode/review-kimi-k.2.6")
        self.assertEqual(parsed.model, "kimi-k.2.6")
        self.assertEqual(parsed.focus, "")

    def test_parse_provider_model_id_form(self):
        parsed = review.parse_slash_review(
            "/opencode/review-google/gemini-3.1-pro-preview focus on integration risks"
        )
        self.assertEqual(parsed.model, "google/gemini-3.1-pro-preview")
        self.assertEqual(parsed.focus, "focus on integration risks")


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

    def test_shipped_alias_preferences_resolve_local_like_models(self):
        config = review.load_model_config(ROOT / "plugins" / "opencode" / "config" / "models.json")
        models = [
            "opencode/glm-5.1",
            "opencode-go/glm-5.1",
            "zai-coding-plan/glm-5.1",
            "opencode/kimi-k2.6",
            "opencode-go/kimi-k2.6",
        ]
        self.assertEqual(review.resolve_model("glm5.1", models, config), "opencode-go/glm-5.1")
        self.assertEqual(review.resolve_model("kimi-k.2.6", models, config), "opencode-go/kimi-k2.6")

    def test_shipped_alias_preferences_cover_opencode_go_catalog(self):
        config = review.load_model_config(ROOT / "plugins" / "opencode" / "config" / "models.json")
        duplicated_models = [
            f"opencode/{model.split('/', 1)[1]}"
            for model in OPENCODE_GO_MODELS
        ]
        duplicated_models.extend(
            [
                "deepseek/deepseek-v4-flash",
                "deepseek/deepseek-v4-pro",
                "zai-coding-plan/glm-5.1",
            ]
        )
        available = sorted([*duplicated_models, *OPENCODE_GO_MODELS])

        for model in OPENCODE_GO_MODELS:
            with self.subTest(model=model):
                alias = model.split("/", 1)[1]
                self.assertEqual(review.resolve_model(alias, available, config), model)
                self.assertEqual(review.resolve_model(review.normalize_model_token(alias), available, config), model)


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


class CliBoundaryTests(unittest.TestCase):
    def test_print_command_uses_plan_agent_and_resolved_model(self):
        stdout = StringIO()
        with (
            patch("opencode_review.list_models", return_value=["opencode/glm-5.1"]),
            patch("opencode_review.shutil.which", return_value="/bin/opencode"),
            patch("sys.stdout", stdout),
        ):
            code = review.main(
                [
                    "--cwd",
                    str(ROOT),
                    "--slash",
                    "/opencode/review-glm5.1 focus on tests",
                    "--print-command",
                ]
            )

        self.assertEqual(code, 0)
        command = stdout.getvalue()
        self.assertIn("--agent plan", command)
        self.assertIn("--model opencode/glm-5.1", command)
        self.assertNotIn("--agent build", command)

    def test_run_subprocess_timeout_is_user_facing(self):
        with self.assertRaises(review.BridgeError) as error:
            review.run_subprocess(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(2)",
                ],
                cwd=ROOT,
                timeout=0.01,
            )
        self.assertIn("timed out", str(error.exception))

    def test_run_subprocess_start_failure_is_user_facing(self):
        with self.assertRaises(review.BridgeError) as error:
            review.run_subprocess(
                ["/definitely/not/opencode"],
                cwd=ROOT,
                timeout=1,
            )
        self.assertIn("Failed to start command", str(error.exception))

    def test_run_subprocess_limits_output_before_returning(self):
        with patch("opencode_review.MAX_OUTPUT_BYTES", 64):
            with self.assertRaises(review.BridgeError) as error:
                review.run_subprocess(
                    [
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.write('x' * 1024); sys.stdout.flush()",
                    ],
                    cwd=ROOT,
                    timeout=2,
                )
        self.assertIn("too much output", str(error.exception))

    def test_run_subprocess_emits_progress_heartbeats(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            result = review.run_subprocess(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(0.12); print('done')",
                ],
                cwd=ROOT,
                timeout=2,
                progress_label="OpenCode review in progress with test/model",
                progress_interval=0.05,
            )

        self.assertEqual(result.stdout.strip(), "done")
        progress = stderr.getvalue()
        self.assertIn("started", progress)
        self.assertIn("still running after", progress)

    def test_cwd_must_be_directory(self):
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as error:
                review.main(
                    [
                        "--cwd",
                        str(ROOT / "README.md"),
                        "--model",
                        "opencode/glm-5.1",
                        "--skip-model-check",
                        "--print-command",
                    ]
                )
        self.assertEqual(error.exception.code, 2)
        self.assertIn("--cwd must be a directory", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
