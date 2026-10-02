"""Static wiring checks for the repository's offline quality gates."""

from pathlib import Path
import re


ROOT = Path(__file__).parents[1]


def test_release_requires_the_exact_tag_ref_and_verified_github_tag() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    shell_commands = workflow.replace("\\\n", " ")

    assert 'validate_versions.py --tag "$TAG" --require-tag-at-head' in workflow
    assert re.search(r'gh release create "\$TAG"\s+--verify-tag\b', shell_commands)


def test_pr_and_release_workflows_run_the_declared_linter() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    quality = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert 'ruff==0.16.9' in pyproject
    assert '[tool.ruff]' in pyproject
    assert "python -m ruff check ." in quality
    assert "python -m ruff check ." in release
