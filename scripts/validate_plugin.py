#!/usr/bin/env python3
"""Validate the local Codex OpenCode plugin package shape."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class ValidationError(RuntimeError):
    """Raised when plugin validation fails."""


def load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except json.JSONDecodeError as error:
        raise ValidationError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: expected a JSON object")
    return value


def require_string(data: dict, key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{path}: `{key}` must be a non-empty string")
    return value


def validate_manifest(plugin_root: Path) -> dict:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    if not manifest_path.exists():
        raise ValidationError(f"Missing manifest: {manifest_path}")

    manifest = load_json(manifest_path)
    name = require_string(manifest, "name", manifest_path)
    version = require_string(manifest, "version", manifest_path)
    require_string(manifest, "description", manifest_path)
    require_string(manifest, "license", manifest_path)

    if name != plugin_root.name:
        raise ValidationError(f"{manifest_path}: name `{name}` must match plugin folder `{plugin_root.name}`")
    if not SEMVER_RE.match(version):
        raise ValidationError(f"{manifest_path}: version `{version}` is not valid SemVer")

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        raise ValidationError(f"{manifest_path}: `interface` must be an object")
    for key in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        require_string(interface, key, manifest_path)

    skills = manifest.get("skills")
    if skills is not None:
        if not isinstance(skills, str) or not skills.strip():
            raise ValidationError(f"{manifest_path}: `skills` must be a non-empty string")
        skills_path = (plugin_root / skills).resolve()
        if not skills_path.exists() or not skills_path.is_dir():
            raise ValidationError(f"{manifest_path}: skills directory does not exist: {skills}")

    return manifest


def validate_skills(plugin_root: Path) -> None:
    skills_root = plugin_root / "skills"
    if not skills_root.exists():
        return

    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    if not skill_files:
        raise ValidationError(f"{skills_root}: expected at least one */SKILL.md file")

    for path in skill_files:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise ValidationError(f"{path}: missing YAML frontmatter")
        try:
            _prefix, frontmatter, _body = text.split("---", 2)
        except ValueError as error:
            raise ValidationError(f"{path}: malformed YAML frontmatter") from error
        if not re.search(r"(?m)^name:\s*\S+", frontmatter):
            raise ValidationError(f"{path}: missing frontmatter `name`")
        if not re.search(r"(?m)^description:\s*\S+", frontmatter):
            raise ValidationError(f"{path}: missing frontmatter `description`")


def validate_scripts(plugin_root: Path) -> None:
    script = plugin_root / "scripts" / "opencode-review"
    implementation = plugin_root / "scripts" / "opencode_review.py"
    for path in (script, implementation):
        if not path.exists() or not path.is_file():
            raise ValidationError(f"Missing script: {path}")
    if not script.stat().st_mode & stat.S_IXUSR:
        raise ValidationError(f"{script}: expected executable bit for owner")


def validate_marketplace(repo_root: Path, plugin_root: Path) -> None:
    marketplace_path = repo_root / ".agents" / "plugins" / "marketplace.json"
    if not marketplace_path.exists():
        return

    marketplace = load_json(marketplace_path)
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        raise ValidationError(f"{marketplace_path}: `plugins` must be an array")

    expected_path = f"./plugins/{plugin_root.name}"
    for item in plugins:
        if not isinstance(item, dict) or item.get("name") != plugin_root.name:
            continue
        source = item.get("source")
        policy = item.get("policy")
        if not isinstance(source, dict) or source.get("source") != "local" or source.get("path") != expected_path:
            raise ValidationError(f"{marketplace_path}: plugin `{plugin_root.name}` must source {expected_path}")
        if not isinstance(policy, dict):
            raise ValidationError(f"{marketplace_path}: plugin `{plugin_root.name}` missing policy object")
        for key in ("installation", "authentication"):
            require_string(policy, key, marketplace_path)
        require_string(item, "category", marketplace_path)
        return

    raise ValidationError(f"{marketplace_path}: missing plugin entry for `{plugin_root.name}`")


def validate(plugin_root: Path) -> None:
    resolved = plugin_root.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValidationError(f"Plugin root does not exist or is not a directory: {plugin_root}")

    repo_root = resolved.parents[1] if resolved.parent.name == "plugins" else resolved.parent
    validate_manifest(resolved)
    validate_skills(resolved)
    validate_scripts(resolved)
    validate_marketplace(repo_root, resolved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin_root", nargs="?", default="plugins/opencode")
    args = parser.parse_args()

    try:
        validate(Path(args.plugin_root))
    except ValidationError as error:
        print(f"validate-plugin: {error}", file=sys.stderr)
        return 1

    print(f"Plugin validation passed: {Path(args.plugin_root).expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
