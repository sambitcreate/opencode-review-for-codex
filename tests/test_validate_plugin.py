import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_plugin as validate  # noqa: E402


def _write_manifest(plugin_root: Path, **overrides) -> None:
    manifest_dir = plugin_root / ".codex-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": plugin_root.name,
        "version": "0.1.0",
        "description": "Run OpenCode model reviews from Codex.",
        "license": "MIT",
        "skills": "./skills/",
        "interface": {
            "displayName": "OpenCode",
            "shortDescription": "Review changes with OpenCode.",
            "longDescription": "OpenCode review bridge.",
            "developerName": "Contributors",
            "category": "Productivity",
        },
    }
    manifest.update(overrides)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def _make_skill(plugin_root: Path, name: str = "review") -> None:
    skill_dir = plugin_root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Run a review.\n---\n# body\n",
        encoding="utf-8",
    )


def _make_script(plugin_root: Path, executable: bool = True) -> None:
    script = plugin_root / "scripts" / "opencode-review"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    impl = plugin_root / "scripts" / "opencode_review.py"
    impl.write_text("print('ok')\n", encoding="utf-8")
    if executable:
        script.chmod(0o755)


class ManifestValidationTests(unittest.TestCase):
    def test_valid_plugin_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            _write_manifest(plugin_root)
            _make_skill(plugin_root)
            _make_script(plugin_root)
            # validate() also checks the marketplace; skip it via direct calls.
            validate.validate_manifest(plugin_root)
            validate.validate_skills(plugin_root)
            validate.validate_scripts(plugin_root)

    def test_name_must_match_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            _write_manifest(plugin_root, name="different")
            with self.assertRaises(validate.ValidationError) as error:
                validate.validate_manifest(plugin_root)
            self.assertIn("must match plugin folder", str(error.exception))

    def test_invalid_semver_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            _write_manifest(plugin_root, version="1.0")
            with self.assertRaises(validate.ValidationError) as error:
                validate.validate_manifest(plugin_root)
            self.assertIn("not valid SemVer", str(error.exception))

    def test_prerelease_semver_is_accepted(self):
        # SEMVER_RE must accept prerelease/build metadata so the validator and
        # the release workflow agree on what counts as a valid version.
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            (plugin_root / "skills").mkdir()
            _write_manifest(plugin_root, version="0.2.0-rc.1")
            validate.validate_manifest(plugin_root)  # should not raise

    def test_missing_required_string_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            manifest_dir = plugin_root / ".codex-plugin"
            manifest_dir.mkdir(parents=True, exist_ok=True)
            (manifest_dir / "plugin.json").write_text(
                json.dumps({"name": "opencode", "version": "0.1.0"}), encoding="utf-8"
            )
            with self.assertRaises(validate.ValidationError) as error:
                validate.validate_manifest(plugin_root)
            self.assertIn("must be a non-empty string", str(error.exception))

    def test_malformed_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            manifest_dir = plugin_root / ".codex-plugin"
            manifest_dir.mkdir(parents=True, exist_ok=True)
            (manifest_dir / "plugin.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(validate.ValidationError) as error:
                validate.validate_manifest(plugin_root)
            self.assertIn("invalid JSON", str(error.exception))


class SkillsValidationTests(unittest.TestCase):
    def test_skill_without_frontmatter_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            _write_manifest(plugin_root)
            skill_dir = plugin_root / "skills" / "review"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# no frontmatter", encoding="utf-8")
            with self.assertRaises(validate.ValidationError) as error:
                validate.validate_skills(plugin_root)
            self.assertIn("frontmatter", str(error.exception))

    def test_skill_missing_description_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            _write_manifest(plugin_root)
            skill_dir = plugin_root / "skills" / "review"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: review\n---\nbody\n", encoding="utf-8"
            )
            with self.assertRaises(validate.ValidationError) as error:
                validate.validate_skills(plugin_root)
            self.assertIn("description", str(error.exception))

    def test_empty_skills_dir_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            (plugin_root / "skills").mkdir(parents=True)
            with self.assertRaises(validate.ValidationError) as error:
                validate.validate_skills(plugin_root)
            self.assertIn("at least one", str(error.exception))


class ScriptsValidationTests(unittest.TestCase):
    def test_non_executable_wrapper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            _make_script(plugin_root, executable=False)
            with self.assertRaises(validate.ValidationError) as error:
                validate.validate_scripts(plugin_root)
            self.assertIn("executable", str(error.exception))

    def test_missing_implementation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "opencode"
            plugin_root.mkdir()
            _make_script(plugin_root, executable=True)
            (plugin_root / "scripts" / "opencode_review.py").unlink()
            with self.assertRaises(validate.ValidationError):
                validate.validate_scripts(plugin_root)


class ShippedPluginValidationTests(unittest.TestCase):
    """The plugin shipped in this repo must always validate end to end."""

    def test_shipped_plugin_validates(self):
        with patch("sys.stdout"):
            validate.validate(ROOT / "plugins" / "opencode")


if __name__ == "__main__":
    unittest.main()
