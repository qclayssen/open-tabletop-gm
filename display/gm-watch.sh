#!/usr/bin/env bash
# gm-watch.sh — bridge the display's player-input panel to a DM subagent session.
#
# Why this exists
# ---------------
# The display's Party Input panel appends a player action to
# `display/.input_queue` the moment they tap Send. In the stock workflow
# `wrapper.py` injects that file into the PTY of an interactive terminal GM.
#
# When the GM is a *subagent session* there is no PTY, so the queue just fills
# and the player's Send button appears to do nothing. This watcher is the
# missing consumer: it polls the queue and forwards each action into the GM
# session with `opencode run --session`.
#
# The result is a fully automatic loop — the player sends on the display, and
# the GM responds. No orchestrating agent in the loop.
#
# Usage
# -----
#   bash display/gm-watch.sh <session-id> [--interval 3] [--dry-run]
#
# Stop with Ctrl-C. State lives in display/.gm-watch.log and display/.gm-watch.pid.

set -uo pipefail

DISPLAY_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DISPLAY_DIR/.gm-watch.log"
PIDFILE="$DISPLAY_DIR/.gm-watch.pid"
QUEUE="$DISPLAY_DIR/.input_queue"
DRAIN="python3 $DISPLAY_DIR/drain_queue.py"

# macOS has no coreutils `timeout`. Detect it once; gm-watch falls back to a
# pure-bash watchdog if it is missing rather than failing every turn with 127.
TIMEOUT_BIN="$(command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null || true)"

SESSION=""
INTERVAL=3
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval) INTERVAL="${2:-3}"; shift 2 ;;
    --dry-run)  DRY_RUN=true; shift ;;
    -*) echo "unknown flag: $1" >&2; exit 1 ;;
    *)  SESSION="$1"; shift ;;
  esac
done

if [[ -z "$SESSION" ]]; then
  echo "usage: gm-watch.sh <session-id> [--interval N] [--dry-run]" >&2
  exit 1
fi

# Refuse to double-start — two watchers would race the queue and one action
# would be delivered twice.
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "gm-watch already running as PID $(cat "$PIDFILE")" >&2
  exit 1
fi
echo $$ > "$PIDFILE"

log() { printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*" >> "$LOG"; }

log "watcher started — session $SESSION, interval ${INTERVAL}s"
trap 'log "watcher stopped"; rm -f "$PIDFILE"; exit 0' INT TERM

# Let the GM finish its opening narration before the first action lands.
sleep 5

while true; do
  # Only act on a *sent* action. The Flask app appends to .input_queue on Send,
  # so a present file means the action is waiting for us.
  if [[ -s "$QUEUE" ]]; then
    sleep 1
    if [[ -s "$QUEUE" ]]; then
      # Snapshot the raw action BEFORE draining. If the GM turn fails or times
      # out we restore exactly this, so a failed delivery retries on the next
      # poll instead of eating the player's input.
      RAW_ACTION="$(cat "$QUEUE")"
      PAYLOAD="$($DRAIN 2>>"$LOG")"

      if [[ -z "${PAYLOAD// }" ]] || grep -q "No player input was sent" <<<"$PAYLOAD"; then
        log "empty drain, nothing to send"
      else
        log "action drained:"
        while IFS= read -r line; do log "  | $line"; done <<<"$PAYLOAD"

        if $DRY_RUN; then
          log "DRY RUN — would send to session $SESSION"
        else
          log "forwarding to GM session $SESSION ..."
          # --auto lets the GM use its tools (dice, display pushes, file reads)
          # without stopping for a permission prompt it will never get.
          #
          # A hung `opencode run` blocks this serial loop forever and every
          # later player action piles up unseen while the display still shows
          # "Sent". A bound is mandatory.
          #
          # macOS ships NO coreutils `timeout` (and no `gtimeout` without brew).
          # Calling it unconditionally makes every turn exit 127 -> "FAILED" ->
          # restore -> retry, forever. So: detect it, and if it is absent fall
          # back to a bounded background-kill watchdog, which needs no
          # external binary.
          if [[ -n "$TIMEOUT_BIN" ]]; then
            ( cd "$DISPLAY_DIR/.." && "$TIMEOUT_BIN" --signal=TERM --kill-after=30 900 \
                opencode run --session "$SESSION" --auto "$PAYLOAD" ) >> "$LOG" 2>&1
            RC=$?
          else
            log "no coreutils timeout found — using the built-in watchdog"
            ( cd "$DISPLAY_DIR/.." && opencode run --session "$SESSION" --auto "$PAYLOAD" ) \
                >> "$LOG" 2>&1 &
            _turn_pid=$!
            ( sleep 900; kill -TERM "$_turn_pid" 2>/dev/null; sleep 30; \
              kill -KILL "$_turn_pid" 2>/dev/null ) &
            _watchdog_pid=$!
            wait "$_turn_pid"; RC=$?
            kill "$_watchdog_pid" 2>/dev/null
            # A signal death (>128) is the watchdog firing. Written as an `if`, not
            # `[[ A ]] || [[ B ]] && C`: bash reads that left to right as
            # `(A || B) && C`, which turned every successful turn (RC=0) into 124.
            if [[ $RC -gt 128 ]]; then RC=124; fi
          fi

          if [[ $RC -eq 0 ]]; then
            log "GM turn complete"
          elif [[ $RC -eq 124 || $RC -eq 137 ]]; then
            log "!! GM turn TIMED OUT (rc=$RC) after 900s"
            log "!! action NOT lost -- restoring to .input_queue for the next poll"
              # Append, never truncate: the drain already consumed this
              # action, so `>` would discard anything a player sent
              # while the GM turn was running.
            printf '%s\n' "$RAW_ACTION" >> "$QUEUE"
          else
            log "!! GM turn FAILED (rc=$RC) -- action NOT lost, restoring to .input_queue"
              # Append, never truncate: the drain already consumed this
              # action, so `>` would discard anything a player sent
              # while the GM turn was running.
            printf '%s\n' "$RAW_ACTION" >> "$QUEUE"
          fi
        fi
      fi
    fi
  fi
  sleep "$INTERVAL"
done
