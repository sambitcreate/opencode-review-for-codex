#!/usr/bin/env python3
"""Run an OpenCode plan-agent review from a Codex plugin skill."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_CONFIG = PLUGIN_ROOT / "config" / "models.json"
DEFAULT_AGENT = "plan"
DEFAULT_REVIEW_SUBAGENT = "explore"
DEFAULT_MODEL_LIST_TIMEOUT_SECONDS = 30
DEFAULT_RUN_TIMEOUT_SECONDS = 1800
DEFAULT_PROGRESS_INTERVAL_SECONDS = 30
MIN_REVIEW_SUBAGENTS = 2
MAX_REVIEW_SUBAGENTS = 8
MAX_DIAGNOSTIC_CHARS = 4000
MAX_OUTPUT_BYTES = 5_000_000
PROCESS_GROUP_GRACE_SECONDS = 2
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MODEL_LINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._/-]*$")


class BridgeError(RuntimeError):
    """Base class for user-facing bridge errors."""


class ModelResolutionError(BridgeError):
    """Raised when a model alias cannot be resolved safely."""


@dataclass(frozen=True)
class SlashReviewRequest:
    model: str
    focus: str
    subagents: int | None


@dataclass(frozen=True)
class ReviewOptions:
    focus: str
    subagents: int | None


@dataclass(frozen=True)
class ReviewOutput:
    """Parsed OpenCode run output: review text plus any captured error events."""

    text: str
    error: str | None


@dataclass(frozen=True)
class ModelConfig:
    aliases: dict[str, list[str]]
    preferred: dict[str, str]


def normalize_model_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def validate_subagent_count(value: int | str) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError) as error:
        raise BridgeError(
            f"Subagent count must be an integer from {MIN_REVIEW_SUBAGENTS} to "
            f"{MAX_REVIEW_SUBAGENTS}."
        ) from error
    if count < MIN_REVIEW_SUBAGENTS or count > MAX_REVIEW_SUBAGENTS:
        raise BridgeError(
            f"Subagent count must be between {MIN_REVIEW_SUBAGENTS} and "
            f"{MAX_REVIEW_SUBAGENTS}; got {count}."
        )
    return count


def merge_subagent_counts(*counts: int | None) -> int | None:
    requested = [count for count in counts if count is not None]
    if not requested:
        return None
    unique = sorted(set(requested))
    if len(unique) > 1:
        rendered = ", ".join(str(count) for count in unique)
        raise BridgeError(f"Conflicting subagent counts were requested: {rendered}.")
    return requested[0]


def parse_review_options(value: str) -> ReviewOptions:
    text = value.strip()
    subagents: int | None = None

    patterns = [
        re.compile(r"(?P<prefix>^|\s)--subagents(?:=|\s+)(?P<count>\d+)(?=$|[\s,.;])", re.I),
        re.compile(r"(?P<prefix>^|\s)subagents?\s*[:=]\s*(?P<count>\d+)(?=$|[\s,.;])", re.I),
        re.compile(
            r"(?P<prefix>^|\s)(?:use|using|spawn|spawning|with|and)\s+"
            r"(?P<count>\d+)\s+subagents?\b",
            re.I,
        ),
        re.compile(r"(?P<prefix>^|\s)subagents?\s+(?P<count>\d+)\b", re.I),
        re.compile(r"(?P<prefix>^|\s)(?P<count>\d+)\s+subagents?\b", re.I),
    ]

    def replace_directive(match: re.Match[str]) -> str:
        nonlocal subagents
        count = validate_subagent_count(match.group("count"))
        subagents = merge_subagent_counts(subagents, count)
        return match.group("prefix")

    for pattern in patterns:
        text = pattern.sub(replace_directive, text)

    focus = re.sub(r"\s+", " ", text).strip(" ,;.")
    return ReviewOptions(focus=focus, subagents=subagents)


def parse_slash_review(value: str) -> SlashReviewRequest:
    text = value.strip()
    match = re.match(r"^/opencode(?::|/)review-([^\s]+)(?:\s+(.*))?$", text, re.DOTALL)
    if not match:
        raise BridgeError(
            "Expected /opencode:review-<model> or /opencode/review-<model>."
        )
    options = parse_review_options(match.group(2) or "")
    return SlashReviewRequest(
        model=match.group(1),
        focus=options.focus,
        subagents=options.subagents,
    )


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


def truncate_text(value: str, limit: int = MAX_DIAGNOSTIC_CHARS) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return value[:limit].rstrip() + f"\n... truncated {omitted} characters ..."


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def run_subprocess(
    command: Sequence[str],
    cwd: Path | None = None,
    timeout: int | float | None = None,
    progress_label: str | None = None,
    progress_interval: int | float = 0,
) -> subprocess.CompletedProcess[str]:
    rendered = shlex.join(list(command))
    started_at = time.monotonic()
    deadline = time.monotonic() + timeout if timeout else None
    poll_interval_seconds = 0.05
    next_progress_at = started_at + progress_interval if progress_label and progress_interval > 0 else None

    def read_output(handle: BinaryIO) -> str:
        handle.seek(0)
        return handle.read(MAX_OUTPUT_BYTES).decode("utf-8", errors="replace")

    def output_size(handle: BinaryIO) -> int:
        return os.fstat(handle.fileno()).st_size

    def terminate_process_group(process: subprocess.Popen) -> None:
        """Tear down the child and any descendants it spawned.

        OpenCode ``run`` launches the plan agent and (with ``--subagents``)
        further model/subagent processes. Killing only the direct child on a
        timeout or interrupt would orphan those descendants, so the child is
        started in its own session and we signal the whole group: SIGTERM
        first (graceful), then SIGKILL after a short grace period.
        """
        if process.poll() is not None:
            return
        try:
            pgid = os.getpgid(process.pid)
        except (ProcessLookupError, PermissionError):
            pgid = process.pid
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, signum)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=PROCESS_GROUP_GRACE_SECONDS if signum == signal.SIGTERM else None)
                return
            except subprocess.TimeoutExpired:
                continue

    with tempfile.TemporaryFile("w+b") as stdout_file, tempfile.TemporaryFile("w+b") as stderr_file:
        try:
            # ``start_new_session=True`` puts the child (and its descendants)
            # in a new process group so we can tear the whole tree down on
            # timeout or interrupt instead of orphaning agent/model processes.
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
        except OSError as error:
            raise BridgeError(f"Failed to start command: {rendered}: {error}") from error

        # Let Ctrl-C reach this process so the operator can cancel a long
        # review, then ensure the child group is always torn down in finally.
        previous_int_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            if progress_label and progress_interval > 0:
                print(f"opencode-review: {progress_label} started.", file=sys.stderr, flush=True)

            while True:
                stdout_len = output_size(stdout_file)
                stderr_len = output_size(stderr_file)
                if stdout_len > MAX_OUTPUT_BYTES or stderr_len > MAX_OUTPUT_BYTES:
                    raise BridgeError(
                        "Command produced too much output "
                        f"(stdout={stdout_len} bytes, stderr={stderr_len} bytes): {rendered}"
                    )

                returncode = process.poll()
                if returncode is not None:
                    return subprocess.CompletedProcess(
                        list(command),
                        returncode,
                        stdout=read_output(stdout_file),
                        stderr=read_output(stderr_file),
                    )

                if deadline is not None and time.monotonic() >= deadline:
                    raise BridgeError(f"Command timed out after {timeout}s: {rendered}")

                now = time.monotonic()
                if next_progress_at is not None and now >= next_progress_at:
                    elapsed = format_duration(now - started_at)
                    print(
                        f"opencode-review: {progress_label}; still running after {elapsed} "
                        f"(stdout={format_bytes(stdout_len)}, stderr={format_bytes(stderr_len)}).",
                        file=sys.stderr,
                        flush=True,
                    )
                    next_progress_at = now + progress_interval

                time.sleep(poll_interval_seconds)
        finally:
            # Always restore the caller's SIGINT handler and tear down the
            # child group, whether the command succeeded, timed out,
            # overflowed, or was interrupted by Ctrl-C.
            signal.signal(signal.SIGINT, previous_int_handler)
            terminate_process_group(process)


def ensure_bounded_output(result: subprocess.CompletedProcess[str], command_name: str) -> None:
    stdout_len = len((result.stdout or "").encode("utf-8"))
    stderr_len = len((result.stderr or "").encode("utf-8"))
    if stdout_len > MAX_OUTPUT_BYTES or stderr_len > MAX_OUTPUT_BYTES:
        raise BridgeError(
            f"`{command_name}` produced too much output "
            f"(stdout={stdout_len} bytes, stderr={stderr_len} bytes)."
        )


def list_models(opencode_command: Sequence[str], cwd: Path, timeout: int | float) -> list[str]:
    result = run_subprocess([*opencode_command, "models"], cwd=cwd, timeout=timeout)
    ensure_bounded_output(result, "opencode models")
    if result.returncode != 0:
        details = truncate_text((result.stderr or result.stdout).strip())
        raise BridgeError(f"`opencode models` failed: {details}")
    models: list[str] = []
    for line in result.stdout.splitlines():
        # `opencode models` output is plain text with no documented format,
        # so strip ANSI decoration and keep only lines that look like a real
        # `provider/model` ID. This avoids polluting the candidate set with
        # header rows or color codes.
        model = ANSI_ESCAPE_RE.sub("", line).strip()
        if model and MODEL_LINE_RE.match(model):
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
    provider, _, model_id = model.partition("/")
    if not provider or not model_id:
        raise ModelResolutionError(
            f"Model `{model}` is not a valid provider/model ID."
        )
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


def build_review_prompt(cwd: Path, focus: str, subagents: int | None = None) -> str:
    focus_block = f"\n\nAdditional review focus:\n{focus.strip()}" if focus.strip() else ""
    subagent_block = ""
    if subagents is not None:
        subagent_block = (
            "\n\nOpenCode subagent fan-out:\n"
            f"- Before finalizing, launch exactly {subagents} fresh "
            f"`{DEFAULT_REVIEW_SUBAGENT}` subagents concurrently with the task tool.\n"
            "- Give each subagent a complete, self-contained, non-mutating review prompt "
            "that includes the repository path, the user's focus, and a distinct review lens.\n"
            "- Use the first N review lenses from this list: correctness/regressions, "
            "security/data-loss, concurrency/state, API/contracts, tests/coverage, "
            "edge cases/config, dependencies/build/release, docs/user impact.\n"
            "- Wait for all subagent results, then deduplicate and verify material claims "
            "against the working tree before reporting findings.\n"
            f"- If the task tool or `{DEFAULT_REVIEW_SUBAGENT}` subagent is unavailable "
            "or permission-denied, continue the review yourself and mention that limitation "
            "in the residual-risk note."
        )
    return (
        "You are performing a code review for the current git working tree.\n"
        f"Repository: {cwd}\n\n"
        "Inspect staged and unstaged changes relative to HEAD. Treat this as a "
        "non-mutating task: do not edit files, create commits, change configuration, "
        "or run mutating commands. Prioritize "
        "correctness bugs, regressions, security/data-loss risks, race conditions, "
        "API compatibility, and missing tests.\n\n"
        "Return findings first, sorted by severity. Use file:line references when "
        "possible. After findings, include open questions and a brief residual-risk "
        "or test-coverage note. If no issues are found, say that clearly."
        f"{subagent_block}"
        f"{focus_block}"
    )


def extract_review_text(stdout: str) -> ReviewOutput:
    """Parse newline-delimited OpenCode JSON events into review text.

    OpenCode emits several event types on stdout (text, tool_use, step_*,
    error, ...). Only `type == "text"` events carry the review; `type ==
    "error"` events carry failure detail that we surface instead of reporting
    a generic "no review text" message. Other event types are ignored.
    """
    chunks: list[str] = []
    error: str | None = None
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
        event_type = event.get("type")
        if event_type == "error":
            message = _extract_error_message(event)
            if message and message.strip():
                error = (error + "\n" + message) if error else message
            continue
        if event_type != "text":
            continue
        part = event.get("part")
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())

    if chunks:
        return ReviewOutput(text="\n\n".join(chunks).strip(), error=error)
    return ReviewOutput(text="" if saw_json else stdout.strip(), error=error)


def _extract_error_message(event: object) -> str:
    """Best-effort extraction of a human-readable message from an error event."""
    if not isinstance(event, dict):
        return ""
    for key in ("message", "error"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            inner = value.get("message")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return truncate_text(json.dumps(event, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an OpenCode review with the plan agent and a selected model."
    )
    parser.add_argument("--model", help="Model alias or exact provider/model ID.")
    parser.add_argument(
        "--slash",
        help="Parse a slash-style command such as /opencode:review-glm5.1 focus text.",
    )
    parser.add_argument("--cwd", default=os.getcwd(), help="Repository directory to review.")
    parser.add_argument("--variant", help="OpenCode model variant, if supported by the provider.")
    parser.add_argument("--opencode-bin", help="OpenCode executable or command string.")
    parser.add_argument("--config", default=str(DEFAULT_MODEL_CONFIG), help="Model alias config.")
    parser.add_argument("--list-models", action="store_true", help="Print OpenCode models and exit.")
    parser.add_argument(
        "--model-list-timeout",
        type=float,
        default=DEFAULT_MODEL_LIST_TIMEOUT_SECONDS,
        help="Seconds to wait for `opencode models`.",
    )
    parser.add_argument(
        "--run-timeout",
        type=float,
        default=DEFAULT_RUN_TIMEOUT_SECONDS,
        help="Seconds to wait for `opencode run`.",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=DEFAULT_PROGRESS_INTERVAL_SECONDS,
        help="Seconds between review progress heartbeats on stderr. Use 0 to disable.",
    )
    parser.add_argument(
        "--subagents",
        type=int,
        metavar="N",
        help="Ask OpenCode to fan out the review through N fresh explore subagents (2-8).",
    )
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
    if not cwd.is_dir():
        parser.error(f"--cwd must be a directory: {cwd}")

    try:
        slash_focus = ""
        slash_subagents: int | None = None
        model = args.model
        if args.slash:
            parsed = parse_slash_review(args.slash)
            model = model or parsed.model
            slash_focus = parsed.focus
            slash_subagents = parsed.subagents
        if args.list_models:
            command = find_opencode_command(args.opencode_bin)
            print("\n".join(list_models(command, cwd, args.model_list_timeout)))
            return 0
        if not model:
            parser.error("--model is required unless --slash includes a model.")

        focus_parts = [slash_focus, *args.focus]
        parsed_options = parse_review_options(" ".join(part for part in focus_parts if part).strip())
        cli_subagents = (
            validate_subagent_count(args.subagents) if args.subagents is not None else None
        )
        subagents = merge_subagent_counts(cli_subagents, slash_subagents, parsed_options.subagents)
        focus = parsed_options.focus
        opencode_command = find_opencode_command(args.opencode_bin)
        config = load_model_config(Path(args.config).expanduser().resolve())

        if args.skip_model_check:
            if "/" not in model:
                raise ModelResolutionError("--skip-model-check requires an exact provider/model ID.")
            resolved_model = model
        else:
            resolved_model = resolve_model(
                model,
                list_models(opencode_command, cwd, args.model_list_timeout),
                config,
            )

        review_prompt = build_review_prompt(cwd, focus, subagents)
        command = [
            *opencode_command,
            "run",
            "--dir",
            str(cwd),
            "--agent",
            DEFAULT_AGENT,
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

        result = run_subprocess(
            command,
            cwd=cwd,
            timeout=args.run_timeout,
            progress_label=(
                f"OpenCode review in progress with {resolved_model}"
                + (f" using {subagents} subagents" if subagents is not None else "")
            ),
            progress_interval=args.progress_interval,
        )
        ensure_bounded_output(result, "opencode run")
        review = extract_review_text(result.stdout)

        if result.returncode != 0:
            # A long review can fail near the end and still have emitted
            # useful findings. Print whatever review text we captured before
            # reporting the failure, so the work is not silently discarded.
            if review.text:
                print(review.text)
            details = truncate_text((result.stderr or result.stdout).strip())
            error_suffix = f" OpenCode reported: {review.error}" if review.error else ""
            raise BridgeError(
                f"`opencode run` failed with exit code {result.returncode}: {details}{error_suffix}"
            )

        if not review.text:
            if review.error:
                raise BridgeError(f"OpenCode reported an error: {review.error}")
            raise BridgeError("OpenCode completed without returning review text.")
        if review.error:
            print(f"opencode-review: OpenCode reported an error: {review.error}", file=sys.stderr)
        print(review.text)
        return 0
    except BridgeError as error:
        print(f"opencode-review: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Operator cancelled the review. The child process group was already
        # torn down inside run_subprocess; exit 130 is the conventional code
        # for SIGINT cancellation.
        print("opencode-review: interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
