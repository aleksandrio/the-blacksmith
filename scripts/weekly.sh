#!/usr/bin/env bash
# Weekly headless run of /blacksmith, plus a desktop notification on macOS.
# Meant to be started by a scheduler (a launchd agent on macOS, cron or a systemd timer on Linux).
# Manual run: <skill dir>/scripts/weekly.sh
#
# Environment:
#   BLACKSMITH_HOME      working dir for reports and logs (default: ~/.claude/blacksmith)
#   BLACKSMITH_WORKDIR   directory `claude` is started in (default: $HOME)
#   BLACKSMITH_OPEN_APP  macOS app that opens the report from the dialog, e.g. "Visual Studio Code"
#                        (default: the system's default text editor)
#
# Plain bash (works with the bash 3.2 that ships with macOS); no zsh-only syntax.
set -u

BS_HOME="${BLACKSMITH_HOME:-$HOME/.claude/blacksmith}"
WORKDIR="${BLACKSMITH_WORKDIR:-$HOME}"
LOG="$BS_HOME/logs/$(date +%Y-%m-%d).log"

log() { echo "$*" >> "$LOG" 2>/dev/null || echo "blacksmith: $*" >&2; }

if ! mkdir -p "$BS_HOME/reports" "$BS_HOME/logs"; then
  echo "blacksmith: cannot create $BS_HOME/reports and $BS_HOME/logs" >&2
  exit 1
fi

# No `set -e`: a failing command is written to the log instead of ending the run silently,
# and an unexpected exit (for example an unset variable under `set -u`) still leaves a last line.
FINISHED=0
trap 'rc=$?; log "=== $(date) error: line $LINENO exited $rc: $BASH_COMMAND"' ERR
trap 'rc=$?; [[ $FINISHED -eq 1 ]] || log "=== $(date) aborted (exit $rc)"' EXIT

# Schedulers start with a minimal PATH: add the usual places where `claude` lives
# (native installer, newest Node from nvm if present, Homebrew). Missing ones are harmless.
NODE_BIN="$(ls -d "${NVM_DIR:-$HOME/.nvm}"/versions/node/*/bin 2>/dev/null | sort -V | tail -1)"
export PATH="${NODE_BIN:+$NODE_BIN:}$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# Desktop notifications only on macOS with osascript; everywhere else the log is the only output.
IS_MAC=0
if [[ "$(uname -s)" == "Darwin" ]] && command -v osascript >/dev/null 2>&1; then IS_MAC=1; fi

notify() {  # notify <subtitle> <message>
  log "notice: $1: $2"
  [[ $IS_MAC -eq 1 ]] || return 0
  osascript - "$1" "$2" >/dev/null 2>&1 <<'AS' || true
on run argv
  display notification (item 2 of argv) with title "Blacksmith" subtitle (item 1 of argv) sound name "Glass"
end run
AS
}

mtime() {  # file modification time in epoch seconds (GNU stat, then BSD/macOS stat)
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"
}

if ! command -v claude >/dev/null; then
  echo "claude CLI not found in PATH=$PATH" >> "$LOG"
  notify "Error" "claude command not found, see $LOG"
  FINISHED=1
  exit 1
fi

if ! cd "$WORKDIR" 2>/dev/null; then
  log "cannot cd to $WORKDIR, starting in $HOME instead"
  cd "$HOME"
fi
echo "=== $(date) start" >> "$LOG"

# Runs on whatever Claude account the `claude` CLI is logged into (subscription or API key),
# so the run's usage counts against that account.
STATUS=0
claude -p "/blacksmith --days 7" \
  --allowedTools "Bash(python3 *),Bash(ls *),Bash(cat *),Bash(head *),Bash(mkdir *),Bash(cp *),Bash(touch *),Bash(mktemp *),Read,Write,Edit,Glob,Grep" \
  --max-turns 60 \
  >> "$LOG" 2>&1 || STATUS=$?
echo "=== $(date) exit $STATUS" >> "$LOG"

REPORT="$BS_HOME/reports/latest.md"
if [[ $STATUS -eq 0 && -f "$REPORT" && $(( $(date +%s) - $(mtime "$REPORT") )) -lt 7200 ]]; then
  SUMMARY="$(grep -m1 '^\*\*Summary:\*\*' "$REPORT" | sed 's/\*\*Summary:\*\* *//' | cut -c1-180)"
  SUMMARY="${SUMMARY:-New report ready.}"
  rm -f "$BS_HOME/.seen"   # a new report is unread; done here so the headless run needs no rm permission
  notify "New weekly report" "$SUMMARY"
  if [[ $IS_MAC -eq 1 ]]; then
    # A dialog with a button: the notification alone has no click action. It closes itself after 10 minutes.
    CHOICE="$(osascript - "$SUMMARY" 2>/dev/null <<'AS'
on run argv
  tell application "System Events" to activate
  try
    set r to display dialog (item 1 of argv) with title "Blacksmith — new weekly report" buttons {"Later", "Open report"} default button "Open report" giving up after 600
    if gave up of r then return "later"
    return button returned of r
  on error
    return "later"
  end try
end run
AS
)" || CHOICE="later"
    if [[ "$CHOICE" == "Open report" ]]; then
      if [[ -n "${BLACKSMITH_OPEN_APP:-}" ]]; then open -a "$BLACKSMITH_OPEN_APP" "$REPORT"; else open -t "$REPORT"; fi
      touch "$BS_HOME/.seen"   # opened from the dialog = read; the SessionStart hook will not remind
    fi
  fi
else
  notify "Analysis failed" "See $LOG"
fi

# Cleanup: logs older than 60 days.
find "$BS_HOME/logs" -name '*.log' -mtime +60 -delete 2>/dev/null || true
FINISHED=1
exit "$STATUS"
