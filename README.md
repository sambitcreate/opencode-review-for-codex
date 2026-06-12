<img src="assets/opencode-codex-plugin.webp" alt="OpenCode and Codex plugin header" width="100%">

# Codex OpenCode Plugin

Run OpenCode model reviews from Codex using OpenCode's `plan` agent.

This repository is a Codex plugin marketplace. It installs the `opencode` plugin, which adds a
review skill and a local bridge script around `opencode run`.

## Install

Clone this repo, then add it as a local Codex marketplace:

```bash
git clone <this-repo-url> codex-opencode
cd codex-opencode
codex plugin marketplace add "$PWD"
codex plugin add opencode@codex-opencode
```

Open a new Codex thread after installing so the skill is loaded.

## Requirements

- Codex with plugin support.
- OpenCode installed as `opencode` on `PATH`.

If OpenCode is not on `PATH`, set one of:

```bash
export OPENCODE_BIN="/absolute/path/to/opencode"
export OPENCODE_REPO="/absolute/path/to/opencode-checkout"
```

`OPENCODE_REPO` runs the development checkout with Bun from `packages/opencode`.

## Usage

Today, this form works without patching Codex slash validation:

```text
/opencode/review-glm5.1 focus on regressions
/opencode/review-qwen3.7-plus focus on review coverage
```

After applying the optional Codex patch, the colon form also works:

```text
/opencode:review-glm5.1
/opencode:review-kimi-k.2.6 focus on security and missing tests
/opencode:review-opencode-go/deepseek-v4-pro focus on correctness
```

You can also ask naturally:

```text
Use OpenCode to review my current changes with provider/model-id.
```

The bridge resolves aliases by calling `opencode models`. Exact `provider/model` IDs are preferred
when available.

## OpenCode Go Models

The shipped aliases prefer OpenCode Go model IDs. The current Go set is:

```text
deepseek-v4-flash -> opencode-go/deepseek-v4-flash
deepseek-v4-pro   -> opencode-go/deepseek-v4-pro
glm5              -> opencode-go/glm-5
glm5.1            -> opencode-go/glm-5.1
kimi-k.2.5        -> opencode-go/kimi-k2.5
kimi-k.2.6        -> opencode-go/kimi-k2.6
mimo-v2.5         -> opencode-go/mimo-v2.5
mimo-v2.5-pro     -> opencode-go/mimo-v2.5-pro
minimax-m2.5      -> opencode-go/minimax-m2.5
minimax-m2.7      -> opencode-go/minimax-m2.7
minimax-m3        -> opencode-go/minimax-m3
qwen3.6-plus      -> opencode-go/qwen3.6-plus
qwen3.7-max       -> opencode-go/qwen3.7-max
qwen3.7-plus      -> opencode-go/qwen3.7-plus
```

You can always type the exact OpenCode Go ID yourself:

```text
/opencode/review-opencode-go/qwen3.7-max focus on architecture risks
```

## Custom Provider Models

Custom/provider-owned models do not need plugin aliases. Connect or configure the provider in
OpenCode, confirm the model appears in `opencode models`, then type the exact `provider/model` ID.

Claude via the Anthropic API:

```text
/opencode/review-anthropic/claude-sonnet-4-5 focus on API compatibility
/opencode/review-anthropic/claude-opus-4-5 focus on deep correctness
```

Gemini via the Gemini API in OpenCode's `google` provider:

```text
/opencode/review-google/gemini-3.1-pro-preview focus on integration risks
/opencode/review-google/gemini-2.5-pro focus on missing tests
```

For the bridge script, exact IDs work the same way:

```bash
python3 plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --model anthropic/claude-sonnet-4-5 \
  "review API compatibility"
```

If you have a local custom model ID that OpenCode can run but does not list, use
`--skip-model-check` with the direct script form.

## Progress Heartbeats

Model-backed reviews can be quiet until OpenCode finishes. The bridge prints progress heartbeats to
stderr so Codex has visible command output while the review is still running. Review text still goes
to stdout only after OpenCode returns.

By default, the bridge emits a heartbeat every 30 seconds:

```text
opencode-review: OpenCode review in progress with opencode-go/kimi-k2.6 started.
opencode-review: OpenCode review in progress with opencode-go/kimi-k2.6; still running after 1m 0s (stdout=0B, stderr=0B).
```

Tune or disable heartbeats with:

```bash
python3 plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --slash "/opencode/review-kimi-k.2.6 focus on the latest fix" \
  --progress-interval 15
```

Use `--progress-interval 0` for quiet mode.

## Non-Mutating Review Requests

The bridge always invokes OpenCode with `--agent plan` and does not expose an agent override. It
also prompts OpenCode to avoid edits, commits, configuration changes, and mutating shell commands.
Actual enforcement still depends on your local OpenCode `plan` agent and permission configuration,
so review those settings before using this on sensitive repositories.

## Model Aliases

Aliases live in:

```text
plugins/opencode/config/models.json
```

If an alias matches multiple OpenCode models, add a preferred mapping:

```json
{
  "preferred": {
    "glm5.1": "provider/glm-5.1"
  }
}
```

The shipped defaults prefer OpenCode Go:

```json
{
  "glm5.1": "opencode-go/glm-5.1",
  "kimi-k.2.6": "opencode-go/kimi-k2.6",
  "qwen3.7-plus": "opencode-go/qwen3.7-plus"
}
```

## Direct Script Usage

```bash
python3 plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --slash "/opencode/review-glm5.1 focus on API compatibility"
```

List models:

```bash
python3 plugins/opencode/scripts/opencode-review --cwd "$PWD" --list-models
```

Print the OpenCode command without running it:

```bash
python3 plugins/opencode/scripts/opencode-review \
  --cwd "$PWD" \
  --model provider/model-id \
  --skip-model-check \
  --print-command
```

## Slash Command Compatibility

Codex currently has built-in slash command validation. Unknown `/name` commands are rejected before
plugin skills can see them, except slash names containing `/`, which pass as normal prompts. That is
why `/opencode/review-...` works immediately and `/opencode:review-...` needs the optional patch in
`patches/codex-opencode-namespaced-slash.diff`.

Apply the patch to a Codex checkout if you want the exact colon form:

```bash
cd /path/to/codex
git apply /path/to/codex-opencode/patches/codex-opencode-namespaced-slash.diff
```

## Releases

Releases are published with the `Release` GitHub Actions workflow. Run it from the GitHub Actions
tab and choose a SemVer bump:

- `patch`: `0.1.0` -> `0.1.1`
- `minor`: `0.1.0` -> `0.2.0`
- `major`: `0.1.0` -> `1.0.0`

You can also provide an exact stable `version` such as `0.3.0`. The workflow strips local Codex
cachebuster metadata, updates `plugins/opencode/.codex-plugin/plugin.json`, runs tests and plugin
validation, commits `Release vX.Y.Z`, pushes it, and creates the GitHub release/tag.

Release runs are serialized, so two manual patch releases cannot race each other. If GitHub creates
a tag but fails before creating the release, rerun the workflow with the exact current version to
create the missing release without another version bump.

The `prerelease` input marks the GitHub Release as a prerelease. Plugin manifest versions remain
stable `X.Y.Z` values.

The repository must allow GitHub Actions to write to contents. In GitHub, check
`Settings -> Actions -> General -> Workflow permissions` and allow read/write if the release
workflow cannot push the version commit or tag.

## Development

Run tests:

```bash
python3 -m unittest discover -s tests
```

Validate the plugin manifest:

```bash
python3 scripts/validate_plugin.py plugins/opencode
```
