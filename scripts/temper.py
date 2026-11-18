#!/usr/bin/env python3
"""Test bench for guard rules. Run it after every change to guards.json; apply.py runs it on each draft.

  temper.py                         test every rule in $BLACKSMITH_HOME/guards.json
  temper.py --rule draft.json       test one draft rule (a single rule object) against the current file
  temper.py --try Bash "git push -f origin main"
  temper.py --try Edit .env         show which rules would hit one call, and the decision
  temper.py --stats [--days 28]     how often each rule fired (from guard-hits.jsonl), quiet rules first

Exit code 0 when every rule holds, 1 when one fails, 2 when the rule file cannot be read.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib  # noqa: E402

HOME = os.path.expanduser(os.environ.get("BLACKSMITH_HOME", "~/.claude/blacksmith"))
GUARDS = os.path.join(HOME, "guards.json")


def report(rule, problems):
    tests = rule.get("tests") or {}
    n = len(tests.get("hit") or []) + len(tests.get("miss") or [])
    status = "ok  " if not problems else "FAIL"
    print(f"{status} {rule.get('id', '?'):<28} {rule.get('decision', '?'):<5} {n:>2} cases")
    for p in problems:
        print(f"       - {p}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rule", help="one draft rule (JSON object) to test before it is applied")
    ap.add_argument("--try", dest="try_", nargs=2, metavar=("TOOL", "VALUE"),
                    help="a tool name and a command or path to check")
    ap.add_argument("--stats", action="store_true", help="show how often each rule fired")
    ap.add_argument("--days", type=int, default=28)
    a = ap.parse_args()
    try:
        rules = guardlib.load_rules(GUARDS)
    except ValueError as exc:
        print(f"cannot read rules: {exc}")
        return 2

    if a.stats:
        since = (datetime.now() - timedelta(days=a.days)).isoformat(timespec="seconds")
        counts, last = {r.get("id"): 0 for r in rules}, {}
        try:
            for line in open(os.path.join(HOME, "guard-hits.jsonl"), encoding="utf-8"):
                e = json.loads(line)
                if e.get("rule") in counts and e.get("ts", "") >= since:
                    counts[e["rule"]] += 1
                    last[e["rule"]] = e["ts"][:10]
        except FileNotFoundError:
            pass
        print(f"hits in the last {a.days} days (rules that never fired first):")
        for rid, n in sorted(counts.items(), key=lambda kv: kv[1]):
            src = next((r.get("source", "starter") for r in rules if r.get("id") == rid), "")
            print(f"  {n:>4}  {rid:<28} last {last.get(rid, '-'):<10} from {src}")
        return 0

    if a.try_:
        tool, value = a.try_
        decision, hits = guardlib.decide(rules, tool, guardlib.as_input(tool, value))
        print(f"{tool} {value!r} -> {decision or 'no decision (normal permissions apply)'}")
        for r in hits:
            print(f"  [{r['id']}] {r.get('reason', '')}")
        return 0

    if a.rule:
        rule = json.load(open(a.rule, encoding="utf-8"))
        problems = guardlib.temper(rule, rules)
        report(rule, problems)
        return 1 if problems else 0

    if not rules:
        print(f"no rules yet in {GUARDS}")
        return 0
    failed = 0
    for i, rule in enumerate(rules):
        problems = guardlib.temper(rule, rules[:i] + rules[i + 1:])
        failed += bool(problems)
        report(rule, problems)
    print(f"\n{len(rules) - failed} of {len(rules)} rules hold")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
