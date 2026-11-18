#!/usr/bin/env python3
"""Inventory of Claude Code add-ons (skills, plugins, memory, CLAUDE.md, hooks, MCP) with their
context cost and last use. Input for the "Cleanup" part of /blacksmith.

Usage sources:
  - transcripts ~/.claude/projects/**/*.jsonl (Claude Code keeps them ~30 days): Skill calls,
    slash commands, Agent, MCP tools, reads of memory files;
  - ~/.claude/history.jsonl (typed prompts, longer history): slash commands;
  - $BLACKSMITH_HOME/usage.json (default ~/.claude/blacksmith/usage.json): last_used/first_seen
    ledger, updated on every run, so it remembers usage longer than the transcripts do.

Usage: inventory.py [--grace 21] [--no-ledger]
Tokens are an estimate: characters / 4.
"""
import argparse, collections, glob, json, os, re, subprocess, sys, time
from datetime import datetime

HOME = os.path.expanduser("~")
CL = os.path.join(HOME, ".claude")
PROJ = os.path.join(CL, "projects")
BS_HOME = os.path.expanduser(os.environ.get("BLACKSMITH_HOME") or os.path.join(CL, "blacksmith"))
LEDGER = os.path.join(BS_HOME, "usage.json")
SELF = "blacksmith"  # the skill's own name: its sessions read memory and skills, which is not real use
NOW = time.time()
TODAY = datetime.now().strftime("%Y-%m-%d")


def tok(chars):
    return int(round(chars / 4))


def day(ts):
    """ISO string / epoch s / epoch ms -> YYYY-MM-DD."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts).strftime("%Y-%m-%d")
    return str(ts)[:10]


def days_ago(d):
    return int((NOW - datetime.strptime(d, "%Y-%m-%d").timestamp()) // 86400) if d else None


def tilde(p):
    return p.replace(HOME, "~")


def enc(path):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def birth(path):
    try:
        st = os.lstat(path)
        return day(getattr(st, "st_birthtime", st.st_mtime))
    except OSError:
        return None


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return {}, text
    fm, out = m.group(1), {}
    dm = re.search(r"^description:\s*(>-?|\|-?)?\s*(.*?)(?=^\S|\Z)", fm, re.M | re.S)
    out["description"] = re.sub(r"\s+", " ", dm.group(2)).strip().strip("\"'") if dm else ""
    out["dmi"] = bool(re.search(r"^disable-model-invocation:\s*true", fm, re.M))
    return out, text[m.end():]


_GIT = {}


def git_tracked(path):
    """True when the file/directory is tracked in a git repo (removing it changes the repo)."""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    if d not in _GIT:
        try:
            r = subprocess.run(["git", "-C", d, "ls-files", "--full-name", "."], capture_output=True, text=True, timeout=5)
            top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5).stdout.strip()
            _GIT[d] = {os.path.join(top, x) for x in r.stdout.splitlines()} if r.returncode == 0 and top else set()
        except Exception:
            _GIT[d] = set()
    rp = os.path.realpath(path)
    return any(f == rp or f.startswith(rp + "/") for f in _GIT[d])


def under(cwd, d):
    return cwd == d or cwd.startswith(d.rstrip("/") + "/")


# ---------------------------------------------------------------- usage

class Usage:
    def __init__(self):
        self.count = collections.Counter()      # key -> number of uses in the transcript window
        self.last = {}                          # key -> last day used
        self.cwd_last = {}                      # cwd -> last session day
        self.cwd_sessions = collections.Counter()
        self.session_days = []
        self.listing = (None, "")               # (ts, content of the latest skill_listing)
        self.deferred = (None, [])              # (ts, deferred tool names from the latest session)

    def hit(self, key, d, n=1):
        self.count[key] += n
        if d and (key not in self.last or d > self.last[key]):
            self.last[key] = d


def norm_cmd(name):
    name = name.strip()
    return name.lstrip("/").split()[0].lower() if name else ""


def scan_transcripts(u):
    for p in glob.glob(os.path.join(PROJ, "**", "*.jsonl"), recursive=True):
        recs = []
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        pass
        except OSError:
            continue
        # blacksmith's own sessions: they read memory and skills during analysis, which is not use.
        head = json.dumps([r.get("message") for r in recs[:40] if r.get("type") == "user"], ensure_ascii=False)
        if f"<command-name>/{SELF}" in head or f'"/{SELF}' in head:
            u.hit("skill:" + SELF, day(next((r.get("timestamp") for r in reversed(recs) if r.get("timestamp")), None)))
            continue
        cwd = None; last = None; is_sub = "/subagents/" in p
        for o in recs:
            ts = o.get("timestamp")
            d = day(ts) if ts else None
            if d:
                last = max(last or d, d)
            cwd = cwd or o.get("cwd")
            t = o.get("type")
            if t == "attachment" and not is_sub:
                a = o.get("attachment") or {}
                if a.get("type") == "skill_listing" and ts and (not u.listing[0] or ts > u.listing[0]):
                    u.listing = (ts, a.get("content") or "")
                if a.get("type") == "deferred_tools_delta" and ts and a.get("addedNames") and (not u.deferred[0] or ts > u.deferred[0]):
                    u.deferred = (ts, a["addedNames"])
            elif t == "user":
                c = (o.get("message") or {}).get("content")
                s = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                for x in re.findall(r"<command-name>/?([^<\s]+)</command-name>", s):
                    u.hit("skill:" + norm_cmd(x), d)
            elif t == "assistant":
                for b in (o.get("message") or {}).get("content") or []:
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    n = b.get("name", ""); inp = b.get("input") or {}
                    if n == "Skill":
                        u.hit("skill:" + norm_cmd(str(inp.get("skill", ""))), d)
                    elif n == "Agent" and ":" in str(inp.get("subagent_type", "")):
                        u.hit("skill:" + str(inp["subagent_type"]).lower(), d)
                    elif n.startswith("mcp__"):
                        u.hit("mcp:" + n.split("__")[1], d)
                    elif n == "Read":
                        m = re.search(r"/\.claude/projects/([^/]+)/memory/([^/]+\.md)$", str(inp.get("file_path", "")))
                        if m and m.group(2) != "MEMORY.md":
                            u.hit(f"memory:{m.group(1)}/{m.group(2)}", d)
        if cwd and not is_sub and last:
            u.cwd_sessions[cwd] += 1
            u.session_days.append(last)
            if cwd not in u.cwd_last or last > u.cwd_last[cwd]:
                u.cwd_last[cwd] = last


def scan_history(u):
    """Typed slash commands from ~/.claude/history.jsonl (longer horizon). Last-used only."""
    p = os.path.join(CL, "history.jsonl")
    first = None
    if not os.path.exists(p):
        return None
    for line in open(p, encoding="utf-8", errors="replace"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        d = day(o.get("timestamp"))
        if not d:
            continue
        first = min(first or d, d)
        disp = str(o.get("display", "")).strip()
        if disp.startswith("/") and len(disp) > 1:
            name = norm_cmd(disp)
            k = "skill:" + name
            if re.fullmatch(r"[a-z0-9][a-z0-9:_.-]*", name) and (k not in u.last or d > u.last[k]):
                u.last[k] = d
        proj = o.get("project")
        if proj and (proj not in u.cwd_last or d > u.cwd_last[proj]):
            u.cwd_last[proj] = d
    return first


# ---------------------------------------------------------------- inventory

def project_roots(u):
    roots = set()
    for cwd in list(u.cwd_last):
        p = cwd
        while p.startswith(HOME + "/") and p != HOME:
            roots.add(p)
            p = os.path.dirname(p)
    return sorted(r for r in roots if os.path.isdir(r))


def skills(settings, roots):
    overrides = settings.get("skillOverrides") or {}
    bases = [(os.path.join(CL, "skills"), "global")]
    bases += [(os.path.join(r, ".claude", "skills"), tilde(r)) for r in roots]
    by_name = {}
    for base, scope in bases:
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            md = os.path.join(path, "SKILL.md")
            if name.startswith(".") or not os.path.exists(md):
                continue
            fm, body = frontmatter(open(md, encoding="utf-8", errors="replace").read())
            if fm.get("dmi"):
                mode, cost = "command only", 0
            elif overrides.get(name) == "name-only":
                mode, cost = "name-only", len(name) + 3
            else:
                mode, cost = "full description", len(f"- {name}: {fm.get('description', '')}") + 1
            s = by_name.setdefault(name, {"key": "skill:" + name.lower(), "name": name, "scopes": [], "mode": mode,
                                          "start_tok": tok(cost), "body_tok": tok(len(body)), "born": birth(path),
                                          "link": tilde(os.path.realpath(path)) if os.path.islink(path) else None})
            s["scopes"].append(scope + (" (git)" if scope != "global" and git_tracked(path) else ""))
            s["born"] = min(s["born"] or "9", birth(path) or "9")
    return list(by_name.values())


def plugins(settings, listing):
    out = []
    for pid, on in (settings.get("enabledPlugins") or {}).items():
        name, _, market = pid.partition("@")
        parts = []
        for d in glob.glob(os.path.join(CL, "plugins", "cache", market, name, "*")):
            parts = sorted(x for x in os.listdir(d) if x in ("skills", "commands", "agents", "hooks", ".mcp.json"))
        start = sum(len(l) + 1 for l in listing.splitlines() if l.startswith(f"- {name}:"))
        out.append({"key": "plugin:" + name.lower(), "id": pid, "enabled": bool(on), "parts": parts, "start_tok": tok(start)})
    return out


def memories(u):
    known = {enc(c): c for c in u.cwd_last}
    dirs = []
    for mdir in sorted(glob.glob(os.path.join(PROJ, "*", "memory"))):
        encd = os.path.basename(os.path.dirname(mdir))
        proj = known.get(encd)
        idx = os.path.join(mdir, "MEMORY.md")
        lines = open(idx, encoding="utf-8").read().splitlines() if os.path.exists(idx) else []
        files = sorted(f for f in os.listdir(mdir) if f.endswith(".md") and f != "MEMORY.md")
        items = []
        for f in files:
            line = next((l for l in lines if f"({f})" in l), None)
            text = open(os.path.join(mdir, f), encoding="utf-8", errors="replace").read()
            items.append({"key": f"memory:{encd}/{f}", "file": f, "index_chars": len(line or ""), "in_index": bool(line),
                          "body_tok": tok(len(text)), "born": birth(os.path.join(mdir, f)),
                          "own": f"source: {SELF}" in text})
        orphans = [m.group(1) for l in lines for m in [re.search(r"\]\(([^)]+\.md)\)", l)] if m and m.group(1) not in files]
        dirs.append({"dir": encd, "path": mdir, "project": tilde(proj) if proj else "?" + encd, "items": items, "orphans": orphans,
                     "index_tok": tok(sum(len(l) + 1 for l in lines)),
                     "sessions": u.cwd_sessions.get(proj, 0) if proj else 0,
                     "last": u.cwd_last.get(proj) if proj else None})
    return dirs


def claude_mds(u, roots):
    cands = [os.path.join(CL, "CLAUDE.md")]
    for r in roots:
        cands += [os.path.join(r, "CLAUDE.md"), os.path.join(r, "CLAUDE.local.md"), os.path.join(r, ".claude", "CLAUDE.md")]
        cands += sorted(glob.glob(os.path.join(r, ".claude", "rules", "*.md")))
    out = []
    for p in dict.fromkeys(cands):
        if not os.path.isfile(p):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        scope = HOME if p.startswith(CL + "/") else re.sub(r"/\.claude(/rules)?$", "", os.path.dirname(p))
        out.append({"path": tilde(p), "lines": text.count("\n") + 1, "tok": tok(len(text)), "git": git_tracked(p),
                    "sections": len(re.findall(r"^#{1,3} .+$", text, re.M)),
                    "sessions": sum(n for c, n in u.cwd_sessions.items() if under(c, scope)),
                    "last": max((d for c, d in u.cwd_last.items() if under(c, scope)), default=None)})
    return out


# Home-relative path inside a hook command: $HOME/..., ${HOME}/..., ~/..., or a literal
# macOS or Linux home directory followed by a path.
HOME_PATH_RE = r"(?:\$HOME|\$\{HOME\}|~|/Users/[^/\s'\"]+|/home/[^/\s'\"]+)(/[^\s'\";|&]+)"


def hooks(settings):
    grouped = collections.OrderedDict()
    for ev, lst in (settings.get("hooks") or {}).items():
        for e in lst:
            for h in e.get("hooks", []):
                cmd = re.sub(r"\s+", " ", str(h.get("command", "")))
                grouped.setdefault(cmd, []).append(ev + (f"[{e['matcher']}]" if e.get("matcher") not in (None, "", "*") else ""))
    out = []
    for cmd, evs in grouped.items():
        notes = []
        for pth in re.findall(HOME_PATH_RE, cmd):
            full = HOME + pth
            if not os.path.exists(full):
                notes.append("MISSING FILE " + tilde(full))
        for var in sorted(set(re.findall(r"\$\{?([A-Z][A-Z0-9_]+)", cmd)) - {"HOME"}):
            notes.append(f"only works when ${var} is set (now: {'yes' if os.environ.get(var) else 'no'})")
        out.append({"cmd": cmd[:100], "events": evs, "notes": notes})
    return out


def mcp_servers(u):
    perm = []
    try:
        d = json.load(open(os.path.join(HOME, ".claude.json")))
        perm += [(n, "user") for n in (d.get("mcpServers") or {})]
        perm += [(n, "project " + tilde(pp)) for pp, pd in (d.get("projects") or {}).items() for n in (pd.get("mcpServers") or {})]
    except Exception:
        pass
    visible = collections.Counter(); chars = collections.Counter()
    for n in u.deferred[1]:
        if n.startswith("mcp__"):
            srv = n.split("__")[1]
            visible[srv] += 1; chars[srv] += len(n) + 1
    return perm, visible, chars


# ---------------------------------------------------------------- report

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grace", type=int, default=21, help="days of protection for new items")
    ap.add_argument("--no-ledger", action="store_true", help="do not write usage.json")
    a = ap.parse_args()

    try:
        settings = json.load(open(os.path.join(CL, "settings.json")))
    except FileNotFoundError:
        settings = {}
    u = Usage()
    scan_transcripts(u)
    hist_from = scan_history(u)
    try:
        led = json.load(open(LEDGER))
    except Exception:
        led = {"items": {}}
    roots = project_roots(u)
    sd = sorted(u.session_days)
    horizon = sd[len(sd) // 20] if sd else TODAY   # 5th percentile: robust against a few stray old files
    ledger_from = min((i["first_seen"] for i in led["items"].values()), default=TODAY)

    sk = skills(settings, roots)
    pl = plugins(settings, u.listing[1])
    for p in pl:  # a plugin counts as used when any of its skills/commands/agents ("name:...") was used
        pref = "skill:" + p["key"].split(":", 1)[1] + ":"
        u.count[p["key"]] = sum(v for k, v in list(u.count.items()) if k.startswith(pref))
        lk = [d for k, d in list(u.last.items()) if k.startswith(pref)]
        if lk:
            u.last[p["key"]] = max(lk)

    def usage(key):
        it = led["items"].setdefault(key, {"first_seen": TODAY})
        lu = u.last.get(key)
        if lu and (not it.get("last_used") or lu > it["last_used"]):
            it["last_used"] = lu
        return u.count.get(key, 0), it.get("last_used")

    def when(last):
        return f"{last} ({days_ago(last)} d)" if last else "none"

    def new(born):
        return born is not None and days_ago(born) < a.grace

    L = [f"# Add-on inventory — {TODAY}\n",
         f"Usage data horizon: transcripts since ~{horizon} ({len(sd)} sessions; skills, MCP, memory reads); "
         f"slash commands typed by hand since {hist_from} (history.jsonl); usage.json ledger since {ledger_from}. "
         f"\"none\" = no use within this horizon. Tokens ≈ chars/4. \"start\" = tokens added to every session in scope "
         f"before you do anything. NEW = younger than {a.grace} days, do not propose removing it.\n"]

    # --- skills
    full = sorted([s for s in sk if s["mode"] == "full description"], key=lambda s: -s["start_tok"])
    L += ["## Skills with full description at start", "| skill | where | start | body | uses | last used | created | notes |", "|---|---|---|---|---|---|---|---|"]
    glob_tot = 0
    for s in full:
        n, last = usage(s["key"])
        if "global" in s["scopes"]:
            glob_tot += s["start_tok"]
        notes = (["symlink → " + s["link"]] if s["link"] else []) + (["NEW"] if new(s["born"]) else [])
        L.append(f"| {s['name']} | {', '.join(s['scopes'])} | {s['start_tok']} | {s['body_tok']} | {n} | {when(last)} | {s['born']} | {'; '.join(notes)} |")
    L.append(f"\nGlobal skills with full description: ≈{glob_tot} tokens in every session.\n")
    rest = sorted([s for s in sk if s["mode"] != "full description"], key=lambda s: s["name"])
    L.append("## Skills name-only / command only (≈3–6 tokens each)")
    L.append("Format: name (uses in window; last used; copies). Copies outside `global` live in projects, \"(git)\" = tracked in a repo.")
    parts, scopes = [], collections.Counter()
    for s in rest:
        n, last = usage(s["key"])
        for x in s["scopes"]:
            if x != "global":
                scopes[x] += 1
        parts.append(f"{s['name']} ({n}; {str(days_ago(last)) + ' d' if last else 'none'}; {len(s['scopes'])}×)")
    L.append(", ".join(parts))
    if scopes:
        L.append("Project copies: " + ", ".join(f"{k}: {v}" for k, v in scopes.most_common()))
    # --- plugins
    L += ["\n## Plugins", "| plugin | enabled | contains | start | uses | last used |", "|---|---|---|---|---|---|"]
    for p in pl:
        n, last = usage(p["key"])
        hooks_only = p["parts"] == ["hooks"]
        L.append(f"| {p['id']} | {'yes' if p['enabled'] else 'no'} | {', '.join(p['parts']) or '?'} | {p['start_tok']} | "
                 f"{'cannot be measured (hooks only)' if hooks_only else n} | {'—' if hooks_only else when(last)} |")

    # --- memory
    md = memories(u)
    L += ["\n## Memory (active projects)",
          "The MEMORY.md index loads in every session in EXACTLY this directory (\"start\" = the index line). A file's body loads only when it is read (Read).",
          "| project | file | start | body | reads | last read | created | notes |", "|---|---|---|---|---|---|---|---|"]
    dormant = []
    for d in md:
        if not d["items"] and not d["orphans"]:
            continue
        if not d["sessions"]:
            dormant.append(d)
            continue
        L.append(f"| **{d['project']}** — {d['sessions']} sessions, index ≈{d['index_tok']} tok | | | | | | | |")
        for m in d["items"]:
            n, last = usage(m["key"])
            notes = (["not in index"] if not m["in_index"] else []) + (["from blacksmith"] if m["own"] else []) \
                + ([f"line {m['index_chars']} chars"] if m["index_chars"] > 150 else []) + (["NEW"] if new(m["born"]) else [])
            L.append(f"| | {m['file']} | {tok(m['index_chars'])} | {m['body_tok']} | {n} | {when(last)} | {m['born']} | {'; '.join(notes)} |")
        for o in d["orphans"]:
            L.append(f"| | {o} | | | | | | INDEX LINE WITHOUT FILE |")
    if dormant:
        L.append("\nMemory in projects with no sessions in the horizon (cost 0 until you work there):")
        for d in dormant:
            for m in d["items"]:
                usage(m["key"])
            L.append(f"- {d['project']}: {len(d['items'])} files ({', '.join(m['file'] for m in d['items'])}), "
                     f"last session {d['last'] or '?'}, newest file {max((m['born'] or '?') for m in d['items']) if d['items'] else '—'}"
                     + (f", orphaned lines: {', '.join(d['orphans'])}" if d["orphans"] else ""))

    # --- CLAUDE.md
    cm = claude_mds(u, roots)
    act = sorted([c for c in cm if c["sessions"]], key=lambda c: -c["tok"])
    L += ["\n## CLAUDE.md and rules (active)", "Loaded in every session in their directory and its subdirectories; the global one always. \"(git)\" = file in a repo.",
          "| file | lines | start | headings | sessions in scope | last session |", "|---|---|---|---|---|---|"]
    for c in act:
        L.append(f"| {c['path']}{' (git)' if c['git'] else ''} | {c['lines']} | {c['tok']} | {c['sections']} | {c['sessions']} | {c['last']} |")
    idle = [c for c in cm if not c["sessions"]]
    if idle:
        L.append(f"No sessions in the horizon (cost 0, usually part of a repo — leave alone): {len(idle)} files, e.g. "
                 + ", ".join(c["path"] for c in idle[:5]))

    # --- hooks
    L.append("\n## Hooks (~/.claude/settings.json)")
    for h in hooks(settings):
        L.append(f"- {', '.join(h['events'])}: `{h['cmd']}`" + (" — " + "; ".join(h["notes"]) if h["notes"] else ""))

    # --- MCP
    perm, visible, chars = mcp_servers(u)
    L.append("\n## MCP")
    for n, scope in perm:
        c, last = usage("mcp:" + n)
        L.append(f"- registered permanently ({scope}): {n} — uses {c}, last used {when(last)}")
    if visible:
        unused = sorted((s for s in visible if not u.count.get("mcp:" + s)), key=lambda s: -chars[s])
        L.append(f"Visible in the latest session as deferred tools: {len(visible)} servers, ≈{tok(sum(chars.values()))} tok of names at start.")
        L.append("Unused in the horizon: " + (", ".join(f"{s} ({visible[s]} tools, ≈{tok(chars[s])} tok)" for s in unused) or "none"))
        L.append("claude.ai connectors (`claude_ai_*`) are removed in the connector settings on claude.ai, not locally.")

    sys.stdout.write("\n".join(L) + "\n")
    if not a.no_ledger:
        led["updated"] = TODAY
        os.makedirs(BS_HOME, exist_ok=True)
        json.dump(led, open(LEDGER, "w"), indent=1, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    main()
