"""Unit tests for the release-version contract validator."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_versions.py"
SPEC = importlib.util.spec_from_file_location("validate_versions", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validate_versions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_versions)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit_release_sources(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Version Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "release fixture")


def _write_release_sources(root: Path, version: str = "1.2.3", metadata_version: str = "0.3.4") -> None:
    (root / "pcxa").mkdir()
    (root / ".claude-plugin").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pcxa-cli"\nversion = "{}"\n'.format(version), encoding="utf-8"
    )
    (root / "pcxa" / "__init__.py").write_text(
        '__version__ = "{}"\n'.format(version), encoding="utf-8"
    )
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "pcxa", "version": version}), encoding="utf-8"
    )
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "metadata": {"version": metadata_version},
                "version": version,
                "plugins": [{"name": "pcxa", "version": version}],
            }
        ),
        encoding="utf-8",
    )


def test_release_sources_and_matching_tag_are_valid(tmp_path: Path) -> None:
    _write_release_sources(tmp_path)

    assert validate_versions.validate(tmp_path, "pcxa--v1.2.3") == []


def test_marketplace_metadata_version_is_not_a_release_version(tmp_path: Path) -> None:
    _write_release_sources(tmp_path, metadata_version="99.0.0")

    assert validate_versions.validate(tmp_path) == []


def test_mismatched_plugin_version_is_reported(tmp_path: Path) -> None:
    _write_release_sources(tmp_path)
    plugin_path = tmp_path / ".claude-plugin" / "plugin.json"
    plugin_path.write_text(json.dumps({"name": "pcxa", "version": "1.2.4"}), encoding="utf-8")

    errors = validate_versions.validate(tmp_path)

    assert errors == [".claude-plugin/plugin.json version is '1.2.4', expected '1.2.3'"]


def test_tag_must_use_the_release_prefix_and_matching_semver(tmp_path: Path) -> None:
    _write_release_sources(tmp_path)

    errors = validate_versions.validate(tmp_path, "v1.2.4")

    assert errors == ["tag 'v1.2.4' must start with 'pcxa--v'"]


def test_tag_version_must_match_release_sources(tmp_path: Path) -> None:
    _write_release_sources(tmp_path)

    errors = validate_versions.validate(tmp_path, "pcxa--v1.2.4")

    assert errors == ["tag 'pcxa--v1.2.4' has version '1.2.4', expected '1.2.3'"]


def test_cli_returns_nonzero_for_an_invalid_tag(tmp_path: Path, capsys) -> None:
    _write_release_sources(tmp_path)

    assert validate_versions.main(["--root", str(tmp_path), "--tag", "v1.2.3"]) == 1
    assert "must start with 'pcxa--v'" in capsys.readouterr().err


def test_required_release_tag_must_exist(tmp_path: Path) -> None:
    _write_release_sources(tmp_path)
    _commit_release_sources(tmp_path)

    errors = validate_versions.validate(
        tmp_path,
        "pcxa--v1.2.3",
        require_tag_at_head=True,
    )

    assert errors == ["tag ref 'refs/tags/pcxa--v1.2.3' does not exist"]


def test_required_release_tag_must_resolve_to_head(tmp_path: Path) -> None:
    _write_release_sources(tmp_path)
    _commit_release_sources(tmp_path)
    _git(tmp_path, "tag", "pcxa--v1.2.3")
    (tmp_path / "after-tag.txt").write_text("new head\n", encoding="utf-8")
    _git(tmp_path, "add", "after-tag.txt")
    _git(tmp_path, "commit", "-m", "move head")

    errors = validate_versions.validate(
        tmp_path,
        "pcxa--v1.2.3",
        require_tag_at_head=True,
    )

    assert len(errors) == 1
    assert errors[0].startswith("tag ref 'refs/tags/pcxa--v1.2.3' resolves to ")
    assert errors[0].endswith(", expected HEAD {}".format(_git(tmp_path, "rev-parse", "HEAD")))


def test_required_release_tag_at_head_is_valid(tmp_path: Path) -> None:
    _write_release_sources(tmp_path)
    _commit_release_sources(tmp_path)
    _git(tmp_path, "tag", "-a", "pcxa--v1.2.3", "-m", "release")

    assert validate_versions.validate(
        tmp_path,
        "pcxa--v1.2.3",
        require_tag_at_head=True,
    ) == []
