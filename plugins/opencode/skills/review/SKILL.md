---
name: review
description: Run read-only OpenCode code reviews from Codex with a selected model alias or provider/model ID. Use when the user mentions OpenCode review, opencode review, /opencode:review-*, /opencode/review-*, review-glm, review-kimi, or asks to review current changes using an OpenCode model.
---

# OpenCode Review

Use this skill to delegate a read-only code review to the local OpenCode CLI.

## Command Forms

Accept these user-facing forms:

- `/opencode:review-<model> [focus]`
- `/opencode/review-<model> [focus]`
- `Use OpenCode to review with <model> [focus]`

`<model>` can be a friendly alias such as `glm5.1` or `kimi-k.2.6`, or an exact OpenCode
`provider/model` ID when not using the slash-style command.

## Procedure

1. Resolve the bridge script relative to this skill file:
   `../../scripts/opencode-review`.
2. Run the bridge from the repository being reviewed. Prefer passing the exact user command via
   `--slash` when the user used a slash-style command.
3. Do not edit files as part of this review. The bridge prompt tells OpenCode to stay read-only and
   uses OpenCode's `plan` agent by default.
4. Relay OpenCode's findings directly. Keep findings first, ordered by severity. If OpenCode reports
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
  --model "provider/model-id" \
  --focus "review API compatibility"
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
