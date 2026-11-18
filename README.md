# Blacksmith

A weekly forge for your Claude Code setup.

Once a week it reads your recent Claude Code sessions and:

- **forges** what you keep doing by hand into the lightest piece that removes the repetition: a memory, a CLAUDE.md
  rule, a skill, or a **guard rule** when you keep stopping the agent from doing the same thing;
- **tempers** every guard rule with its own test cases before it can be applied;
- **melts down** what loads into every session but is never used: skills, plugins, memory lines, CLAUDE.md sections,
  hooks, and guard rules that never fire.

You get a short report and numbered drafts. Nothing but new memory files is applied without you, and every change
can be undone by week.

## What a run looks like

```
/blacksmith                 # analyse the last 7 days, write the report and drafts
/blacksmith apply 2,6       # apply the drafts you want
/blacksmith undo 2026-11-16 --item 6
/blacksmith temper          # test all guard rules, show how often each fired
```

A guard draft from a real pattern ("you stopped `wrangler deploy` three times and said to ask first"):

```json
{"id": "no-prod-deploy", "tools": "Bash",
 "pattern": "\\bwrangler\\s+deploy\\b(?![^\\n]*--env\\s+(dev|staging))",
 "decision": "ask",
 "reason": "Deploys to production. Ask the user before every production deploy.",
 "tests": {"hit": ["npx wrangler deploy", "wrangler deploy --minify"],
           "miss": ["wrangler deploy --env staging", "wrangler dev"]}}
```

`apply` runs `temper.py` on it first and refuses a rule that misses a case, hits a near miss, or also hits everyday
commands such as `git status` or `npm test`.

## Install

Needs Claude Code, Python 3 (standard library only) and bash. The schedule uses launchd on macOS; on Linux the
installer prints a cron line instead.

```bash
git clone <this repo> ~/src/blacksmith && cd ~/src/blacksmith
./install.sh               # links the skill as ~/.claude/skills/blacksmith, nothing else
./install.sh --guard       # optional: PreToolUse guard hook, offers 9 starter rules (all "ask")
./install.sh --reminder    # optional: mention an unread report at session start (max 3 times)
./install.sh --schedule    # optional: run every Monday 09:00
./install.sh --uninstall   # removes link, hooks, schedule; keeps your reports and rules
```

Each step that edits `~/.claude/settings.json` backs it up first to `~/.claude/blacksmith/backups/`.

Settings by environment variable:

| Variable | Default | What it sets |
|---|---|---|
| `BLACKSMITH_HOME` | `~/.claude/blacksmith` | reports, drafts, backups, changelog, guards.json |
| `BLACKSMITH_ROOTS` | `~/.claude:~/Projects` | the only folders a `delete` draft may touch |
| `BLACKSMITH_WORKDIR` | `$HOME` | where the weekly headless run starts |

## Context cost

- The skill has `disable-model-invocation: true`, so it adds nothing to a session until you type `/blacksmith`.
  A run loads the skill file (about 4k tokens) plus the digest of your week.
- The guard hook adds no tokens. It costs one short Python start per tool call it matches, and its answer only
  reaches the model when a rule fires.
- The reminder hook adds one line, only when a report is unread, at most three times.

## Guard rules

`~/.claude/blacksmith/guards.json` holds the rules. The format is documented at the top of `scripts/guardlib.py`.

- A rule answers `ask` or `deny`. The guard never answers `allow`, so it can only make your normal permissions
  stricter, never looser.
- Bash commands are checked as a whole and split at `&&`, `||`, `;`, `|` and newlines, with `sudo`, `env` and
  `VAR=value` prefixes removed. File tools are checked by path.
- If `guards.json` cannot be read, the hook asks before every Bash and file-writing call until you fix it, so a
  typo cannot switch it off without you noticing.
- Each hit is logged to `guard-hits.jsonl` with rule id, tool and time, never the command text. The weekly run uses
  it to propose switching off rules that never fire and narrowing rules that fire too often.
- `scripts/temper.py --try Bash "git push -f"` shows which rules a single call would hit.

## Limits

- **A guard is a speed bump, not a sandbox.** It matches text. A command hidden in a script the agent writes and
  then runs, base64, or an unusual alias gets past it. For a hard boundary use Claude Code's sandbox and deny rules.
- It reads Claude Code transcripts only (`~/.claude/projects`), which Claude Code keeps for about 30 days. Other
  harnesses are not analysed.
- Token counts are estimates (characters / 4), not a tokenizer.
- The weekly run is a headless `claude -p` on whatever account your Claude Code is signed in to, so it uses that
  account's usage. Running `/blacksmith` sends a digest of your week's prompts and tool calls to the model, the
  same as any other Claude Code session. Secrets in transcripts are not filtered by the digest; the skill tells the
  model never to repeat them.
- The scheduled headless run may use, without asking: `python3`, `ls`, `cat`, `head`, `mkdir`, `cp`, `touch`,
  `mktemp`, and Read, Write, Edit, Glob, Grep. Read `scripts/weekly.sh` before you schedule it, and narrow the list if
  you want; the procedure only writes inside `$BLACKSMITH_HOME` and, through `apply.py memory`, new memory files.
- Pattern finding is done by the model, so a week can produce weak or no proposals. The report says so instead
  of forcing one.

## Files

```
SKILL.md                    the procedure the model follows
install.sh                  opt-in installer and uninstaller
scripts/digest.py           compresses recent sessions into a readable digest
scripts/inventory.py        start-up cost and last use of every skill, plugin, memory, CLAUDE.md, hook, MCP server
scripts/apply.py            applies and undoes drafts (append, create, memory, guard, delete, edit, settings)
scripts/guardlib.py         guard rule format, matching and tempering
scripts/guard.py            the PreToolUse hook
scripts/temper.py           test bench and hit statistics for guard rules
scripts/weekly.sh           headless weekly run with a desktop notification on macOS
scripts/session-start-hook.sh  reminder about an unread report
examples/starter-guards.json   9 starter rules with test cases
```

## License

MIT
