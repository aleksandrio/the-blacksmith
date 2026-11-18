#!/usr/bin/env bash
# Install blacksmith for Claude Code. Each part is opt-in; run with no flags first.
#
#   ./install.sh               link this folder as the skill ~/.claude/skills/blacksmith (nothing else)
#   ./install.sh --guard       add the PreToolUse guard hook; offers the starter rules if you have none
#   ./install.sh --reminder    add a SessionStart hook that mentions an unread weekly report (max 3 times)
#   ./install.sh --schedule    run /blacksmith every Monday 09:00 (launchd on macOS; prints a cron line elsewhere)
#   ./install.sh --uninstall   remove the link, the hooks and the schedule; keeps $BLACKSMITH_HOME (reports, rules)
#
# Every change to ~/.claude/settings.json is backed up to $BLACKSMITH_HOME/backups/ first.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
SKILL="$HOME/.claude/skills/blacksmith"
BS="${BLACKSMITH_HOME:-$HOME/.claude/blacksmith}"
SETTINGS="$HOME/.claude/settings.json"
LABEL="dev.blacksmith.weekly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$BS/logs" "$BS/backups" "$HOME/.claude/skills"

settings_edit() {   # $1 = add-guard | add-reminder | remove-all
  [ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
  cp "$SETTINGS" "$BS/backups/settings.json.$(date +%Y%m%d-%H%M%S)"
  python3 - "$1" "$SETTINGS" "$SKILL" <<'PY'
import json, sys
action, path, skill = sys.argv[1:4]
s = json.load(open(path))
hooks = s.setdefault("hooks", {})
def has(event):
    return any("blacksmith" in json.dumps(e) for e in hooks.get(event, []))
if action == "add-guard" and not has("PreToolUse"):
    hooks.setdefault("PreToolUse", []).append({
        "matcher": "Bash|Read|Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [{"type": "command", "command": f"python3 {skill}/scripts/guard.py", "timeout": 10}]})
    print("Added the PreToolUse guard hook.")
elif action == "add-reminder" and not has("SessionStart"):
    hooks.setdefault("SessionStart", []).append({
        "matcher": "startup",
        "hooks": [{"type": "command", "command": f"{skill}/scripts/session-start-hook.sh"}]})
    print("Added the SessionStart reminder hook.")
elif action == "remove-all":
    for event in list(hooks):
        hooks[event] = [e for e in hooks[event] if "blacksmith" not in json.dumps(e)]
        if not hooks[event]:
            del hooks[event]
    print("Removed blacksmith hooks.")
else:
    print("Already installed, nothing changed.")
json.dump(s, open(path, "w"), indent=2, ensure_ascii=False)
open(path, "a").write("\n")
PY
}

link_skill() {
  if [ -L "$SKILL" ] || [ ! -e "$SKILL" ]; then
    ln -sfn "$SRC" "$SKILL"
    echo "Skill linked: $SKILL -> $SRC"
  elif [ "$(cd "$SKILL" && pwd -P)" != "$(cd "$SRC" && pwd -P)" ]; then
    echo "$SKILL exists and is not this folder; move it away first." >&2
    exit 1
  fi
  chmod +x "$SRC"/scripts/*.py "$SRC"/scripts/*.sh
}

case "${1:-}" in
  "")
    link_skill
    echo "Done. Run /blacksmith in Claude Code. Optional: --guard, --reminder, --schedule."
    ;;
  --guard)
    link_skill
    if [ ! -f "$BS/guards.json" ]; then
      echo "You have no guard rules yet. Starter rules (all answer \"ask\", none blocks outright):"
      python3 - "$SRC/examples/starter-guards.json" <<'PY'
import json, sys
for r in json.load(open(sys.argv[1]))["rules"]:
    print("  %-20s %s" % (r["id"], r["reason"]))
PY
      read -r -p "Copy them to $BS/guards.json? [y/N] " yn
      case "$yn" in [yY]*) cp "$SRC/examples/starter-guards.json" "$BS/guards.json";; esac
    fi
    settings_edit add-guard
    BLACKSMITH_HOME="$BS" python3 "$SRC/scripts/temper.py" || true
    echo "Restart Claude Code (or open /hooks) so the hook loads."
    ;;
  --reminder)
    link_skill
    settings_edit add-reminder
    ;;
  --schedule)
    link_skill
    if [ "$(uname)" = "Darwin" ]; then
      cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>-lc</string><string>$SKILL/scripts/weekly.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$BS/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$BS/logs/launchd.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
EOF
      plutil -lint "$PLIST"
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$PLIST"
      echo "Scheduled: Mondays 09:00. Test by hand: $SKILL/scripts/weekly.sh"
    else
      echo "Add this line with 'crontab -e' (Mondays 09:00):"
      echo "0 9 * * 1 $SKILL/scripts/weekly.sh >> $BS/logs/cron.log 2>&1"
    fi
    ;;
  --uninstall)
    if [ "$(uname)" = "Darwin" ]; then
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
      rm -f "$PLIST"
    fi
    settings_edit remove-all
    [ -L "$SKILL" ] && rm "$SKILL"
    echo "Uninstalled. Reports, drafts and guard rules stay in $BS; remove that folder yourself if you want."
    echo "On Linux, also remove the blacksmith line from 'crontab -e'."
    ;;
  *)
    sed -n '2,9p' "$0"; exit 1
    ;;
esac
