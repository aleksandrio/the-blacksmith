#!/usr/bin/env python3
"""PreToolUse hook: checks each tool call against the rules in $BLACKSMITH_HOME/guards.json.

It only ever tightens. On a hit it answers "ask" or "deny" with the rule's reason; otherwise it prints
nothing and the call goes through your normal permission rules. It never answers "allow".
If guards.json is broken, it asks before Bash and file-writing tools until the file is fixed, so a
typo cannot switch the guard off without anyone noticing.
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib  # noqa: E402

HOME = os.path.expanduser(os.environ.get("BLACKSMITH_HOME", "~/.claude/blacksmith"))
GUARDS = os.path.join(HOME, "guards.json")


def answer(decision, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": decision,
                                             "permissionDecisionReason": reason}}))


def main():
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    try:
        rules = guardlib.load_rules(GUARDS)
        decision, hits = guardlib.decide(rules, tool_name, tool_input)
    except Exception as exc:  # a broken rule file must not turn the guard off silently
        if guardlib.GUARDED_ON_ERROR.fullmatch(tool_name):
            answer("ask", f"blacksmith guard: rules unreadable ({exc}). Fix guards.json or run temper.py.")
        return
    if decision:
        reason = " ".join(f"[{r['id']}] {r.get('reason', '').strip()}" for r in hits)
        answer(decision, f"blacksmith guard {reason}")
        log_hits(hits, tool_name, decision)


def log_hits(hits, tool_name, decision):
    """Record which rules fired, so the weekly run can prune rules that never do. No command text is kept."""
    try:
        with open(os.path.join(HOME, "guard-hits.jsonl"), "a", encoding="utf-8") as fh:
            ts = datetime.now().isoformat(timespec="seconds")
            for r in hits:
                fh.write(json.dumps({"ts": ts, "rule": r.get("id"), "tool": tool_name, "decision": decision}) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
