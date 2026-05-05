#!/usr/bin/env bash
# Cut a new pcxa-skill release.
#
# Usage:  scripts/release.sh <version>
# Example: scripts/release.sh 0.3.4
#
# Idempotent: every phase checks the desired state before acting, so a
# re-run after a partial failure (e.g. push interrupted) resumes from
# wherever it stopped instead of erroring or duplicating work. Re-running
# on a fully-released version is a no-op that prints "already done."
#
# Phases:
#   1. Bump version in the 4 source-of-truth files (skip if all 5
#      version sites already equal the target — marketplace.json has
#      two).
#   2. Commit the bumps (skip if HEAD already is the release commit).
#   3. Create the annotated tag locally (skip if tag exists at HEAD;
#      hard-error if it exists but points elsewhere).
#   4. Push commit + tag (git push no-ops if already up to date; tag
#      push errors only if the remote tag points at a different SHA).
#
# The Release workflow at .github/workflows/release.yml then verifies
# the tag-vs-files match and creates the GitHub release.

set -euo pipefail

# ── Args + repo root ──────────────────────────────────────────────────

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <version>  (e.g. 0.3.4)" >&2
  exit 2
fi

VERSION="$1"
if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "error: version must be MAJOR.MINOR.PATCH (got: $VERSION)" >&2
  exit 2
fi

TAG="pcxa--v${VERSION}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# ── Helpers ───────────────────────────────────────────────────────────

# Read the current version recorded in each of the 5 sites.
read_versions() {
  PYPROJ_VER="$(grep -E '^version = ' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/')"
  INIT_VER="$(grep -E '^__version__' pcxa/__init__.py | sed -E 's/.*"([^"]+)".*/\1/')"
  PLUGIN_VER="$(jq -r '.version' .claude-plugin/plugin.json)"
  MARKET_TOP_VER="$(jq -r '.version' .claude-plugin/marketplace.json)"
  MARKET_PLUG_VER="$(jq -r '.plugins[0].version' .claude-plugin/marketplace.json)"
}

all_versions_match_target() {
  read_versions
  [ "$PYPROJ_VER" = "$VERSION" ] \
    && [ "$INIT_VER" = "$VERSION" ] \
    && [ "$PLUGIN_VER" = "$VERSION" ] \
    && [ "$MARKET_TOP_VER" = "$VERSION" ] \
    && [ "$MARKET_PLUG_VER" = "$VERSION" ]
}

bump_files() {
  sed -i -E "s/^version = \"[^\"]+\"/version = \"${VERSION}\"/"          pyproject.toml
  sed -i -E "s/^__version__ = \"[^\"]+\"/__version__ = \"${VERSION}\"/" pcxa/__init__.py
  jq --indent 2 --arg v "$VERSION" '.version = $v' \
    .claude-plugin/plugin.json > .claude-plugin/plugin.json.tmp \
    && mv .claude-plugin/plugin.json.tmp .claude-plugin/plugin.json
  jq --indent 2 --arg v "$VERSION" '.version = $v | .plugins[0].version = $v' \
    .claude-plugin/marketplace.json > .claude-plugin/marketplace.json.tmp \
    && mv .claude-plugin/marketplace.json.tmp .claude-plugin/marketplace.json
}

# ── Pre-flight ────────────────────────────────────────────────────────

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "error: must be on main (currently on $BRANCH)" >&2
  exit 1
fi

git fetch --tags origin main >/dev/null

# Allow a clean tree, OR a dirty tree where the only changes are the
# version files at the target version (a previous bump that didn't
# commit).
DIRTY_FILES="$(git status --porcelain | awk '{print $2}')"
if [ -n "$DIRTY_FILES" ]; then
  EXPECTED_DIRTY=$(printf '%s\n' \
    "pyproject.toml" \
    "pcxa/__init__.py" \
    ".claude-plugin/plugin.json" \
    ".claude-plugin/marketplace.json" | sort)
  ACTUAL_DIRTY=$(printf '%s\n' $DIRTY_FILES | sort -u)
  if ! diff <(echo "$EXPECTED_DIRTY") <(echo "$ACTUAL_DIRTY" | grep -Fxf <(echo "$EXPECTED_DIRTY")) >/dev/null \
     || ! all_versions_match_target; then
    UNEXPECTED=$(echo "$ACTUAL_DIRTY" | grep -vFxf <(echo "$EXPECTED_DIRTY") || true)
    if [ -n "$UNEXPECTED" ]; then
      echo "error: unrelated uncommitted changes:" >&2
      echo "$UNEXPECTED" >&2
      echo "commit or stash them first" >&2
      exit 1
    fi
  fi
fi

# ── Phase 1: bump ─────────────────────────────────────────────────────

if all_versions_match_target; then
  echo "✓ phase 1 (bump): already at $VERSION"
else
  read_versions
  CURR="$PYPROJ_VER"
  echo "→ phase 1 (bump): $CURR → $VERSION"
  bump_files
  if ! all_versions_match_target; then
    echo "error: bump did not land on all 5 sites" >&2
    read_versions
    echo "  pyproject.toml=$PYPROJ_VER init=$INIT_VER plugin=$PLUGIN_VER marketplace.top=$MARKET_TOP_VER marketplace.plugin=$MARKET_PLUG_VER" >&2
    exit 1
  fi
  echo "  bumped"
fi

# ── Phase 2: commit ───────────────────────────────────────────────────

EXPECTED_MSG="chore(release): v${VERSION}"
HEAD_MSG="$(git log -1 --format=%s HEAD 2>/dev/null || echo '')"

if [ -z "$(git status --porcelain)" ]; then
  if [ "$HEAD_MSG" = "$EXPECTED_MSG" ]; then
    echo "✓ phase 2 (commit): already committed (HEAD = $EXPECTED_MSG)"
  else
    echo "✓ phase 2 (commit): nothing to commit (version files match target, no diff)"
  fi
else
  echo "→ phase 2 (commit): committing bump"
  git add pyproject.toml pcxa/__init__.py .claude-plugin/plugin.json .claude-plugin/marketplace.json
  git commit -m "$EXPECTED_MSG"
fi

# ── Phase 3: tag ──────────────────────────────────────────────────────

HEAD_SHA="$(git rev-parse HEAD)"
if git rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
  TAG_SHA="$(git rev-parse "refs/tags/$TAG^{commit}")"
  if [ "$TAG_SHA" = "$HEAD_SHA" ]; then
    echo "✓ phase 3 (tag): $TAG already at HEAD"
  else
    echo "error: local tag $TAG points at $TAG_SHA, but HEAD is $HEAD_SHA" >&2
    echo "  this means a previous release attempt tagged a different commit." >&2
    echo "  resolve manually: git tag -d $TAG  (then re-run)" >&2
    exit 1
  fi
else
  echo "→ phase 3 (tag): creating $TAG at HEAD"
  git tag -a "$TAG" -m "pcxa v${VERSION}"
fi

# ── Phase 4: push ─────────────────────────────────────────────────────
# `git push` is naturally idempotent on already-pushed refs.

REMOTE_HEAD="$(git ls-remote origin refs/heads/main | awk '{print $1}')"
if [ "$REMOTE_HEAD" = "$HEAD_SHA" ]; then
  echo "✓ phase 4a (push main): already at $HEAD_SHA"
else
  echo "→ phase 4a (push main)"
  git push origin main
fi

REMOTE_TAG="$(git ls-remote origin "refs/tags/$TAG" | awk '{print $1}')"
if [ -n "$REMOTE_TAG" ]; then
  REMOTE_TAG_COMMIT="$(git ls-remote origin "refs/tags/$TAG^{}" | awk '{print $1}')"
  REMOTE_TAG_RESOLVED="${REMOTE_TAG_COMMIT:-$REMOTE_TAG}"
  if [ "$REMOTE_TAG_RESOLVED" = "$HEAD_SHA" ]; then
    echo "✓ phase 4b (push tag): $TAG already on origin at HEAD"
  else
    echo "error: remote tag $TAG points at $REMOTE_TAG_RESOLVED, but HEAD is $HEAD_SHA" >&2
    echo "  someone else may have already tagged this version differently." >&2
    exit 1
  fi
else
  echo "→ phase 4b (push tag): $TAG"
  git push origin "$TAG"
fi

echo
echo "Release $TAG complete."
echo "Watch the publishing workflow:"
echo "  gh run watch -R PCX-Analytics/pcxa-skill"
