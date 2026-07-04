# Changelog

All notable changes to the Codex OpenCode Plugin are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CI workflow (`.github/workflows/ci.yml`) that runs the unit-test suite and
  plugin validation on every push and pull request to `main`.
- `CHANGELOG.md` to track release history.
- Tests for previously uncovered CLI paths: `--list-models`,
  `--skip-model-check` (happy path and missing-provider error), `--variant`,
  `find_opencode_command` environment branches (`OPENCODE_BIN`,
  `OPENCODE_REPO`) and the not-found error, the `main()` exit-code-1 path,
  `extract_review_text` with both text and error events present, and invalid
  slash-command input.
- Test suite for `scripts/validate_plugin.py` covering manifest SemVer,
  required fields, malformed JSON, skill frontmatter, executable-bit, and the
  shipped plugin end to end.

### Changed
- The OpenCode child process now runs in its own session, and the bridge
  tears down the whole process group (SIGTERM then SIGKILL) on timeout,
  output overflow, or interrupt. This prevents orphaning agent/model
  descendant processes when a review is cancelled or times out.
- On a non-zero `opencode run` exit, the bridge now prints any review text it
  captured before reporting the failure, so a late failure no longer
  discards a long review. `Ctrl-C` (SIGINT) now exits with code 130 instead
  of printing a traceback.
- `list_models` strips ANSI escapes and only keeps lines matching a
  `provider/model` shape, so header rows or colored output cannot pollute the
  model candidate set.
- `split_model` now raises a user-facing `ModelResolutionError` instead of an
  uncaught `ValueError` for malformed model IDs.

### Fixed
- The release workflow's auto-bump is now idempotent: if a previous run pushed
  its version commit but failed before tagging, re-running reuses that
  unreleased version instead of bumping past it. The bump regex now accepts
  full SemVer (prerelease/build metadata), matching `scripts/validate_plugin.py`.

## [0.1.0]

### Added
- Initial Codex `opencode` plugin: a review skill and a bridge script around
  `opencode run` using the `plan` agent.
- Optional OpenCode subagent fan-out (`--subagents 2-8`) for broader reviews.
- Friendly model aliases (preferred OpenCode Go mappings) in
  `plugins/opencode/config/models.json`.
- Optional Codex slash-validation patch
  (`patches/codex-opencode-namespaced-slash.diff`) for the colon slash form.
- Progress heartbeats on stderr during long reviews.
- GitHub Actions release workflow with serialized, main-locked releases.
- Plugin manifest validator (`scripts/validate_plugin.py`).
