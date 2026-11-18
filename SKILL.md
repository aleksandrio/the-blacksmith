---
name: blacksmith
description: >
  Weekly forge for your Claude Code setup. Reads your recent sessions, forges what you keep repeating into
  memory, CLAUDE.md rules, skills and tested guard rules, and melts down what loads into every session but goes
  unused. Modes: /blacksmith [--days N], clean, show, seen, list, apply 1,3, undo YYYY-MM-DD, temper.
disable-model-invocation: true
---

# Blacksmith: forge what you repeat, temper it, melt down what you don't use

Once a week, look at what the user did in Claude Code sessions and:
1. **Forge.** Find things they do or say by hand again and again, and draft the lightest piece that removes the
   repetition: a memory, a CLAUDE.md rule, a skill, a slash command, or a guard rule.
2. **Temper.** Every guard rule ships with its own test cases and must pass `temper.py` before it can be applied.
3. **Melt down.** Find additions that cost context in every session but are not used, duplicate each other, or
   never fire, and propose removing, shortening or switching them to name-only.

Output: a short report, auto-saved memory (the only automatic change), and drafts the user applies by number.

Paths:
- Working folder: `$BLACKSMITH_HOME`, default `~/.claude/blacksmith/` (reports/, drafts/, backups/, logs/,
  changelog.jsonl, guards.json, guard-hits.jsonl, dismissed.md, keep.md, usage.json).
- Scripts: `~/.claude/skills/blacksmith/scripts/` (below: `apply.py`, `temper.py`, `digest.py`, `inventory.py`).
- `$SCRATCH` = this session's scratchpad, or `mktemp -d`.

## Modes

- `/blacksmith` or `/blacksmith --days N` (default 7): full run (procedure below), including the melt-down part.
- `/blacksmith clean`: only the melt-down part. Save the report as `reports/YYYY-MM-DD-clean.md`, drafts in the
  same `drafts/YYYY-MM-DD/` (append to an existing manifest, keep numbering). Do not overwrite `latest.md` if a full
  report exists for that day.
- `/blacksmith show`: do not analyse. Print `reports/latest.md`, then `apply.py list`, then
  `touch $BLACKSMITH_HOME/.seen`.
- `/blacksmith seen`: only `touch $BLACKSMITH_HOME/.seen` (the user read the report elsewhere).
- `/blacksmith list`: only `apply.py list [--date D]`.
- `/blacksmith apply 1,3` (or `all`, optionally `--date D`): run `apply.py apply 1,3`, show the result and what each
  applied file now says in 2-3 sentences. If a `create` collides with an existing file, a `delete` is refused (file in
  git) or a `guard` fails tempering, say so and ask; never use `--force` without an explicit yes. If an `edit`
  reports that its text is gone (file changed since the draft), offer a refreshed draft instead of forcing it.
- `/blacksmith undo D [--item N]`: `apply.py undo D [--item N]`, show what was undone.
- `/blacksmith temper`: `temper.py` (all rules) and `temper.py --stats`; explain any failure in plain words.

## Forge: analysis

1. Build the digest (the script skips blacksmith's own sessions and subagent sidechains) and read all of it:
   ```
   python3 ~/.claude/skills/blacksmith/scripts/digest.py --days 7 > "$SCRATCH/digest.md"
   ```
2. Read the last 3 reports in `reports/` (not `latest.md`) and `changelog.jsonl`, so you do not repeat what was
   already applied. A pattern reported before and still present → "reported for the 2nd time". Skip ideas listed in
   `dismissed.md`. Drafts from earlier weeks still in status `draft`: list them as "still waiting", do not redraft.
3. Check existing skills (`ls ~/.claude/skills`, `ls <project>/.claude/skills` for projects in the digest), the
   CLAUDE.md files of projects you write about, and `guards.json`. If an existing skill fits but was not used,
   suggest using it instead of a new one.
4. Look for patterns. A candidate must appear in **at least 2 sessions** or **3 times in one**:
   - the same or very similar prompts (same intent, other words);
   - repeated tool chains (e.g. always `git log` → `gh pr view` → summary);
   - multi-step procedures dictated step by step instead of one command;
   - repeated corrections of the same kind ("no, in English", "don't use X") → CLAUDE.md / memory;
   - **corrections about an action**: the user stopped a tool call, reverted what it did, or said "never do X
     without asking" / "always ask before X" → **guard**;
   - context explained again and again (project, conventions, accounts, URLs) → memory;
   - periodic tasks (reports, reviews) → skill + schedule.
5. For each pattern weigh frequency, manual cost and how stable the procedure is, and pick the lightest form that
   works: memory / CLAUDE.md / skill / hook / guard / an existing skill. Do not invent skills for one-off tasks.
   Zero proposals is better than forced ones.

### Forging a guard rule

A guard rule is for **actions** (tool calls), never for style or knowledge. Draft one only when the transcripts show
the user correcting the same kind of action in 2+ sessions, or saying once, explicitly, "never do X without asking".

- Write `drafts/D/N-slug/guard.json`: one rule object (format in the header of `scripts/guardlib.py`):
  `id`, `tools`, `pattern`, `decision`, `reason`, `tests: {hit: [...], miss: [...]}`.
- `decision` is `ask` by default. Use `deny` only when the user said "never" and the action cannot be undone.
- `hit` cases: the real commands or paths from the transcripts that the user stopped (replace secrets, hostnames
  and personal paths with neutral ones). `miss` cases: the near misses the user does want (push without
  `--force`, `.env.example`, a staging deploy). At least 2 of each.
- `reason`: one sentence the agent will read when it is stopped. Say what to do instead ("Ask the user first",
  "Use the staging environment").
- Before the draft goes into the manifest, run `python3 ~/.claude/skills/blacksmith/scripts/temper.py --rule <file>`.
  It fails on a missed case, on a hit near-miss, and on a pattern that also hits everyday commands (`git status`,
  `npm test`, `ls`...). Fix the pattern or drop the draft. Never ship a rule that fails.
- At most **2 guard drafts per week**. Guards are never applied automatically.
- In the report, describe the rule in plain words ("asks before any `git push` with `--force` or `-f`") and list
  its cases; do not make the user read the regex.

## Melt down (second half of the analysis; alone: `/blacksmith clean`)

6. Build the inventory and read all of it:
   ```
   python3 ~/.claude/skills/blacksmith/scripts/inventory.py > "$SCRATCH/inventory.md"
   python3 ~/.claude/skills/blacksmith/scripts/temper.py --stats --days 28
   ```
   The inventory counts the start-up cost (tokens added to every session) and the last use of each addition, and
   updates `usage.json`, which remembers usage longer than the ~30 days of transcripts Claude Code keeps.
   Also read `keep.md` (things to keep for good even without use; never propose them).
7. Pick candidates. Order by value: tokens saved × number of sessions they load in.
   Choose the weakest move that works: **name-only > shorten > delete**.
   - **Skill with a full description, 0 uses in the window, not NEW** → `settings`
     `{"skillOverrides": {"<name>": "name-only"}}` (still callable as `/name`, costs ~4 tokens at start). `delete`
     only when the skill is clearly dead: a duplicate, its project closed, replaced by something newer. A symlink is
     removed as a link (its target stays).
   - **Plugin enabled, 0 uses of its commands/skills, not NEW** → `settings` `{"enabledPlugins": {"<id>": false}}`.
     A plugin with hooks only (usage cannot be measured): just ask in the report whether it is still needed.
   - **Memory**: no reads alone is not enough (the index line is often what gets used). Delete when the memory
     duplicates a global CLAUDE.md, a skill or another memory, or describes a state that is out of date. Index line
     over 150 characters → `edit` in MEMORY.md, shortened to ≤120 characters keeping the key fact. Memory in projects
     with no sessions (cost 0): no proposal.
   - **CLAUDE.md** (global and active projects): sections that duplicate memory, a skill or another CLAUDE.md,
     stale status lines, long instructions better moved into a skill (progressive disclosure) → `edit`.
   - **Hooks**: a hook that depends on a variable that is not set, or points at a missing file → `settings` with a new
     list for that event: copy the other entries from `~/.claude/settings.json` **verbatim**, leave out only the one
     being removed. Hooks run on every tool call, so they cost time too.
   - **Guard rules**: a rule from blacksmith (has a `source` date) that has not fired in 8 weeks → propose turning it
     off (`edit` guards.json: `"enabled": false`) or removing it with `undo` of its week. A rule that fires many times a
     week on things the user then approves is too broad: propose a narrower pattern as a new `guard` draft. Starter
     rules (no `source`) are only reported, never proposed for removal.
   - **MCP servers kept for good** (`~/.claude.json`) and unused → only an instruction in the report:
     `claude mcp remove <name> -s user`. **claude.ai connectors** unused → list the biggest (tokens) and say they are
     switched off on claude.ai in the connector settings. If the previous report gave the same list, one line is enough.
   - **Files in a git repo** (marked "(git)"): no manifest item; one line in the report saying what to remove in a commit.
8. Protection and limits:
   - Anything marked NEW (younger than 21 days) cannot be proposed as "unused". A duplicate or a stale fact can
     always be reported.
   - Never touch: blacksmith itself, entries in `keep.md`, anything outside `$BLACKSMITH_ROOTS`
     (default `~/.claude:~/Projects`).
   - At most **5 melt-down items in the manifest per week**, largest saving first. The rest: one line under
     "Weaker signals". A proposal made twice and not applied → the third time only one line.
   - Every item in the report has evidence (uses in the window, last use, data since when) and the saving in tokens.

## What is applied automatically, what stays a draft

Create `$BLACKSMITH_HOME/drafts/YYYY-MM-DD/` and a `manifest.json` in it (format in the header of `apply.py`). Each
item in the report gets an `id` equal to its number in the report and a subfolder `N-slug/`.

**Automatically (no question): memory only.** For each finding whose right form is memory:
- write `drafts/D/N-slug/memory.md` in memory format (frontmatter `name`, `description`, `metadata.type` ∈
  user|feedback|project|reference; for feedback/project add **Why:** and **How to apply:** lines). Only facts
  visible in the transcripts, no guesses. If a fact is uncertain, write it as a question to confirm, not as a rule.
- apply it: `apply.py memory --date D --item N --project <cwd of the project where the pattern appeared> --file <file> --hook "Title — one line"`.
  A memory about many projects goes to the common parent folder of those projects.
- `apply.py` adds `source: blacksmith` and `blacksmith_week`, updates MEMORY.md and the changelog.
- Limit: **at most 3 memories per week**, index line **≤120 characters** (MEMORY.md loads into every session in
  that folder, so each line is a fixed context cost). If the topic already has a memory there, update that file
  instead of adding a new one.

**As a draft (waits for `/blacksmith apply`):** CLAUDE.md entries, new skills, guard rules, hooks, all melt-down items.
- CLAUDE.md → `kind: append`, `source: N-slug/claude-md.snippet.md`, `target: <project>/CLAUDE.md`. Snippet: a `##`
  heading + at most ~15 lines. If the project's CLAUDE.md is over 150 lines, propose a separate file
  (`target: <project>/.claude/rules/<topic>.md`, `kind: create`) and say so.
- Skill → `kind: create`, `source: N-slug/skill/` (folder with a complete SKILL.md and any scripts/),
  `target: ~/.claude/skills/<name>` or `<project>/.claude/skills/<name>`. The SKILL.md must be complete and ready to
  use, not a skeleton.
- Guard → `kind: guard`, `source: N-slug/guard.json` (tempered, see above). If the guard hook is not installed,
  say once in the report: `~/.claude/skills/blacksmith/install.sh --guard`.
- New hook (other than guards) → only a description in the report plus a ready snippet in `N-slug/`, no manifest item.
- Melt-down → items in the same manifest, numbered after the forge items (kinds described in the header of `apply.py`):
  ```json
  {"id": 6, "title": "some-skill → name-only", "kind": "settings", "patch": {"skillOverrides": {"some-skill": "name-only"}}, "status": "draft"}
  {"id": 7, "title": "delete memory X (duplicates ~/.claude/CLAUDE.md)", "kind": "delete", "target": "~/.claude/projects/<enc>/memory/x.md", "status": "draft"}
  {"id": 8, "title": "shorten 3 index lines", "kind": "edit", "target": "~/.claude/projects/<enc>/memory/MEMORY.md",
   "edits": [{"old": "<whole old line>", "new": "<new line>"}], "status": "draft"}
  ```
  `delete` of a memory file also removes its line from MEMORY.md. In `edit`, copy each `old` verbatim from the file
  (it must occur exactly once); `new` must not be empty.
- Never apply anything but (new) memory yourself. Never run `apply.py apply` during an analysis.

At the end: save the report to `reports/YYYY-MM-DD.md` and copy it to `reports/latest.md`.
Interactive: print the report and run `rm -f $BLACKSMITH_HOME/.seen`. Headless (`-p`): print only two lines,
Summary and Changes (`weekly.sh` clears `.seen` itself).

## Report format

```
# Blacksmith: YYYY-MM-DD (sessions DD.MM–DD.MM)

**Summary:** N things to forge (K strong), G guard rules, S to melt down (≈T tokens per session). One sentence on the most important one.
**Changes:** saved A memories, B drafts wait for `/blacksmith apply` (incl. G guards and S melt-down). Undo: `/blacksmith undo YYYY-MM-DD`.

## Applied automatically
- memory `name` (project X): one sentence on what it holds

## Ready to apply
- 1. `append` → ~/code/app/CLAUDE.md: one sentence
- 2. `guard` no-prod-deploy (ask): asks before `wrangler deploy` without `--env staging`; 2 hit / 2 miss cases, tempered
- 3. `create` → ~/.claude/skills/release-notes: one sentence
(Apply: `/blacksmith apply 1,3`. All: `/blacksmith apply all`.)

## Forge

### 1. <name>
- **Signal:** where and how often (S3, S7, S11: 4 sessions, ~15 prompts)
- **What you repeat:** 2-3 concrete sentences, with a shortened prompt quote
- **Form:** memory (saved) / CLAUDE.md draft / skill draft `/name` / guard draft / hook / use existing `/x`
- **Gain:** an estimate ("~10 prompts a week")

## Melt down
**If you apply everything:** ≈T tokens less in every session (global) + ≈P in projects X, Y.
- 6. `settings` some-skill → name-only: 0 uses since DD.MM (last DD.MM), −100 tok/session
- 7. `delete` memory `old-status`: duplicates ~/.claude/CLAUDE.md, −45 tok in ~/code
- 8. `edit` guards.json: `no-npm-publish` has not fired in 8 weeks → enabled: false
Do it yourself (outside the manifest): claude.ai connectors…; `claude mcp remove X -s user`; files in repos.
(Apply: `/blacksmith apply 6,7`. Undo one: `/blacksmith undo YYYY-MM-DD --item 6`.
Keep something for good: add its name to $BLACKSMITH_HOME/keep.md.)

## Weaker signals
- one line per pattern seen once or twice; drafts from earlier weeks still waiting; melt-down candidates over the limit of 5

## Stats
- sessions: N, prompts: M, projects: …; most used commands: …
- start-up: global skills ≈X tok, MCP connectors ≈Y tok, global CLAUDE.md ≈Z tok; guards: R rules, H hits this week

Dismiss for good: add the name to $BLACKSMITH_HOME/dismissed.md (forge ideas) or keep.md (melt-down).
```

## Rules

- Write the report in the language the user writes to you in. At most ~120 lines. Shorten prompt quotes.
- Never quote secrets, tokens, passwords or URLs with keys: not in the report, memory, drafts or guard test cases.
- Changes outside `$BLACKSMITH_HOME` only through `apply.py` (memory automatically, the rest after `apply`).
