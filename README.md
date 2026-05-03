# pcxa — Claude Code plugin for the PCXA platform

A Claude Code plugin and skill that lets Claude drive the [PCXA](https://www.pcxa.app)
construction-intelligence platform: search and read project files, manage
activities and progress, fill out forms, work with resources and timesheets,
link entities, and chat with the project's AI assistant.

The repo is structured for both Claude Code plugin installation and direct CLI installation:

- `.claude-plugin/plugin.json` — plugin metadata for Claude Code.
- `skills/pcxa/SKILL.md` — skill instructions Claude Code reads.
- `bin/pcxa` — plugin executable wrapper.
- `pcxa.py` — Python CLI that talks to `api.pcxa.app`.

The CLI uses only the Python standard library.

## Install

### 1. Claude Code plugin

From a local checkout, validate and load the plugin with Claude Code:

```bash
git clone https://github.com/PCX-Analytics/pcxa-skill.git ~/pcxa-skill
claude plugin validate ~/pcxa-skill
claude --plugin-dir ~/pcxa-skill
```

Once the plugin is listed in a marketplace, install it from Claude Code with the marketplace name provided by the listing.

### 2. CLI on PATH

Use [pipx](https://pypa.github.io/pipx/) so the CLI lives in its own venv and
`pcxa` is callable everywhere:

```bash
pipx install git+https://github.com/PCX-Analytics/pcxa-skill.git
pcxa --version
```

After install, `pcxa update` self-upgrades from GitHub. The CLI also prints a
one-line notice to stderr (max once every 24 hours) when a newer release is
out. Disable with `PCXA_NO_UPDATE_CHECK=1` in your environment.

For local development:

```bash
git clone https://github.com/PCX-Analytics/pcxa-skill.git ~/pcxa-skill
pipx install -e ~/pcxa-skill        # edits to ~/pcxa-skill take effect immediately
```

## First-run auth

From a terminal (not from Claude), log in once:

```bash
pcxa login
```

This opens `pcxa.app` in your browser. Sign in normally (MFA, SSO supported);
the CLI captures the tokens via a local callback. No password is typed into the
terminal.

Fallback if browser login isn't available:

```bash
pcxa setup -u you@example.com
```

## Per-repo sessions (without per-repo secrets)

All credentials live in **one global file** at `~/.pcxa/credentials.json` —
never inside any repo. Each named profile holds the tokens for one PCXA account.

To make a repo always use a specific account, drop a `.pcxa` file at the repo
root with a `user` field:

```json
{ "company": 4, "project": 10, "user": "alice@example.com" }
```

`.pcxa` is committed (no secrets in it). The CLI matches `user` against profile
usernames in `~/.pcxa/credentials.json` to pick the right account for that repo.
This means different repos can transparently use different accounts without ever
writing tokens into a repo and without per-repo `.gitignore` entries.

`pcxa login` automatically pins the new account into the repo's `.pcxa` file
(if one exists and doesn't already pin a user), so you usually don't have to
edit it by hand.

`pcxa whoami` always prints the credentials path and the active repo pin so you
can see which session is in use.

Pre-0.3 installs that have credentials at `~/.file_explorer/config.json` or
`<repo>/.pcxa-credentials.json` are migrated to the new location automatically
on first run; the legacy files are left in place and can be deleted afterwards.

## Per-repo project pinning

Different repos can target different PCXA projects without colliding. Drop a
`.pcxa` file at the repo root:

```json
{ "company": 4, "project": 10 }
```

Or set it from the CLI:

```bash
pcxa set-project 10 --company 4 --local
```

Confirm with `pcxa whoami` — it shows `(from .pcxa)` when a repo-level config
is active.

## Using it directly

The CLI is useful on its own too. All commands print JSON by default; pass
`-f table` for a human-readable view, and `--dry-run` on writes.

```bash
pcxa whoami
pcxa files search "structural defects" --include histogram,facets
pcxa activities list --status in_progress -f table
pcxa progress add 123 --percent 50 --notes "Reviewed shop drawings"
```

Run `pcxa <command> --help` for the full option list.

## License

MIT — see [LICENSE](./LICENSE).
