#!/usr/bin/env python3
"""Run a read-only OpenCode review from a Codex plugin skill."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_CONFIG = PLUGIN_ROOT / "config" / "models.json"
DEFAULT_AGENT = "plan"


class BridgeError(RuntimeError):
    """Base class for user-facing bridge errors."""


class ModelResolutionError(BridgeError):
    """Raised when a model alias cannot be resolved safely."""


@dataclass(frozen=True)
class SlashReviewRequest:
    model: str
    focus: str


@dataclass(frozen=True)
class ModelConfig:
    aliases: dict[str, list[str]]
    preferred: dict[str, str]


def normalize_model_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_slash_review(value: str) -> SlashReviewRequest:
    text = value.strip()
    match = re.match(r"^/opencode(?::|/)review-([^\s]+)(?:\s+(.*))?$", text, re.DOTALL)
    if not match:
        raise BridgeError(
            "Expected /opencode:review-<model> or /opencode/review-<model>."
        )
    return SlashReviewRequest(model=match.group(1), focus=(match.group(2) or "").strip())


def load_model_config(path: Path = DEFAULT_MODEL_CONFIG) -> ModelConfig:
    if not path.exists():
        return ModelConfig(aliases={}, preferred={})
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    aliases: dict[str, list[str]] = {}
    for key, value in raw.get("aliases", {}).items():
        if isinstance(value, str):
            aliases[key] = [value]
        elif isinstance(value, list):
            aliases[key] = [str(item) for item in value]
    preferred = {str(k): str(v) for k, v in raw.get("preferred", {}).items()}
    return ModelConfig(aliases=aliases, preferred=preferred)


def command_from_value(value: str | None) -> list[str] | None:
    if not value:
        return None
    parts = shlex.split(value)
    if not parts:
        return None
    return parts


def find_opencode_command(explicit: str | None = None) -> list[str]:
    for value in (explicit, os.environ.get("OPENCODE_BIN")):
        command = command_from_value(value)
        if command:
            return command

    found = shutil.which("opencode")
    if found:
        return [found]

    repo = os.environ.get("OPENCODE_REPO")
    if repo:
        package_dir = Path(repo).expanduser().resolve() / "packages" / "opencode"
        return ["bun", "run", "--cwd", str(package_dir), "src/index.ts"]

    raise BridgeError(
        "OpenCode CLI was not found. Install `opencode`, set OPENCODE_BIN, "
        "or set OPENCODE_REPO to an OpenCode checkout."
    )


def run_subprocess(command: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def list_models(opencode_command: Sequence[str], cwd: Path) -> list[str]:
    result = run_subprocess([*opencode_command, "models"], cwd=cwd)
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise BridgeError(f"`opencode models` failed: {details}")
    models: list[str] = []
    for line in result.stdout.splitlines():
        model = line.strip()
        if model and "/" in model:
            models.append(model)
    return sorted(set(models))


def alias_patterns(requested: str, config: ModelConfig) -> list[str]:
    requested_norm = normalize_model_token(requested)
    patterns = [requested]
    for key, values in config.aliases.items():
        if normalize_model_token(key) == requested_norm:
            patterns.extend(values)
    return patterns


def split_model(model: str) -> tuple[str, str]:
    provider, model_id = model.split("/", 1)
    return provider, model_id


def resolve_model(requested: str, available: Sequence[str], config: ModelConfig) -> str:
    if not requested.strip():
        raise ModelResolutionError("Model alias is empty.")

    available_set = set(available)
    if requested in available_set:
        return requested

    patterns = alias_patterns(requested, config)
    pattern_norms = {normalize_model_token(pattern) for pattern in patterns}
    requested_norm = normalize_model_token(requested)
    preferred = config.preferred.get(requested) or config.preferred.get(requested_norm)

    exact_norm: list[str] = []
    suffix: list[str] = []
    contains: list[str] = []

    for model in available:
        _provider, model_id = split_model(model)
        full_norm = normalize_model_token(model)
        id_norm = normalize_model_token(model_id)
        if full_norm in pattern_norms or id_norm in pattern_norms:
            exact_norm.append(model)
            continue
        if any(full_norm.endswith(pattern) or id_norm.endswith(pattern) for pattern in pattern_norms):
            suffix.append(model)
            continue
        if any(pattern and pattern in id_norm for pattern in pattern_norms):
            contains.append(model)

    candidates = exact_norm or suffix or contains
    if preferred and preferred in candidates:
        return preferred
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        sample = ", ".join(available[:12])
        raise ModelResolutionError(
            f"Could not resolve model alias `{requested}`. "
            f"Try an exact provider/model ID. Available examples: {sample}"
        )
    choices = "\n".join(f"  - {model}" for model in candidates[:25])
    raise ModelResolutionError(
        f"Model alias `{requested}` matched multiple OpenCode models:\n{choices}\n"
        "Use an exact provider/model ID or add a preferred mapping in config/models.json."
    )


def build_review_prompt(cwd: Path, focus: str) -> str:
    focus_block = f"\n\nAdditional review focus:\n{focus.strip()}" if focus.strip() else ""
    return (
        "You are performing a read-only code review for the current git working tree.\n"
        f"Repository: {cwd}\n\n"
        "Inspect staged and unstaged changes relative to HEAD. Do not edit files, "
        "create commits, change configuration, or run mutating commands. Prioritize "
        "correctness bugs, regressions, security/data-loss risks, race conditions, "
        "API compatibility, and missing tests.\n\n"
        "Return findings first, sorted by severity. Use file:line references when "
        "possible. After findings, include open questions and a brief residual-risk "
        "or test-coverage note. If no issues are found, say that clearly."
        f"{focus_block}"
    )


def extract_review_text(stdout: str) -> str:
    chunks: list[str] = []
    saw_json = False
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        saw_json = True
        if event.get("type") != "text":
            continue
        part = event.get("part")
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())

    if chunks:
        return "\n\n".join(chunks).strip()
    return "" if saw_json else stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an OpenCode read-only review with a selected model."
    )
    parser.add_argument("--model", help="Model alias or exact provider/model ID.")
    parser.add_argument(
        "--slash",
        help="Parse a slash-style command such as /opencode:review-glm5.1 focus text.",
    )
    parser.add_argument("--cwd", default=os.getcwd(), help="Repository directory to review.")
    parser.add_argument("--agent", default=DEFAULT_AGENT, help="OpenCode agent to use.")
    parser.add_argument("--variant", help="OpenCode model variant, if supported by the provider.")
    parser.add_argument("--opencode-bin", help="OpenCode executable or command string.")
    parser.add_argument("--config", default=str(DEFAULT_MODEL_CONFIG), help="Model alias config.")
    parser.add_argument("--list-models", action="store_true", help="Print OpenCode models and exit.")
    parser.add_argument(
        "--skip-model-check",
        action="store_true",
        help="Pass an exact provider/model through without calling `opencode models`.",
    )
    parser.add_argument("--print-command", action="store_true", help="Print the OpenCode command.")
    parser.add_argument("focus", nargs="*", help="Optional review focus.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.exists():
        parser.error(f"--cwd does not exist: {cwd}")

    try:
        slash_focus = ""
        model = args.model
        if args.slash:
            parsed = parse_slash_review(args.slash)
            model = model or parsed.model
            slash_focus = parsed.focus
        if args.list_models:
            command = find_opencode_command(args.opencode_bin)
            print("\n".join(list_models(command, cwd)))
            return 0
        if not model:
            parser.error("--model is required unless --slash includes a model.")

        focus_parts = [slash_focus, *args.focus]
        focus = " ".join(part for part in focus_parts if part).strip()
        opencode_command = find_opencode_command(args.opencode_bin)
        config = load_model_config(Path(args.config).expanduser().resolve())

        if args.skip_model_check:
            if "/" not in model:
                raise ModelResolutionError("--skip-model-check requires an exact provider/model ID.")
            resolved_model = model
        else:
            resolved_model = resolve_model(model, list_models(opencode_command, cwd), config)

        review_prompt = build_review_prompt(cwd, focus)
        command = [
            *opencode_command,
            "run",
            "--dir",
            str(cwd),
            "--agent",
            args.agent,
            "--model",
            resolved_model,
            "--format",
            "json",
        ]
        if args.variant:
            command.extend(["--variant", args.variant])
        command.append(review_prompt)

        if args.print_command:
            print(shlex.join(command))
            return 0

        result = run_subprocess(command, cwd=cwd)
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            raise BridgeError(f"`opencode run` failed with exit code {result.returncode}: {details}")

        review = extract_review_text(result.stdout)
        if not review:
            raise BridgeError("OpenCode completed without returning review text.")
        print(review)
        return 0
    except BridgeError as error:
        print(f"opencode-review: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
