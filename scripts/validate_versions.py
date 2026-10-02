#!/usr/bin/env python3
"""Validate pcxa release-version sources without treating schema metadata as a release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
TAG_PREFIX = "pcxa--v"
PACKAGE_VERSION_RE = re.compile(
    r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE
)
INIT_VERSION_RE = re.compile(
    r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE
)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("{}: {}".format(path, exc)) from exc


def _read_match(path: Path, pattern: re.Pattern[str], label: str) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("{}: {}".format(path, exc)) from exc
    match = pattern.search(content)
    if not match:
        raise ValueError("{}: missing {}".format(path, label))
    return match.group(1)


def source_versions(root: Path) -> Dict[str, str]:
    """Return the release versions that must agree for a pcxa publication.

    ``marketplace.metadata.version`` is intentionally excluded: it describes the
    marketplace schema/metadata, not the published pcxa plugin version.
    """
    package = _read_match(root / "pyproject.toml", PACKAGE_VERSION_RE, "project version")
    init = _read_match(root / "pcxa" / "__init__.py", INIT_VERSION_RE, "__version__")

    plugin_path = root / ".claude-plugin" / "plugin.json"
    plugin = _read_json(plugin_path)
    if not isinstance(plugin, dict) or not isinstance(plugin.get("version"), str):
        raise ValueError("{}: missing string version".format(plugin_path))

    marketplace_path = root / ".claude-plugin" / "marketplace.json"
    marketplace = _read_json(marketplace_path)
    if not isinstance(marketplace, dict) or not isinstance(marketplace.get("version"), str):
        raise ValueError("{}: missing string top-level version".format(marketplace_path))
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError("{}: missing plugins list".format(marketplace_path))
    pcxa_plugins = [entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == "pcxa"]
    if len(pcxa_plugins) != 1 or not isinstance(pcxa_plugins[0].get("version"), str):
        raise ValueError("{}: expected exactly one pcxa plugin with a string version".format(marketplace_path))

    return {
        "pyproject.toml [project].version": package,
        "pcxa/__init__.py __version__": init,
        ".claude-plugin/plugin.json version": plugin["version"],
        ".claude-plugin/marketplace.json version": marketplace["version"],
        ".claude-plugin/marketplace.json plugins[name=pcxa].version": pcxa_plugins[0]["version"],
    }


def _resolve_git_commit(root: Path, revision: str) -> Optional[str]:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", revision],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _validate_tag_at_head(root: Path, tag: str) -> List[str]:
    ref = "refs/tags/{}".format(tag)
    tag_commit = _resolve_git_commit(root, "{}^{{commit}}".format(ref))
    if tag_commit is None:
        return ["tag ref {!r} does not exist".format(ref)]

    head_commit = _resolve_git_commit(root, "HEAD")
    if head_commit is None:
        return ["could not resolve HEAD in {!s}".format(root)]
    if tag_commit != head_commit:
        return [
            "tag ref {!r} resolves to {}, expected HEAD {}".format(
                ref,
                tag_commit,
                head_commit,
            )
        ]
    return []


def validate(
    root: Path,
    tag: Optional[str] = None,
    require_tag_at_head: bool = False,
) -> List[str]:
    """Return validation errors; an empty list means versions are publishable."""
    try:
        versions = source_versions(root)
    except ValueError as exc:
        return [str(exc)]

    errors = []
    for label, version in versions.items():
        if not SEMVER_RE.fullmatch(version):
            errors.append("{} is not MAJOR.MINOR.PATCH: {!r}".format(label, version))

    expected = versions["pyproject.toml [project].version"]
    for label, version in versions.items():
        if version != expected:
            errors.append("{} is {!r}, expected {!r}".format(label, version, expected))

    tag_is_valid = False
    if tag is not None:
        if not tag.startswith(TAG_PREFIX):
            errors.append("tag {!r} must start with {!r}".format(tag, TAG_PREFIX))
        else:
            tag_version = tag[len(TAG_PREFIX) :]
            if not SEMVER_RE.fullmatch(tag_version):
                errors.append("tag {!r} must end with MAJOR.MINOR.PATCH".format(tag))
            elif tag_version != expected:
                errors.append("tag {!r} has version {!r}, expected {!r}".format(tag, tag_version, expected))
            else:
                tag_is_valid = True

    if require_tag_at_head:
        if tag is None:
            errors.append("--require-tag-at-head requires --tag")
        elif tag_is_valid:
            errors.extend(_validate_tag_at_head(root, tag))

    return errors


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root (for tests)")
    parser.add_argument("--tag", help="release tag to validate, e.g. pcxa--v0.7.2")
    parser.add_argument(
        "--require-tag-at-head",
        action="store_true",
        help="require refs/tags/<tag> to exist and resolve to HEAD",
    )
    args = parser.parse_args(argv)

    errors = validate(args.root, args.tag, args.require_tag_at_head)
    if errors:
        print("version validation failed:", file=sys.stderr)
        for error in errors:
            print("- {}".format(error), file=sys.stderr)
        return 1
    print("version validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
