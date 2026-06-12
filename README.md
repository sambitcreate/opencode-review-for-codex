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
```

After applying the optional Codex patch, the colon form also works:

```text
/opencode:review-glm5.1
/opencode:review-kimi-k.2.6 focus on security and missing tests
```

You can also ask naturally:

```text
Use OpenCode to review my current changes with provider/model-id.
```

The bridge resolves aliases by calling `opencode models`. Exact `provider/model` IDs are preferred
when available.

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

The shipped defaults prefer:

```json
{
  "glm5.1": "opencode/glm-5.1",
  "kimi-k.2.6": "opencode/kimi-k2.6"
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

## Development

Run tests:

```bash
python3 -m unittest discover -s tests
```

Validate the plugin manifest:

```bash
python3 /path/to/plugin-creator/scripts/validate_plugin.py plugins/opencode
```
