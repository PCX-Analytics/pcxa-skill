# pcxa — Claude Code skill for the PCXA platform

A Claude Code skill that lets Claude drive the [PCXA](https://www.pcxa.app)
construction-intelligence platform: search and read project files, manage
activities and progress, fill out forms, work with resources and timesheets,
link entities, and chat with the project's AI assistant.

The skill is two files:

- `SKILL.md` — manifest Claude Code reads to learn the commands.
- `pcxa.py` — a self-contained Python CLI that talks to `api.pcxa.app`.

It depends only on the `requests` library.

## Install

There are two install vectors — pick one or do both.

### 1. CLI on PATH (recommended)

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

### 2. Claude Code skill

For Claude Code to discover the skill, `SKILL.md` must live at
`~/.claude/skills/pcxa/SKILL.md`. The simplest pattern (especially if you
have multiple repos and want one source of truth) is a symlink to a single
git checkout:

```bash
git clone https://github.com/PCX-Analytics/pcxa-skill.git ~/pcxa-skill
ln -s ~/pcxa-skill ~/.claude/skills/pcxa
```

A `git pull` in `~/pcxa-skill` updates the skill everywhere it's symlinked.

If you want the skill scoped to a single project instead, symlink (or clone)
into that project's `.claude/skills/pcxa/`.

That's it — Claude Code picks up the skill automatically. Inside Claude, type
`/pcxa` to invoke it, or just ask Claude to do something on your PCXA project
and it will reach for the skill.

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
