---
name: pcxa
description: PCXA construction intelligence platform CLI. Search/read files, manage tags and folders, manage activities/steps/progress/dependencies, manage form templates/fields/submissions, manage resources/timesheets/cost-codes/budgets, manage entity links between objects, and chat with the project's AI assistant. Use when the user asks about project files, documents, tasks, activities, forms, resources, timesheets, entity links, project management, or wants to send messages to the AI chatbot.
argument-hint: <command> [options]
user-invocable: true
disable-model-invocation: false
---

# PCXA CLI

**Tool:** `pcxa` (or `python .claude/skills/pcxa/pcxa.py` if not pipx-installed)

All commands output JSON by default. Use `-f table` for human-readable. Use `--dry-run` on write operations. Run `pcxa <command> --help` for full options.

If `pcxa` is not on PATH, ask the user to install it once with:
```
pipx install git+https://github.com/PCX-Analytics/pcxa-skill.git
```
Then `pcxa update` self-updates from GitHub. The CLI prints a one-line notice to stderr (max once per 24h) when a newer release is available.

## Setup & Authentication

**Preferred — Browser login (no password needed):**

Tell the user to run this in their terminal:
```
python .claude/skills/pcxa/pcxa.py login
```
This opens `pcxa.app` in the browser. The user signs in normally (supports MFA, SSO), then the CLI captures the tokens automatically. No password is typed into the terminal.

**Fallback — Password-based login:**

```
python .claude/skills/pcxa/pcxa.py setup -u THEIR_EMAIL
```
Prompts for password securely. Use this if browser login is unavailable.

**Step 2 — Check auth and project (Claude does this):**
```bash
pcxa whoami        # shows the resolved credentials path on the "Creds:" line
```

**Sessions are per-repo by default.** Inside any git repo, credentials are stored at `<git-root>/.pcxa-credentials.json`, so logging into one repo does not affect any other. The global fallback (`~/.file_explorer/config.json`) is only used when running outside a git repo. If `whoami` shows the wrong account, check the `Creds:` path it prints — that's the file in use.

**Project is configured per-repo** via a `.pcxa` file in the repo root — different repos automatically use different projects without affecting each other.

```bash
# Set project for the current repo (writes .pcxa in CWD)
pcxa set-project 10 --company 4 --local

# Set global fallback (used when no .pcxa present)
pcxa set-project 4
```

`.pcxa` file format: `{ "company": 4, "project": 10 }`

If `whoami` shows "Project: not set" and no `.pcxa` exists, ask the user which project to use, then run `pcxa set-project ID --local`.

If a command fails with "Profile not found", tell the user to run `pcxa login` in their terminal.

## Project Metadata

```bash
pcxa project get                                              # view project details
pcxa project members                                          # list members (name → user ID)
pcxa project members --search "John"                          # search by name/username
pcxa project update --name "New Name"                         # update name
pcxa project update --description "..." --scope-statement "..." # set description & scope
pcxa project update --code "T-FAB1" --industry "Construction" # set code & industry
pcxa project update --start-date 2025-12-03 --end-date 2026-08-10
pcxa project update --rollup-method equal                     # equal|duration|cost|labor
pcxa project update --progress-input-method percentage        # status|percentage
```

Fields: `name`, `code` (max 20), `description`, `scope-statement`, `industry`, `life-cycle`, `start-date`, `end-date`, `progress-input-method`, `rollup-method`.

## File Search & Reading

```bash
pcxa files list --ext PDF --search "keyword" --limit 50
pcxa files list --tags "urgent,review" --tags-mode all   # AND: files with ALL tags
pcxa files search "natural language query" --limit 10        # semantic search
pcxa files content "exact phrase" --ext PDF                   # keyword in file text
pcxa files read FILE_ID --outline                             # section map
pcxa files read FILE_ID                                       # first 5 chunks
pcxa files read FILE_ID --start 5                             # next window
pcxa files info FILE_ID                                       # metadata + versions
pcxa files stats                                              # project-wide counts
pcxa files aggregate file_type                                # group by dimension
pcxa files recent --limit 30
pcxa files download FILE_ID                              # download to current dir
pcxa files download FILE_ID -o report.pdf                # custom output path
pcxa files upload /path/to/file.pdf --folder 5 --title "Report" --tags "final,2026"
pcxa files upload /path/to/dir/ --folder 5               # bulk upload all files in dir
pcxa files delete 123 124 --yes                          # mark for deletion (adds 'to_delete' tag)
pcxa files restore 123 124                               # remove 'to_delete' tag (undo)
pcxa files list --tags to_delete                         # list everything pending deletion
```

**Upload storage:** Small files are uploaded through the API. Larger files use a presigned upload flow handled by the CLI and API.

**Deletion convention:** `pcxa files delete <ids>` marks files for deletion by applying the `to_delete` tag. Use `pcxa files restore <ids>` to undo before cleanup runs. Without `--yes`, `delete` prompts for confirmation.

Search results include `url` fields — always show these to users for document links.

## Tags & Folders

```bash
pcxa tags list                                                # all tags with counts
pcxa tags add 1 2 3 --tags urgent,review                      # add (preserves existing)
pcxa tags remove 1 2 --tags draft                             # remove specific tags
pcxa tags set 1 2 --tags final,approved                       # replace all tags
pcxa folders tree --depth 2                                   # hierarchy
pcxa folders create "Contracts" --parent 5                    # new folder
pcxa folders rename 5 "Legal"                                 # rename
pcxa folders move 5 --parent 10                               # reparent
pcxa folders contents 5                                       # subfolders + files (paginated; --timeout for slow folders)
pcxa folders subfolders 5                                     # lightweight [{id,name}] list (fast on large folders)
pcxa folders delete 5                                         # delete + all contents
pcxa move 10 11 12 --folder 5                                 # bulk move files
pcxa categorize 10 11 --category "Submittal"                  # bulk set category
pcxa files update 10 --title "New" --tags a,b --folder 5      # single file update
```

## Activities

```bash
pcxa activities list --status in_progress --priority 3,4
pcxa activities list --search "foundation" --assignee 5 --sort -due_date
pcxa activities list --tags "structural,review" --tags-mode all  # AND mode
pcxa activities list --after 2026-03-01 --before 2026-03-31       # updated in date range
pcxa activities list --after last_month                          # relative dates supported
pcxa activities list --created-after 2026-01-01 --created-before 2026-03-31
pcxa activities list --assignee 5 --after 2026-03-01 --before 2026-03-31  # user's work in period
pcxa activities get 123                                       # detail + steps + deps
pcxa activities create --title "Review" --priority 3 --type 5 --assignees 1,2
pcxa activities update 123 --status completed --percent 100
pcxa activities delete 123 456                                # bulk delete
pcxa activities bulk-update 1 2 3 --status in_progress
pcxa activities types                                         # list templates
```

**Statuses:** `not_started`, `in_progress`, `completed` | **Priority:** 0=none, 1=low, 2=med, 3=high, 4=critical

**Descriptions support Markdown** (headings, tables, bold, lists). Keep descriptions focused on:
- **Scope/objective** — what the activity is and what it produces
- **Requestor** — who asked for it, with reference to source communication (e.g., `file:247764`)
- **Business justification** — why this work exists

Do NOT put in descriptions: processing details, scripts, output file lists, status updates, or progress notes. Those belong in **comments** (`pcxa comments add`) as dated narrative entries.

**Date filters:** `--after`/`--before` filter by last updated; `--created-after`/`--created-before` filter by creation date. Accepts `YYYY-MM-DD` or relative keywords: `today`, `last_7_days`, `this_month`, `last_quarter`, etc.

**Name resolution:** `--assignee` and `--owner` accept user IDs or names. Names are fuzzy-matched against project members:
- Exact/substring match → resolves automatically with confirmation message
- Multiple close matches → lists candidates with IDs for you to pick
- No match → suggests `pcxa project members` to list all

**Invoicing workflow:** Query a user's activity in a billing period by name — no `--status` filter needed since `--after`/`--before` captures any work (started, progressed, or completed):
```bash
pcxa activities list --assignee "John" --after 2026-03-01 --before 2026-03-31
```

## Steps (Subtasks)

When steps exist, activity progress auto-calculates from weighted step completion.

```bash
pcxa steps list 123                                           # list steps
pcxa steps create 123 --name "Draft review" --weight 40
pcxa steps update 123 45 --percent 100                        # mark step complete
pcxa steps delete 123 45                                      # weights rebalance
pcxa steps from-template 123                                  # create from type template
```

## Progress

```bash
pcxa progress list 123                                        # progress timeline
pcxa progress add 123 --percent 50 --notes "Halfway"
pcxa progress add 123 --percent 25 --date 2026-03-15          # backdate
pcxa progress delete 123 789                                  # manual entries only
```

## Comments

```bash
pcxa comments list 3712                                       # list comments on activity
pcxa comments add 3712 --content "[2026-03-16] Drafting initiated..."
pcxa comments delete 3712 2952                                # delete comment by ID
pcxa comments bulk 3712 --file comments.json                  # bulk add from JSON file
```

**Bulk JSON format** (`comments.json`):
```json
[
  {"content": "[2026-03-16] Drafting initiated per weekly report #12"},
  {"content": "[2026-03-23] Internal review completed, minor revisions needed"}
]
```

Supports both bare list and wrapper object with `"comments"` key.

## Dependencies (CPM)

Types: `FS` (finish-to-start), `SS`, `FF`, `SF`. Lag in days (negative = lead).

```bash
pcxa deps list --predecessor 10
pcxa deps create --predecessor 10 --successor 20 --type FS --lag 2
pcxa deps delete 456
```

## Gantt & WBS Tree

```bash
pcxa gantt --status in_progress
pcxa tree --max-depth 3
```

## Forms (Templates)

Form templates define reusable forms with typed fields. Submissions are instances of a form.

```bash
pcxa forms list --category Safety                     # list templates
pcxa forms list --scope project --search "inspection"
pcxa forms get 1                                      # detail + fields
pcxa forms create --name "Safety Inspection" --category Safety --code-prefix SI --code-padding 3
pcxa forms update 1 --description "Updated desc" --reviewers 3,5
pcxa forms delete 1
```

**Scope:** `project` (default) or `company`. **Code settings:** `code-prefix` (e.g. RFI), `code-scope` (project/company), `code-separator` (default: -), `code-padding` (3=001).

## Fields (Form Fields)

Fields define the structure of a form template. Field values are referenced by field ID in submissions.

```bash
pcxa fields list 1                                    # list fields for form 1
pcxa fields create 1 --label "Inspector" --type text --required --order 1
pcxa fields create 1 --label "Severity" --type select --options '{"choices":["Low","Med","High"]}'
pcxa fields create 1 --label "Date" --type date --required --order 2
pcxa fields update 1 5 --label "New Label" --required true
pcxa fields delete 1 5
```

**Field types:** `text`, `textarea`, `date`, `select`, `checkbox`, `number`, `email`, `phone`, `url`, `file`, `signature`, etc.

## Submissions (Form Submissions)

Submissions are instances of a form template, with field values keyed by field ID.

```bash
pcxa submissions list --form 1                        # list for a specific form
pcxa submissions list --status draft                  # filter by status
pcxa submissions get 1 42                             # detail (form_id submission_id)
pcxa submissions create 1 --code SI-001 --values '{"1":"John","2":"2026-03-24","3":"High"}'
pcxa submissions update 1 42 --values '{"3":"Critical"}' --tags urgent
pcxa submissions delete 1 42
```

**Statuses:** `draft`, `submitted`, `closed`. Values are JSON objects mapping field IDs to values.

## Resources

Resources represent people, equipment, consumables, or subcontractors assigned to activities.

```bash
pcxa resources list --type personnel --active true
pcxa resources get 1                                  # detail with rates
pcxa resources create --name "John Smith" --type personnel --user 5
pcxa resources create --name "Crane #3" --type equipment --unit hours --capacity 10
pcxa resources update 1 --name "John D. Smith" --active false
pcxa resources delete 1
```

**Types:** `personnel`, `equipment`, `consumable`, `subcontractor` | **Units:** `hours`, `days`, `each`, `lump_sum`

Only `personnel` resources can link to a user (`--user`). Use `--user 0` to unlink.

## Rates (Resource Rates)

Append-only rate history. Create new rates with new effective dates — no updates/deletes.

```bash
pcxa rates list 1                                     # rates for resource 1
pcxa rates create 1 --effective-date 2026-04-01 --standard-rate 75 --cost-rate 50 --bill-rate 100
pcxa rates create 1 --effective-date 2026-04-01 --standard-rate 75 --cost-rate 50 --bill-rate 100 --overtime-rate 112.50
```

## Assignments (Resource → Activity)

```bash
pcxa assignments list 123                             # list for activity 123
pcxa assignments create 123 --resource 5 --planned-units 40 --role "Lead Engineer"
pcxa assignments create 123 --resource 8 --planned-units 80 --curve front_loaded --driving
pcxa assignments update 123 45 --remaining 20 --at-completion 50
pcxa assignments delete 123 45
```

**Curves:** `uniform`, `front_loaded`, `back_loaded`, `bell`

## Cost Codes (Company-Scoped)

Hierarchical cost tracking codes shared across all projects.

```bash
pcxa cost-codes list --root-only
pcxa cost-codes get 1                                 # detail with children
pcxa cost-codes create --code "03.300" --name "Cast-in-Place Concrete"
pcxa cost-codes create --code "03.310" --name "Structural" --parent 1
pcxa cost-codes update 1 --active false
pcxa cost-codes delete 1
```

## Budgets (Cost Code Budgets)

```bash
pcxa budgets list
pcxa budgets create --cost-code 1 --amount 50000 --units 200
pcxa budgets update 1 --amount 75000
pcxa budgets delete 1
```

## Timesheets

Time/cost tracking per resource with approval workflow: `draft` → `submitted` → `approved` | `rejected` → `draft`.

```bash
pcxa timesheets list --status draft --resource 5
pcxa timesheets list --after 2026-03-01 --before 2026-03-31
pcxa timesheets get 1                                 # detail with all entries
pcxa timesheets create --resource 5 --period-start 2026-03-24 --period-end 2026-03-30 --period-type weekly
pcxa timesheets update 1 --period-end 2026-03-31
pcxa timesheets delete 1
pcxa timesheets submit 1                              # draft → submitted
pcxa timesheets approve 1                             # submitted → approved
pcxa timesheets reject 1 --reason "Missing entries"   # submitted → rejected
pcxa timesheets reopen 1                              # rejected → draft
```

**Period types:** `weekly`, `biweekly`, `monthly`. Only draft/rejected timesheets are editable.

## Time Entries (nested under timesheets)

```bash
pcxa entries list 1                                   # entries for timesheet 1
pcxa entries list 1 --date 2026-03-25 --activity 123
pcxa entries create 1 --activity 123 --date 2026-03-25 --hours 8
pcxa entries create 1 --activity 123 --date 2026-03-25 --hours 2 --type overtime --cost-code 5
pcxa entries update 1 42 --hours 6 --description "Half day"
pcxa entries delete 1 42
```

**Types:** `regular`, `overtime`, `double_time` | Hours: 0.01–24

## Cost Entries (nested under timesheets)

```bash
pcxa cost-entries list 1
pcxa cost-entries create 1 --resource 8 --activity 123 --date 2026-03-25 --quantity 5 --unit-cost 200
pcxa cost-entries update 1 42 --quantity 10
pcxa cost-entries delete 1 42
```

`total_cost` auto-computed: `quantity × unit_cost`.

## Time Period Locks

Lock date ranges to prevent edits.

```bash
pcxa locks list
pcxa locks create --period-start 2026-03-01 --period-end 2026-03-15 --reason "Month closed"
pcxa locks delete 1
```

## Links (Entity Links)

Connect any two objects (files, activities, photos, drawings) with contextual descriptions. Object references use `type:id` format.

**Types:** `file`, `activity`, `photo`, `drawing`, `source_document`, `project`

```bash
pcxa links list --source file:170106                  # links from a file
pcxa links list --target activity:3710                # links to an activity
pcxa links list --source file:170106 --target activity:3710  # specific pair
pcxa links create --source file:170106 --target file:170107 --type attachment
pcxa links create --source activity:3710 --target file:170106 --type deliverable
pcxa links create --source file:170129 --target file:170100 --type "supersedes" --description "Corrected analysis"
pcxa links delete 42
pcxa links bulk --file links.json                     # bulk create from JSON
```

**Bulk JSON format** (`links.json`):
```json
[
  {"source": "file:170106", "target": "file:170107", "type": "attachment"},
  {"source": "activity:3710", "target": "file:170106", "description": "RFI analysis deliverable"},
  {"source_type": "file", "source_id": 170129, "target_type": "file", "target_id": 170100, "type": "supersedes"}
]
```

The `--type` flag sets the description field (the backend doesn't enforce typed relationships). Use `--description` for longer context. Both shorthand (`"source": "file:123"`) and explicit (`"source_type": "file", "source_id": 123`) formats work in bulk JSON.

## AI Chat

Send messages to the project's AI assistant and read back its replies. Useful for ad-hoc probing and for letting an agent evaluate chatbot response quality. Project-scoped — uses the `.pcxa` company/project.

```bash
pcxa chat send "What is the status of the foundation work?"          # uses current conversation, waits for reply
pcxa chat send "..." --new --title "Eval run 1"                      # fresh conversation
pcxa chat send "..." --conversation 123                              # continue an existing thread
pcxa chat send "..." --research                                      # enable file-search tools (research_mode)
pcxa chat send "..." --model gemini-2.5-pro                          # override model (see chat models)
pcxa chat send "..." --no-wait                                       # fire-and-forget; returns agent_task_id
pcxa chat send "..." --timeout 300                                   # polling timeout (default 180s)

pcxa chat ls --search "RFI"                                          # list conversations
pcxa chat get [ID]                                                    # show full transcript (default: current)
pcxa chat get 123 --show-tools                                        # include tool calls
pcxa chat new --title "Probe"                                         # create empty conversation
pcxa chat delete 123                                                  # soft-delete (archive)
pcxa chat models                                                      # list available models
```

**Default JSON output** (from `chat send`):
```json
{
  "conversation_id": 42,
  "agent_task_id": 9001,
  "agent_task_status": "completed",
  "elapsed_seconds": 14.2,
  "timed_out": false,
  "user_message": {"id": 500, "content": "..."},
  "assistant_message": {
    "id": 501, "role": "assistant", "content": "...",
    "tool_steps": [...], "thinking_steps": [...], "action_cards": [...]
  }
}
```

**How it works:** `chat send` submits a message, waits for the platform's assistant task to finish, and returns the assistant response. Exit code 2 means the task failed. `--no-wait` skips polling and returns the task ID immediately.

## Reporting API errors

When a PCXa CLI command returns a non-2xx API response, surface the endpoint, status code, and response body to the user. Do not invent alternate data paths; ask the user how they want to proceed.

## Invocation

`/pcxa $ARGUMENTS` → `pcxa $ARGUMENTS` (if installed via pipx) or `python .claude/skills/pcxa/pcxa.py $ARGUMENTS`
