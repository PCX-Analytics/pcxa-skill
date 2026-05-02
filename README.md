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

Drop the skill into your Claude Code skills directory.

**User-level (available in every project):**

```bash
git clone https://github.com/pcurielatmxi/pcxa-skill.git ~/.claude/skills/pcxa
```

**Project-level (only in this repo):**

```bash
git clone https://github.com/pcurielatmxi/pcxa-skill.git .claude/skills/pcxa
```

Make sure `requests` is available on your Python:

```bash
pip install requests
```

That's it — Claude Code picks up the skill automatically. Inside Claude, type
`/pcxa` to invoke it, or just ask Claude to do something on your PCXA project
and it will reach for the skill.

## First-run auth

From a terminal (not from Claude), log in once:

```bash
python ~/.claude/skills/pcxa/pcxa.py login
```

This opens `pcxa.app` in your browser. Sign in normally (MFA, SSO supported);
the CLI captures the tokens via a local callback. No password is typed into the
terminal.

Fallback if browser login isn't available:

```bash
python ~/.claude/skills/pcxa/pcxa.py setup -u you@example.com
```

Tokens are cached at `~/.file_explorer/config.json` (global) or in a
`.pcxa-credentials.json` file next to a `.pcxa` project marker (per-repo).

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
