"""Guard rules shared by guard.py (the PreToolUse hook), temper.py (the test bench) and apply.py.

A rule file (guards.json) looks like this:

  {"rules": [
    {"id": "no-force-push",
     "tools": "Bash",                      # regex on the tool name, full match
     "pattern": "\\bgit\\s+push\\b.*\\s(--force|-f)\\b",   # Python regex, re.search
     "flags": "",                          # optional: "i" for case-insensitive
     "decision": "ask",                    # "ask" or "deny"; a guard never allows
     "reason": "Force push rewrites shared history. Ask the user first.",
     "source": "2026-11-16 #3",            # where the rule came from (filled in by apply.py)
     "enabled": true,
     "tests": {"hit":  ["git push --force origin main", "npm test && git push -f"],
               "miss": ["git push origin main", "git fetch --force"]}}
  ]}

What a rule is matched against:
  Bash                              the command, as a whole and split into segments (&&, ||, ;, |, newlines),
                                    with leading sudo / env / VAR=value prefixes removed from each segment
  Edit, Write, MultiEdit, Read      file_path
  NotebookEdit                      notebook_path
  anything else                     tool_input serialised as JSON
"""
import json
import re

DECISIONS = ("ask", "deny")
FILE_FIELD = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "Read": "file_path",
              "NotebookEdit": "notebook_path"}
GUARDED_ON_ERROR = re.compile(r"Bash|Edit|Write|MultiEdit|NotebookEdit")

# Everyday actions a rule must leave alone. A rule that hits one of these is too broad to ship.
BENIGN = {
    "Bash": ["ls -la", "pwd", "git status", "git diff", "git log --oneline -5", "git add -A", "git commit -m 'wip'",
             "git push origin feature/x", "npm test", "npm run build", "cat README.md", "python3 script.py",
             "grep -rn TODO src", "mkdir -p build", "cd src && ls"],
    "file": ["src/index.ts", "README.md", "docs/notes.md", "app/main.py", "tests/test_app.py"],
}

_SPLIT = re.compile(r"&&|\|\||;|\||\n")
_PREFIX = re.compile(r"^(?:(?:sudo|env|command|exec|nohup|time)\s+(?:-\S+\s+)*|[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")


def load_rules(path):
    """Return the list of rules. A missing file means no rules. A broken file raises ValueError."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        raise ValueError(f"{path} has no \"rules\" list")
    return rules


def save_rules(path, rules):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"rules": rules}, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def candidates(tool_name, tool_input):
    """The strings a rule is searched in, for one tool call."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        out = [cmd]
        for seg in _SPLIT.split(cmd):
            seg = _PREFIX.sub("", seg.strip())
            if seg and seg not in out:
                out.append(seg)
        return out
    field = FILE_FIELD.get(tool_name)
    if field:
        return [str(tool_input.get(field, ""))]
    return [json.dumps(tool_input, ensure_ascii=False, sort_keys=True)]


def _compiled(rule):
    flags = re.IGNORECASE if "i" in str(rule.get("flags", "")) else 0
    return re.compile(rule["tools"]), re.compile(rule["pattern"], flags)


def rule_hits(rule, tool_name, tool_input):
    if rule.get("enabled", True) is False:
        return False
    tools, pattern = _compiled(rule)
    if not tools.fullmatch(tool_name):
        return False
    return any(pattern.search(c) for c in candidates(tool_name, tool_input))


def decide(rules, tool_name, tool_input):
    """Return (decision, [rules that hit]). decision is "deny", "ask" or None. Deny beats ask."""
    hits = [r for r in rules if rule_hits(r, tool_name, tool_input)]
    if any(r.get("decision") == "deny" for r in hits):
        return "deny", [r for r in hits if r.get("decision") == "deny"]
    if hits:
        return "ask", hits
    return None, []


def test_tool(rule):
    """The tool a rule's test strings are fed to: test_tool, else the first plain name in tools."""
    if rule.get("test_tool"):
        return rule["test_tool"]
    for name in re.split(r"[|()^$]", str(rule.get("tools", ""))):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
            return name
    return "Bash"


def as_input(tool_name, value):
    if tool_name == "Bash":
        return {"command": value}
    if tool_name in FILE_FIELD:
        return {FILE_FIELD[tool_name]: value}
    return value if isinstance(value, dict) else {"value": value}


def temper(rule, others=()):
    """Check one rule before it ships. Returns a list of problems; empty means it holds."""
    problems = []
    rid = rule.get("id")
    if not isinstance(rid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,48}", rid):
        problems.append("id must be a lowercase slug (a-z, 0-9, -), 2-49 characters")
    if rid in {o.get("id") for o in others}:
        problems.append(f"id {rid!r} is already used")
    if rule.get("decision") not in DECISIONS:
        problems.append("decision must be \"ask\" or \"deny\" (a guard never allows)")
    reason = str(rule.get("reason", "")).strip()
    if not reason or len(reason) > 300:
        problems.append("reason must say why, in 1-300 characters")
    try:
        _compiled(rule)
    except (KeyError, re.error, TypeError) as exc:
        problems.append(f"tools/pattern do not compile: {exc}")
        return problems
    if re.compile(rule["pattern"]).search(""):
        problems.append("pattern matches the empty string, so it matches everything")
    tests = rule.get("tests") or {}
    hit, miss = tests.get("hit") or [], tests.get("miss") or []
    if len(hit) < 2 or len(miss) < 2:
        problems.append("tests need at least 2 \"hit\" and 2 \"miss\" cases")
    tool = test_tool(rule)
    probe = dict(rule, enabled=True)
    for case in hit:
        if not rule_hits(probe, tool, as_input(tool, case)):
            problems.append(f"should hit but does not: {case!r}")
    for case in miss:
        if rule_hits(probe, tool, as_input(tool, case)):
            problems.append(f"should miss but hits: {case!r}")
    corpus = BENIGN["Bash"] if tool == "Bash" else BENIGN["file"] if tool in FILE_FIELD else []
    broad = [c for c in corpus if c not in hit and rule_hits(probe, tool, as_input(tool, c))]
    if broad:
        problems.append("too broad, it also hits everyday actions: " + ", ".join(repr(c) for c in broad[:3]))
    return problems
