import json
import subprocess
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

    def test_parse_slash_subagents_option(self):
        parsed = review.parse_slash_review(
            "/opencode/review-glm5.1 --subagents 4 focus on risky migrations"
        )
        self.assertEqual(parsed.model, "glm5.1")
        self.assertEqual(parsed.subagents, 4)
        self.assertEqual(parsed.focus, "focus on risky migrations")


class SubagentOptionTests(unittest.TestCase):
    def test_parse_natural_subagent_option(self):
        options = review.parse_review_options("focus on auth with 6 subagents")
        self.assertEqual(options.subagents, 6)
        self.assertEqual(options.focus, "focus on auth")

    def test_parse_subagent_option_with_punctuation(self):
        options = review.parse_review_options("subagents=4, focus on auth and edge cases")
        self.assertEqual(options.subagents, 4)
        self.assertEqual(options.focus, "focus on auth and edge cases")

    def test_rejects_subagent_count_outside_range(self):
        with self.assertRaises(review.BridgeError):
            review.parse_review_options("focus on auth with 9 subagents")

    def test_rejects_conflicting_subagent_counts(self):
        with self.assertRaises(review.BridgeError):
            review.parse_review_options("--subagents 3 focus on auth with 4 subagents")

    def test_review_prompt_includes_subagent_instructions_when_requested(self):
        prompt = review.build_review_prompt(ROOT, "focus on tests", subagents=3)
        self.assertIn("launch exactly 3 fresh `explore` subagents", prompt)
        self.assertIn("focus on tests", prompt)

    def test_review_prompt_omits_subagent_instructions_by_default(self):
        prompt = review.build_review_prompt(ROOT, "focus on tests")
        self.assertNotIn("OpenCode subagent fan-out", prompt)


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
            {"type": "step_start", "part": {}},
            {"type": "text", "part": {"text": "Finding one."}},
            {"type": "text", "part": {"text": "Finding two."}},
        ]
        stdout = "\n".join(json.dumps(line) for line in lines)
        output = review.extract_review_text(stdout)
        self.assertEqual(output.text, "Finding one.\n\nFinding two.")
        self.assertIsNone(output.error)

    def test_falls_back_to_plain_stdout(self):
        output = review.extract_review_text("plain review")
        self.assertEqual(output.text, "plain review")
        self.assertIsNone(output.error)

    def test_ignores_non_text_events_without_crashing(self):
        # OpenCode also emits tool_use, step_start, step_finish, and reasoning
        # events that have no `part.text`. The parser must skip them, not crash.
        lines = [
            {"type": "step_start", "part": {"id": "p1"}},
            {"type": "tool_use", "part": {"tool": "bash"}},
            {"type": "step_finish", "part": {"id": "p1"}},
            {"type": "text", "part": {"text": "Only finding."}},
        ]
        stdout = "\n".join(json.dumps(line) for line in lines)
        self.assertEqual(review.extract_review_text(stdout).text, "Only finding.")

    def test_surfaces_error_event_when_no_text_returned(self):
        lines = [
            {"type": "step_start", "part": {}},
            {"type": "error", "message": "model provider timed out"},
        ]
        stdout = "\n".join(json.dumps(line) for line in lines)
        output = review.extract_review_text(stdout)
        self.assertEqual(output.text, "")
        self.assertEqual(output.error, "model provider timed out")

    def test_surfaces_nested_error_event_message(self):
        lines = [{"type": "error", "error": {"message": "rate limited"}}]
        stdout = "\n".join(json.dumps(line) for line in lines)
        self.assertEqual(review.extract_review_text(stdout).error, "rate limited")


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

    def test_print_command_accepts_subagents_option(self):
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
                    "--subagents",
                    "4",
                    "--print-command",
                ]
            )

        self.assertEqual(code, 0)
        command = stdout.getvalue()
        self.assertIn("--agent plan", command)
        self.assertIn("launch exactly 4 fresh `explore` subagents", command)

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

    def test_run_subprocess_closes_stdin_to_avoid_hang(self):
        # OpenCode's `run` appends stdin to the prompt when it is not a TTY
        # (packages/opencode/src/cli/cmd/run.ts), so the bridge must give the
        # child an immediate EOF on stdin. A child that blocks reading stdin
        # should therefore finish promptly instead of hanging until timeout.
        result = review.run_subprocess(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write(sys.stdin.read() or 'no-stdin')",
            ],
            cwd=ROOT,
            timeout=2,
        )
        self.assertEqual(result.stdout, "no-stdin")

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


class CliPathCoverageTests(unittest.TestCase):
    """Cover user-facing CLI paths the original suite did not exercise."""

    def test_list_models_prints_available_models_and_exits_zero(self):
        stdout = StringIO()
        with (
            patch("opencode_review.find_opencode_command", return_value=["/bin/opencode"]),
            patch(
                "opencode_review.list_models",
                return_value=["opencode/glm-5.1", "opencode/kimi-k2.6"],
            ),
            patch("sys.stdout", stdout),
        ):
            code = review.main(["--cwd", str(ROOT), "--list-models"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().splitlines(), ["opencode/glm-5.1", "opencode/kimi-k2.6"])

    def test_skip_model_check_passes_exact_provider_model_through(self):
        stdout = StringIO()
        with (
            patch("opencode_review.list_models") as list_models,
            patch("opencode_review.shutil.which", return_value="/bin/opencode"),
            patch("sys.stdout", stdout),
        ):
            code = review.main(
                [
                    "--cwd",
                    str(ROOT),
                    "--model",
                    "anthropic/claude-sonnet-4-5",
                    "--skip-model-check",
                    "--print-command",
                ]
            )
        self.assertEqual(code, 0)
        list_models.assert_not_called()  # --skip-model-check must bypass model discovery
        self.assertIn("--model anthropic/claude-sonnet-4-5", stdout.getvalue())

    def test_skip_model_check_rejects_alias_without_provider(self):
        stderr = StringIO()
        with (
            patch("opencode_review.shutil.which", return_value="/bin/opencode"),
            patch("sys.stderr", stderr),
        ):
            code = review.main(
                [
                    "--cwd",
                    str(ROOT),
                    "--model",
                    "glm5.1",  # no provider/ -> ambiguous under --skip-model-check
                    "--skip-model-check",
                    "--print-command",
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("exact provider/model ID", stderr.getvalue())

    def test_variant_flag_is_appended_to_command(self):
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
                    "--model",
                    "opencode/glm-5.1",
                    "--variant",
                    "high",
                    "--skip-model-check",
                    "--print-command",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("--variant high", stdout.getvalue())

    def test_bridge_error_surfaces_as_exit_code_one(self):
        stderr = StringIO()
        with (
            patch("opencode_review.find_opencode_command", side_effect=review.BridgeError("nope")),
            patch("sys.stderr", stderr),
        ):
            code = review.main(["--cwd", str(ROOT), "--list-models"])
        self.assertEqual(code, 1)
        self.assertIn("nope", stderr.getvalue())

    def test_invalid_slash_command_raises_bridge_error(self):
        with self.assertRaises(review.BridgeError):
            review.parse_slash_review("/not-opencode/review-glm5.1")

    def test_extract_review_text_keeps_text_and_surfaces_error(self):
        lines = [
            {"type": "text", "part": {"text": "Finding one."}},
            {"type": "error", "message": "partial provider failure"},
        ]
        stdout = "\n".join(json.dumps(line) for line in lines)
        output = review.extract_review_text(stdout)
        self.assertEqual(output.text, "Finding one.")
        self.assertEqual(output.error, "partial provider failure")


class ModelListHardeningTests(unittest.TestCase):
    def test_list_models_strips_ansi_and_ignores_non_model_lines(self):
        raw_stdout = (
            "\x1b[1mMODEL\x1b[0m\n"
            "  opencode/glm-5.1   \n"
            "  opencode/kimi-k2.6\n"
            "some free-form note\n"
            "header/with space\n"
        )
        completed = subprocess.CompletedProcess(
            args=["opencode", "models"], returncode=0, stdout=raw_stdout, stderr=""
        )
        with patch("opencode_review.run_subprocess", return_value=completed):
            models = review.list_models(["opencode"], ROOT, 5)
        self.assertEqual(models, ["opencode/glm-5.1", "opencode/kimi-k2.6"])

    def test_split_model_rejects_missing_provider_or_id(self):
        with self.assertRaises(review.ModelResolutionError):
            review.split_model("no-slash")
        with self.assertRaises(review.ModelResolutionError):
            review.split_model("/leading-slash")
        with self.assertRaises(review.ModelResolutionError):
            review.split_model("trailing-slash/")


class FindOpencodeCommandTests(unittest.TestCase):
    def test_explicit_argument_wins(self):
        self.assertEqual(
            review.find_opencode_command("/custom/opencode --flag"),
            ["/custom/opencode", "--flag"],
        )

    def test_opencode_bin_env_is_used_when_no_explicit_arg(self):
        environ = {"OPENCODE_BIN": "/env/opencode"}
        with patch("opencode_review.os.environ", environ), patch(
            "opencode_review.shutil.which", return_value=None
        ):
            self.assertEqual(review.find_opencode_command(), ["/env/opencode"])

    def test_opencode_repo_env_builds_bun_command(self):
        repo = ROOT  # any existing path
        environ = {"OPENCODE_REPO": str(repo)}
        with patch("opencode_review.os.environ", environ), patch(
            "opencode_review.shutil.which", return_value=None
        ):
            command = review.find_opencode_command()
        self.assertEqual(command[:3], ["bun", "run", "--cwd"])
        self.assertIn("packages/opencode", command[3])

    def test_missing_command_raises_user_facing_error(self):
        environ = {}
        with patch("opencode_review.os.environ", environ), patch(
            "opencode_review.shutil.which", return_value=None
        ):
            with self.assertRaises(review.BridgeError) as error:
                review.find_opencode_command()
        self.assertIn("OpenCode CLI was not found", str(error.exception))


if __name__ == "__main__":
    unittest.main()
