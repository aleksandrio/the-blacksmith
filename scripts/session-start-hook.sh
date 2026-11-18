#!/usr/bin/env bash
# SessionStart hook: remind about an unread blacksmith report.
# Stops when: a .seen file newer than the report exists, the report is older than 14 days,
# or the reminder has already been shown 3 times.
# Environment: BLACKSMITH_HOME (default: ~/.claude/blacksmith). Plain bash, no zsh-only syntax.
BS_HOME="${BLACKSMITH_HOME:-$HOME/.claude/blacksmith}"
R="$BS_HOME/reports/latest.md"; S="$BS_HOME/.seen"; C="$BS_HOME/.reminded"
[[ -f "$R" ]] || exit 0
[[ -f "$S" && "$S" -nt "$R" ]] && exit 0
MTIME=$(stat -c %Y "$R" 2>/dev/null || stat -f %m "$R")   # GNU stat, then BSD/macOS stat
AGE=$(( ( $(date +%s) - MTIME ) / 86400 ))
[[ $AGE -gt 14 ]] && exit 0
N=0; [[ -f "$C" && "$C" -nt "$R" ]] && N=$(cat "$C" 2>/dev/null || echo 0)
[[ $N -ge 3 ]] && exit 0
echo $((N+1)) > "$C"
SUMMARY="$(grep -m1 '^\*\*Summary:\*\*' "$R" | sed 's/\*\*Summary:\*\* *//' | cut -c1-200)"
CHANGES="$(grep -m1 '^\*\*Changes:\*\*' "$R" | sed 's/\*\*Changes:\*\* *//' | cut -c1-200)"
SEEN="$S"; case "$S" in "$HOME"/*) SEEN="~${S#"$HOME"}" ;; esac
echo "Blacksmith: unread weekly report (from ${AGE} days ago, reminder $((N+1))/3). ${SUMMARY} ${CHANGES} Mention this to the user in one sentence at the start of your first reply (report: /blacksmith show, apply: /blacksmith apply N). If the user says they have already read it, run: touch ${SEEN}"
exit 0
