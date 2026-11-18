#!/usr/bin/env python3
"""Collect Claude Code session transcripts from the last N days and print a compact digest
(markdown) for spotting repeated work.

Usage: digest.py [--days 7] [--max-sessions 60]
"""
import argparse, collections, glob, json, os, re, sys, time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
SELF_CMD = "/blacksmith"  # sessions that ran blacksmith itself are left out of the digest
MAX_PROMPT = 220
MAX_PROMPTS_PER_SESSION = 40
MAX_TOOL_STEPS = 80
BUILTIN_CMDS = {"/clear", "/model", "/effort", "/mcp", "/resume", "/plugin", "/reload-plugins", "/reload-skills",
                "/config", "/compact", "/exit", "/help", "/login", "/logout", "/cost", "/status", "/fast", "/context"}


def short(s, n=MAX_PROMPT):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def human_text(msg):
    """Return the text of a human prompt, or None (for tool_result / system messages)."""
    c = msg.get("content")
    if isinstance(c, str):
        t = c.strip()
    elif isinstance(c, list):
        parts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
        t = " ".join(parts).strip()
    else:
        return None
    if not t or t.startswith("<"):  # system-reminder, command-output, etc.
        return None
    return t


def tool_digest(block):
    name = block.get("name", "?")
    inp = block.get("input") or {}
    if name == "Bash":
        cmd = str(inp.get("command", "")).strip().split("\n")[0]
        return f"Bash({short(cmd, 70)})"
    if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        p = str(inp.get("file_path", "")).replace(HOME, "~")
        return f"{name}({p})"
    if name in ("Grep", "Glob"):
        return f"{name}({short(str(inp.get('pattern', '')), 40)})"
    if name == "Skill":
        return f"Skill({inp.get('skill', '')})"
    if name == "Agent":
        return f"Agent({inp.get('subagent_type', '')}: {short(str(inp.get('description', '')), 50)})"
    if name.startswith("mcp__"):
        return name
    return name


def bash_head(cmd):
    """First 1-2 tokens of a bash command (e.g. 'git commit', 'npm test')."""
    cmd = cmd.strip().split("\n")[0]
    cmd = re.sub(r"^(cd\s+\S+\s*(&&|;)\s*)+", "", cmd)
    cmd = re.sub(r"^(\w+=\S*\s*;?\s*)+", "", cmd)  # skip VAR=... assignments
    toks = cmd.split()
    if not toks:
        return None
    sub = {"git", "npm", "pnpm", "yarn", "npx", "docker", "gh", "python3", "python", "uv", "cargo", "go", "make", "brew"}
    if toks[0] in sub and len(toks) > 1 and not toks[1].startswith("-"):
        return f"{toks[0]} {toks[1]}"
    return toks[0]


def parse_session(path, cutoff):
    cwd = None; first_ts = None; last_ts = None
    prompts = []; tools = []; skills = collections.Counter()
    bash_heads = collections.Counter(); files = collections.Counter()
    title = None; own_run = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            if t == "ai-title" and o.get("title"):
                title = o["title"]
            if o.get("isSidechain"):
                continue
            ts = o.get("timestamp")
            if ts:
                last_ts = ts
                first_ts = first_ts or ts
            cwd = cwd or o.get("cwd")
            if t == "user":
                c = o.get("message", {}).get("content")
                raw = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                for x in re.findall(r"<command-name>(/[^<\s]+)</command-name>", raw):
                    if x not in BUILTIN_CMDS:
                        skills[x] += 1
                    if x == SELF_CMD:
                        own_run = True
                txt = human_text(o.get("message", {}))
                if txt is None:
                    continue
                if o.get("origin", {}).get("kind") not in (None, "human") and o.get("promptSource") not in (None, "typed"):
                    continue
                if txt.startswith("/"):
                    skills[txt.split()[0]] += 1
                prompts.append(short(txt))
            elif t == "assistant":
                for b in o.get("message", {}).get("content", []):
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tools.append(tool_digest(b))
                        inp = b.get("input") or {}
                        if b.get("name") == "Bash":
                            h = bash_head(str(inp.get("command", "")))
                            if h:
                                bash_heads[h] += 1
                        if b.get("name") == "Skill":
                            skills["/" + str(inp.get("skill", ""))] += 1
                        fp = inp.get("file_path")
                        if fp:
                            files[os.path.splitext(fp)[1] or os.path.basename(fp)] += 1
    if not prompts:
        return None
    if last_ts and datetime.fromisoformat(last_ts.replace("Z", "+00:00")) < cutoff:
        return None
    # skip blacksmith's own sessions
    if own_run or (prompts and prompts[0].startswith(SELF_CMD)):
        return None
    # collapse consecutive repeats in the tool sequence
    seq = []
    for x in tools:
        if seq and seq[-1][0] == x:
            seq[-1][1] += 1
        else:
            seq.append([x, 1])
    seq = [f"{x}×{n}" if n > 1 else x for x, n in seq]
    return {
        "path": path, "cwd": (cwd or "?").replace(HOME, "~"), "title": title,
        "start": first_ts, "end": last_ts, "prompts": prompts, "tools": seq,
        "skills": skills, "bash_heads": bash_heads, "files": files,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-sessions", type=int, default=60)
    a = ap.parse_args()
    cutoff = datetime.fromtimestamp(time.time() - a.days * 86400, tz=timezone.utc)

    paths = [p for p in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
             if os.path.getmtime(p) >= cutoff.timestamp()]
    sessions = [s for s in (parse_session(p, cutoff) for p in paths) if s]
    sessions.sort(key=lambda s: s["start"] or "")
    sessions = sessions[-a.max_sessions:]

    all_heads = collections.Counter(); heads_sessions = collections.Counter()
    all_skills = collections.Counter(); all_files = collections.Counter()
    proj = collections.Counter()
    for s in sessions:
        all_heads.update(s["bash_heads"])
        for h in s["bash_heads"]:
            heads_sessions[h] += 1
        all_skills.update(s["skills"]); all_files.update(s["files"]); proj[s["cwd"]] += 1

    out = []
    out.append(f"# Claude Code session digest — last {a.days} days (up to {datetime.now():%Y-%m-%d})\n")
    out.append(f"Sessions with prompts: {len(sessions)}, total prompts: {sum(len(s['prompts']) for s in sessions)}\n")
    out.append("## Projects (session count)")
    out += [f"- {k}: {v}" for k, v in proj.most_common()]
    out.append("\n## Most frequent Bash commands (calls / sessions)")
    out += [f"- `{k}`: {v} / {heads_sessions[k]}" for k, v in all_heads.most_common(30)]
    out.append("\n## Skills / slash commands used")
    out += [f"- {k}: {v}" for k, v in all_skills.most_common()] or ["- (none)"]
    out.append("\n## File types edited/read")
    out += [f"- {k}: {v}" for k, v in all_files.most_common(15)]

    out.append("\n## Sessions\n")
    for i, s in enumerate(sessions, 1):
        d = (s["start"] or "")[:16].replace("T", " ")
        out.append(f"### S{i} · {d} · {s['cwd']}" + (f" · \"{s['title']}\"" if s["title"] else ""))
        out.append(f"Human prompts ({len(s['prompts'])}):")
        ps = s["prompts"]
        shown = ps[:MAX_PROMPTS_PER_SESSION]
        out += [f"  {j}. {p}" for j, p in enumerate(shown, 1)]
        if len(ps) > len(shown):
            out.append(f"  … (+{len(ps)-len(shown)} more)")
        tl = s["tools"]
        if tl:
            out.append(f"Tools ({len(tl)} steps): " + " → ".join(tl[:MAX_TOOL_STEPS])
                       + (" → …" if len(tl) > MAX_TOOL_STEPS else ""))
        out.append("")
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
