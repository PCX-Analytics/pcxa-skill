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

## Per-repo sessions

Sessions are isolated per repository so different repos can sign in as different
PCXA accounts without clobbering each other. The CLI picks the credentials file
in this order:

1. `<dir-with-.pcxa>/.pcxa-credentials.json` — explicit project marker
2. `<git-root>/.pcxa-credentials.json` — automatic, when inside any git repo
3. `~/.file_explorer/config.json` — only when run outside any repo

So the moment you `pcxa login` from inside a git repo, that login lives in the
repo and won't affect any other repo. `pcxa whoami` always prints the resolved
credentials path so you can see which session is active.

Add `.pcxa-credentials.json` to your repo's `.gitignore` — it contains tokens.

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
pcxa files search "structural defects" --limit 10
pcxa activities list --status in_progress -f table
pcxa progress add 123 --percent 50 --notes "Reviewed shop drawings"
```

Run `pcxa <command> --help` for the full option list.

## License

MIT — see [LICENSE](./LICENSE).
