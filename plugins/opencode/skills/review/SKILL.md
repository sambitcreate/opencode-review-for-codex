---
name: review
description: Run OpenCode plan-agent code reviews from Codex with a selected model alias or provider/model ID. Use when the user mentions OpenCode review, opencode review, /opencode:review-*, /opencode/review-*, review-glm, review-kimi, or asks to review current changes using an OpenCode model.
---

# OpenCode Review

Use this skill to delegate a non-mutating review request to the local OpenCode CLI.

## Command Forms

Accept these user-facing forms:

- `/opencode:review-<model> [focus]`
- `/opencode/review-<model> [focus]`
- `/opencode/review-<model> --subagents <2-8> [focus]`
- `/opencode/review-<model> [focus] with <2-8> subagents`
- `Use OpenCode to review with <model> [focus]`

`<model>` can be a friendly OpenCode Go alias such as `glm5.1`, `qwen3.7-plus`, or
`kimi-k.2.6`, or an exact OpenCode `provider/model` ID. Exact IDs also work in slash-style
commands, for example `/opencode/review-google/gemini-3.1-pro-preview`.

When the user asks for OpenCode subagents, accept counts from 2 through 8. The bridge asks
OpenCode's `plan` agent to launch that many fresh `explore` subagents with distinct review lenses.
Keep this as an optional fan-out; do not add subagents unless the user requests them.

## Procedure

1. Resolve the bridge script relative to this skill file:
   `../../scripts/opencode-review`.
2. Run the bridge from the repository being reviewed. Prefer passing the exact user command via
   `--slash` when the user used a slash-style command.
3. Do not edit files as part of this review. The bridge always uses OpenCode's `plan` agent and
   does not expose an agent override. Treat non-mutating behavior as dependent on the local OpenCode
   plan-agent and permission configuration.
4. If the user requests a specific subagent count, pass `--subagents N` or include the user's
   slash/focus text verbatim so the bridge can parse `--subagents N`, `subagents=N`, or
   `with N subagents`.
5. Leave progress heartbeats enabled unless the user asks for quiet mode. The bridge writes
   heartbeat lines to stderr while OpenCode is still running, so Codex command output stays visibly
   active without mixing those lines into the final review text.
6. Relay OpenCode's findings directly. Keep findings first, ordered by severity. If OpenCode reports
   no issues, say that clearly and mention any residual risk.

## Examples

```bash
python3 /path/to/plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --slash "/opencode:review-glm5.1 focus on concurrency regressions"
```

```bash
python3 /path/to/plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --slash "/opencode/review-opencode-go/qwen3.7-plus focus on review coverage" \
  --progress-interval 15
```

```bash
python3 /path/to/plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --slash "/opencode/review-kimi-k.2.6 focus on release safety" \
  --subagents 4
```

```bash
python3 /path/to/plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --slash "/opencode/review-glm5.1 with 6 subagents focus on edge cases"
```

```bash
python3 /path/to/plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --model "anthropic/claude-sonnet-4-5" \
  "review API compatibility with Claude via Anthropic"
```

```bash
python3 /path/to/plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --model "google/gemini-3.1-pro-preview" \
  "review integration risks with Gemini"
```

To list models:

```bash
python3 /path/to/plugins/opencode/scripts/opencode-review --cwd "$PWD" --list-models
```

## Compatibility Note

Current Codex builds reject unknown slash commands whose command token contains `:` before skills
or hooks can see them. `/opencode/review-<model>` passes through current slash validation because
Codex treats slash names containing `/` as plain prompts. The plugin supports both forms; use the
colon form after applying the optional Codex patch in this repository or after Codex adds
namespaced plugin slash commands.
