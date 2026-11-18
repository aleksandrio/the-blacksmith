#!/usr/bin/env python3
"""Apply and undo blacksmith drafts. Every change outside $BLACKSMITH_HOME goes through this tool,
so each one is listed in the changelog and can be undone.

  apply.py list   [--date D]                 show drafts and their status
  apply.py apply  1,3 [--date D] [--force]   apply drafts by id ("all" for every draft)
  apply.py memory --date D --item N --project ~/code/app --file draft.md --hook "Title — one line"
                                             write a memory file (the only automatic change)
  apply.py undo   D [--item N]               undo the changes of week D (all of them, or one draft)

Manifest: $BLACKSMITH_HOME/drafts/<D>/manifest.json
  {"date": D, "items": [{"id": 1, "title": "...", "kind": "append|create|memory|guard|delete|edit|settings",
                         "target": "~/code/app/CLAUDE.md", "source": "1-app/claude-md.snippet.md",
                         "status": "draft|applied|auto-applied|undone"}]}

Forging (new things):
  append   - append the source file to target (creates the file if missing); backup first
  create   - copy the source file or folder to target; refuses when target exists (unless --force)
  memory   - a memory file; target is derived from the project (see `memory`)
  guard    - one guard rule (source: N-slug/guard.json, a single rule object). It is tested with
             temper first and refused if any test fails or it is too broad. Added to guards.json;
             undo removes that rule by id. Rules only take effect once the guard hook is installed
             (install.sh --guard).

Pruning (always a draft, never automatic):
  delete   - move target (file, skill folder, symlink) to backups/<D>/<id>/; for a memory file also removes
             its line from MEMORY.md. Refuses for files tracked by git (unless --force) and outside the
             allowed roots ($BLACKSMITH_ROOTS, default ~/.claude:~/Projects).
  edit     - replace text in target: "edits": [{"old": "...", "new": "..."}]; each old must occur exactly
             once. new must not be empty (keep an anchor such as the next heading), because undo
             looks for new to put old back.
  settings - deep-merge into a JSON file (default ~/.claude/settings.json): "patch": {...}; null removes a key,
             lists are replaced whole. Undo restores only the keys it changed.
             E.g. {"skillOverrides": {"some-skill": "name-only"}}, {"enabledPlugins": {"x@y": false}}.
"""
import argparse, json, os, re, shutil, subprocess, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib  # noqa: E402

HOME = os.path.expanduser("~")
BS = os.path.expanduser(os.environ.get("BLACKSMITH_HOME", "~/.claude/blacksmith"))
DRAFTS = os.path.join(BS, "drafts")
BACKUPS = os.path.join(BS, "backups")
CHANGELOG = os.path.join(BS, "changelog.jsonl")
GUARDS = os.path.join(BS, "guards.json")
ROOTS = [os.path.expanduser(r).rstrip("/") + "/"
         for r in os.environ.get("BLACKSMITH_ROOTS", "~/.claude:~/Projects").split(":") if r.strip()]


def expand(p):
    return os.path.expanduser(p)


def latest_date():
    ds = sorted(d for d in os.listdir(DRAFTS) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)) if os.path.isdir(DRAFTS) else []
    if not ds:
        sys.exit("No drafts in " + DRAFTS)
    return ds[-1]


def load_manifest(date):
    p = os.path.join(DRAFTS, date, "manifest.json")
    if not os.path.exists(p):
        sys.exit(f"No manifest: {p}")
    return p, json.load(open(p))


def save_manifest(p, m):
    json.dump(m, open(p, "w"), indent=2, ensure_ascii=False)


def log(entry):
    entry["ts"] = datetime.now().isoformat(timespec="seconds")
    os.makedirs(BS, exist_ok=True)
    with open(CHANGELOG, "a") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def backup(target, date, item_id):
    if not os.path.exists(target):
        return None
    d = os.path.join(BACKUPS, date, str(item_id))
    os.makedirs(d, exist_ok=True)
    b = os.path.join(d, os.path.basename(target))
    if os.path.isdir(target):
        shutil.copytree(target, b, dirs_exist_ok=True)
    else:
        shutil.copy2(target, b)
    return b


def memory_dir(project_path):
    enc = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(expand(project_path)))
    return os.path.join(HOME, ".claude", "projects", enc, "memory")


def parse_name(md_text):
    m = re.search(r"^name:\s*(\S+)", md_text, re.M)
    return m.group(1) if m else None


def git_tracked(path):
    d = path if os.path.isdir(path) and not os.path.islink(path) else os.path.dirname(path)
    try:
        r = subprocess.run(["git", "-C", d, "ls-files", "--error-unmatch", path], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def guard_hook_installed():
    try:
        s = json.load(open(os.path.join(HOME, ".claude", "settings.json")))
    except Exception:
        return False
    return "guard.py" in json.dumps(s.get("hooks", {}).get("PreToolUse", []))


def apply_guard(date, item, src, force):
    try:
        rule = json.load(open(src, encoding="utf-8"))
        rules = guardlib.load_rules(GUARDS)
    except (OSError, ValueError) as exc:
        return f"[{item['id']}] cannot read the rule or guards.json: {exc}"
    others = [r for r in rules if not (force and r.get("id") == rule.get("id"))]
    problems = guardlib.temper(rule, others)
    if problems:
        return f"[{item['id']}] refused, the rule does not hold:\n" + "\n".join(f"       - {p}" for p in problems)
    rule.setdefault("source", f"{date} #{item['id']}")
    rule.setdefault("enabled", True)
    b = backup(GUARDS, date, item["id"])
    os.makedirs(BS, exist_ok=True)
    guardlib.save_rules(GUARDS, others + [rule])
    log({"week": date, "item": item["id"], "kind": "guard", "target": GUARDS, "rule_id": rule["id"], "safety_backup": b})
    item["status"] = "applied"; item["target"] = GUARDS
    note = "" if guard_hook_installed() else "  (the guard hook is not installed yet: run install.sh --guard)"
    return f"[{item['id']}] OK (guard {rule['id']}, {rule['decision']}) → {GUARDS}{note}"


def apply_delete(date, item, force):
    target = expand(item["target"]).rstrip("/")
    if not os.path.lexists(target):
        return f"[{item['id']}] {target} does not exist, skipping"
    if not target.startswith(tuple(ROOTS)) or target.startswith(BS):
        return f"[{item['id']}] refused: I only delete inside {', '.join(ROOTS)} (and never in the blacksmith folder)"
    if git_tracked(target) and not force:
        return f"[{item['id']}] {target} is tracked by git, remove it with a commit or use --force"
    d = os.path.join(BACKUPS, date, str(item["id"]))
    os.makedirs(d, exist_ok=True)
    b = os.path.join(d, os.path.basename(target))
    if os.path.lexists(b):
        shutil.rmtree(b) if os.path.isdir(b) and not os.path.islink(b) else os.remove(b)
    shutil.move(target, b)   # a symlink moves as a link; its target stays untouched
    entry = {"week": date, "item": item["id"], "kind": "delete", "target": target, "backup": b}
    idx = os.path.join(os.path.dirname(target), "MEMORY.md")
    if "/memory/" in target and target.endswith(".md") and os.path.exists(idx):
        lines = open(idx).read().splitlines(True)
        ref = f"({os.path.basename(target)})"
        removed = [[i, l] for i, l in enumerate(lines) if ref in l]
        open(idx, "w").writelines(l for l in lines if ref not in l)
        entry.update(index=idx, index_removed=removed)
    log(entry)
    item["status"] = "applied"
    return f"[{item['id']}] OK (delete) {target} → {b}" + (" + its line in MEMORY.md" if entry.get("index_removed") else "")


def apply_edit(date, item):
    target = expand(item["target"])
    if not os.path.isfile(target):
        return f"[{item['id']}] {target} does not exist"
    text = open(target, encoding="utf-8").read()
    for e in item.get("edits", []):
        if not e.get("new"):
            return f"[{item['id']}] empty new, keep an anchor (such as the next heading)"
        n = text.count(e["old"])
        if n != 1:
            return f"[{item['id']}] old text occurs {n} times (file changed?), nothing changed: {e['old'][:60]!r}"
        text = text.replace(e["old"], e["new"], 1)
    b = backup(target, date, item["id"])
    open(target, "w", encoding="utf-8").write(text)
    log({"week": date, "item": item["id"], "kind": "edit", "target": target, "safety_backup": b, "edits": item["edits"]})
    item["status"] = "applied"
    return f"[{item['id']}] OK (edit, {len(item['edits'])} changes) → {target}"


def apply_settings(date, item, src):
    target = expand(item.get("target") or "~/.claude/settings.json")
    patch = item.get("patch") or (json.load(open(src)) if src and os.path.exists(src) else None)
    if not isinstance(patch, dict):
        return f"[{item['id']}] no patch"
    s = json.load(open(target))
    changes = []

    def walk(dst, p, path):
        for k, v in p.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                walk(dst[k], v, path + [k]); continue
            changes.append({"path": path + [k], "had": k in dst, "old": dst.get(k)})
            if v is None:
                dst.pop(k, None)
            else:
                dst[k] = v
    walk(s, patch, [])
    b = backup(target, date, item["id"])
    json.dump(s, open(target, "w"), indent=2, ensure_ascii=False); open(target, "a").write("\n")
    log({"week": date, "item": item["id"], "kind": "settings", "target": target, "safety_backup": b, "changes": changes})
    item["status"] = "applied"
    return f"[{item['id']}] OK (settings) → {target}: " + ", ".join(".".join(c["path"]) for c in changes)


def do_apply_item(date, item, src_root, force):
    kind = item["kind"]
    src = os.path.join(src_root, item["source"]) if item.get("source") else None
    if kind == "delete":
        return apply_delete(date, item, force)
    if kind == "edit":
        return apply_edit(date, item)
    if kind == "settings":
        return apply_settings(date, item, src)
    if not src or not os.path.exists(src):
        return f"[{item['id']}] source file missing: {src}"
    if kind == "guard":
        return apply_guard(date, item, src, force)
    if kind == "memory":
        res = write_memory(date, item["id"], item["project"], src, item.get("hook", item.get("title", "")))
        if "→" in res:
            item["status"] = "applied"; item["target"] = res.split("→ ", 1)[1]
        return res
    target = expand(item["target"])
    if kind == "append":
        b = backup(target, date, item["id"])
        os.makedirs(os.path.dirname(target), exist_ok=True)
        content = open(src).read().rstrip("\n") + "\n"
        existed = os.path.exists(target)
        with open(target, "a") as fh:
            if existed and os.path.getsize(target) and not open(target).read().endswith("\n"):
                fh.write("\n")
            fh.write(("\n" if existed else "") + content)
        log({"week": date, "item": item["id"], "kind": kind, "target": target, "backup": b, "created": not existed})
    elif kind == "create":
        if os.path.exists(target) and not force:
            return f"[{item['id']}] {target} already exists, use --force to overwrite (a backup is made)"
        b = backup(target, date, item["id"])
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isdir(src):
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
        log({"week": date, "item": item["id"], "kind": kind, "target": target, "backup": b, "created": b is None})
    else:
        return f"[{item['id']}] unknown kind: {kind}"
    item["status"] = "applied"
    return f"[{item['id']}] OK ({kind}) → {target}"


def write_memory(date, item_id, project, src, hook):
    text = open(src).read()
    name = parse_name(text)
    if not name:
        return f"[{item_id}] memory file has no name: in its frontmatter"
    if "source: blacksmith" not in text:
        text = re.sub(r"^metadata:\n", f"metadata:\n  source: blacksmith\n  blacksmith_week: {date}\n", text, count=1, flags=re.M)
    mdir = memory_dir(project)
    os.makedirs(mdir, exist_ok=True)
    target = os.path.join(mdir, name + ".md")
    if os.path.exists(target):
        return f"[{item_id}] memory {target} already exists, skipping"
    open(target, "w").write(text)
    index = os.path.join(mdir, "MEMORY.md")
    line = f"- [{hook.split(' — ')[0] if ' — ' in hook else name}]({name}.md) — {hook.split(' — ',1)[1] if ' — ' in hook else hook}"
    with open(index, "a") as fh:
        if os.path.exists(index) and os.path.getsize(index) and not open(index).read().endswith("\n"):
            fh.write("\n")
        fh.write(line + "\n")
    log({"week": date, "item": item_id, "kind": "memory", "target": target, "index": index, "index_line": line})
    return f"[{item_id}] memory → {target}"


def cmd_list(a):
    date = a.date or latest_date()
    _, m = load_manifest(date)
    print(f"Drafts from {date}:")
    for it in m["items"]:
        tgt = it.get("target") or {"settings": "~/.claude/settings.json", "guard": GUARDS}.get(
            it["kind"], "memory of project " + it.get("project", "?"))
        print(f"  {it['id']:>2}. [{it.get('status','draft'):<12}] {it['kind']:<8} {it['title']}  → {tgt}")


def cmd_apply(a):
    date = a.date or latest_date()
    mp, m = load_manifest(date)
    ids = {int(x) for x in a.ids.split(",")} if a.ids != "all" else {it["id"] for it in m["items"]}
    src_root = os.path.join(DRAFTS, date)
    for it in m["items"]:
        if it["id"] not in ids:
            continue
        if it.get("status") in ("applied", "auto-applied"):
            print(f"[{it['id']}] already applied, skipping"); continue
        print(do_apply_item(date, it, src_root, a.force))
    save_manifest(mp, m)


def cmd_memory(a):
    mp, m = load_manifest(a.date)
    it = next((i for i in m["items"] if i["id"] == a.item), None)
    res = write_memory(a.date, a.item, a.project, expand(a.file), a.hook)
    print(res)
    if it is not None and res.endswith(".md") and "→" in res:
        it["status"] = "auto-applied"; it["kind"] = "memory"; it["target"] = res.split("→ ", 1)[1]
        save_manifest(mp, m)


def cmd_undo(a):
    entries = [json.loads(l) for l in open(CHANGELOG)] if os.path.exists(CHANGELOG) else []
    sel = [e for e in entries if e["week"] == a.date and (a.item is None or e["item"] == a.item) and not e.get("undone")]
    if not sel:
        print("Nothing to undo."); return
    for e in reversed(sel):
        t = e["target"]
        if e["kind"] == "delete":
            if os.path.lexists(t):
                print(f"[{e['item']}] {t} exists again, not overwriting; copy kept at {e['backup']}"); continue
            shutil.move(e["backup"], t)
            if e.get("index_removed") and os.path.exists(e["index"]):
                lines = open(e["index"]).read().splitlines(True)
                for i, l in e["index_removed"]:
                    if lines and not lines[-1].endswith("\n"):
                        lines[-1] += "\n"
                    lines.insert(min(i, len(lines)), l)
                open(e["index"], "w").writelines(lines)
        elif e["kind"] == "guard":
            try:
                rules = guardlib.load_rules(t)
            except ValueError as exc:
                print(f"[{e['item']}] cannot read {t} ({exc}); restore it by hand from {e.get('safety_backup')}"); continue
            kept = [r for r in rules if r.get("id") != e["rule_id"]]
            if len(kept) == len(rules):
                print(f"[{e['item']}] rule {e['rule_id']} is no longer in {t}, nothing to remove")
            guardlib.save_rules(t, kept)
        elif e["kind"] == "edit":
            text = open(t, encoding="utf-8").read()
            bad = [x for x in e["edits"] if text.count(x["new"]) != 1]
            if bad:
                print(f"[{e['item']}] file changed after the edit, restore it by hand from {e['safety_backup']}"); continue
            for x in reversed(e["edits"]):
                text = text.replace(x["new"], x["old"], 1)
            open(t, "w", encoding="utf-8").write(text)
        elif e["kind"] == "settings":
            s = json.load(open(t))
            for c in reversed(e["changes"]):
                d = s
                for k in c["path"][:-1]:
                    d = d.setdefault(k, {})
                if c["had"]:
                    d[c["path"][-1]] = c["old"]
                else:
                    d.pop(c["path"][-1], None)
            json.dump(s, open(t, "w"), indent=2, ensure_ascii=False); open(t, "a").write("\n")
        elif e["kind"] == "memory":
            if os.path.exists(t): os.remove(t)
            if os.path.exists(e["index"]):
                lines = [l for l in open(e["index"]) if l.rstrip("\n") != e["index_line"]]
                open(e["index"], "w").writelines(lines)
        elif e.get("backup"):
            if os.path.isdir(t): shutil.rmtree(t)
            elif os.path.exists(t): os.remove(t)
            if os.path.isdir(e["backup"]): shutil.copytree(e["backup"], t)
            else: shutil.copy2(e["backup"], t)
        elif e.get("created"):
            if os.path.isdir(t): shutil.rmtree(t)
            elif os.path.exists(t): os.remove(t)
        print(f"[{e['item']}] undone {e['kind']} {t}")
        e["undone"] = True
    with open(CHANGELOG, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    try:
        mp, m = load_manifest(a.date)
        for it in m["items"]:
            if a.item is None or it["id"] == a.item:
                if it.get("status") in ("applied", "auto-applied"): it["status"] = "undone"
        save_manifest(mp, m)
    except SystemExit:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--date"); p.set_defaults(f=cmd_list)
    p = sub.add_parser("apply"); p.add_argument("ids"); p.add_argument("--date"); p.add_argument("--force", action="store_true"); p.set_defaults(f=cmd_apply)
    p = sub.add_parser("memory"); p.add_argument("--date", required=True); p.add_argument("--item", type=int, required=True)
    p.add_argument("--project", required=True); p.add_argument("--file", required=True); p.add_argument("--hook", required=True); p.set_defaults(f=cmd_memory)
    p = sub.add_parser("undo"); p.add_argument("date"); p.add_argument("--item", type=int); p.set_defaults(f=cmd_undo)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
