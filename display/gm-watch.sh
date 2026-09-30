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
#   bash display/gm-watch.sh stop [--port N] [--no-server]
#
# Run in the foreground, Ctrl-C stops it cleanly (the in-flight GM turn is
# signalled and its action is put back on the queue). Started in the background
# or from a subagent there is no terminal, so use `stop`: it stops this watcher
# FIRST, then the display server on --port (default $GM_DISPLAY_PORT or 5001),
# then removes the pidfiles. Stopping the server first would leave a live
# consumer polling a dead server. State lives in display/.gm-watch.log,
# display/.gm-watch.pid and the display/.gm-watch.lock start guard.
#
# Env: GM_WATCH_KILL_GRACE (seconds between TERM and KILL of a turn on shutdown,
# default 10), GM_WATCH_STARTUP_DELAY (default 5).

set -uo pipefail

DISPLAY_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DISPLAY_DIR/.gm-watch.log"
PIDFILE="$DISPLAY_DIR/.gm-watch.pid"
QUEUE="$DISPLAY_DIR/.input_queue"
DRAIN="python3 $DISPLAY_DIR/drain_queue.py"

# macOS has no coreutils `timeout`. Detect it once; gm-watch falls back to a
# pure-bash watchdog if it is missing rather than failing every turn with 127.
TIMEOUT_BIN="$(command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null || true)"

LOCKDIR="$DISPLAY_DIR/.gm-watch.lock"
KILL_GRACE="${GM_WATCH_KILL_GRACE:-10}"

# True when $1 is a live process whose command line is one of ours ($2 pattern).
# A bare `kill -0` also succeeds on a PID recycled by an unrelated process after
# a reboot, which would give a false "already running" (or kill a stranger).
pid_is_ours() {
  local pid="$1" pat="$2"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -o command= -p "$pid" 2>/dev/null | grep -q "$pat"
}

# TERM, wait up to $2 seconds, then KILL. Never signals a process that is not ours.
stop_pid() {
  local pid="$1" grace="$2" i=0
  kill -TERM "$pid" 2>/dev/null || return 0
  while kill -0 "$pid" 2>/dev/null && [[ $i -lt $grace ]]; do sleep 1; i=$((i+1)); done
  kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
  return 0
}

do_stop() {
  local port="${GM_DISPLAY_PORT:-5001}" server=true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --port) port="${2:-$port}"; shift 2 ;;
      --no-server) server=false; shift ;;
      *) echo "unknown stop option: $1" >&2; return 1 ;;
    esac
  done
  local pid
  # 1. consumer first
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if pid_is_ours "$pid" "gm-watch\.sh"; then
    echo "stopping watcher (PID $pid)"
    stop_pid "$pid" $((KILL_GRACE + 20))
  fi
  rm -f "$PIDFILE"; rm -rf "$LOCKDIR"
  # 2. then the server
  if $server; then
    local sp="$DISPLAY_DIR/app-$port.pid"
    pid="$(cat "$sp" 2>/dev/null || true)"
    if pid_is_ours "$pid" "gm-display-app\.py"; then
      echo "stopping display server (PID $pid, port $port)"
      stop_pid "$pid" 5
    fi
    rm -f "$sp"
  fi
  return 0
}

if [[ "${1:-}" == "stop" ]]; then
  shift
  do_stop "$@"
  exit $?
fi

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
# would be delivered twice. `mkdir` is the atomic primitive (flock is not on stock
# macOS). A leftover lock whose pid is dead or not a gm-watch is reclaimed by an
# atomic rename, so exactly one of several racing starters wins it.
acquire_lock() {
  local tries=0 owner
  while [[ $tries -lt 5 ]]; do
    if mkdir "$LOCKDIR" 2>/dev/null; then
      echo $$ > "$PIDFILE"
      return 0
    fi
    owner="$(cat "$PIDFILE" 2>/dev/null || true)"
    if pid_is_ours "$owner" "gm-watch\.sh"; then
      echo "gm-watch already running as PID $owner" >&2
      return 1
    fi
    if [[ -z "$owner" && $tries -lt 2 ]]; then
      sleep 1          # a concurrent starter holds the lock but has not written its pid yet
    else
      mv "$LOCKDIR" "$LOCKDIR.stale.$$" 2>/dev/null && rm -rf "$LOCKDIR.stale.$$"
    fi
    tries=$((tries+1))
  done
  echo "gm-watch could not take $LOCKDIR" >&2
  return 1
}
acquire_lock || exit 1

log() { printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*" >> "$LOG"; }

# A signal-interruptible sleep: bash defers traps until a foreground child
# returns, so a bare `sleep` would delay shutdown by up to $INTERVAL seconds.
_nap_pid=""
nap() { sleep "$1" & _nap_pid=$!; wait "$_nap_pid" 2>/dev/null; _nap_pid=""; }

_turn_pid=""
_watchdog_pid=""
RAW_ACTION=""

on_signal() {
  trap '' INT TERM HUP
  [[ -n "$_nap_pid" ]] && kill "$_nap_pid" 2>/dev/null
  [[ -n "$_watchdog_pid" ]] && kill "$_watchdog_pid" 2>/dev/null
  if [[ -n "$_turn_pid" ]] && kill -0 "$_turn_pid" 2>/dev/null; then
    log "signal received -- stopping in-flight GM turn (PID $_turn_pid)"
    kill -TERM "$_turn_pid" 2>/dev/null
    local i=0
    while kill -0 "$_turn_pid" 2>/dev/null && [[ $i -lt $KILL_GRACE ]]; do sleep 1; i=$((i+1)); done
    if kill -0 "$_turn_pid" 2>/dev/null; then
      log "turn ignored TERM for ${KILL_GRACE}s -- KILL"
      kill -KILL "$_turn_pid" 2>/dev/null
    fi
    if [[ -n "$RAW_ACTION" ]]; then
      log "!! action NOT lost -- restoring to .input_queue"
      printf '%s\n' "$RAW_ACTION" >> "$QUEUE"
    fi
  fi
  log "watcher stopped"
  rm -f "$PIDFILE"; rm -rf "$LOCKDIR"
  exit 0
}
trap on_signal INT TERM HUP
trap 'rm -f "$PIDFILE"; rm -rf "$LOCKDIR"' EXIT

log "watcher started — session $SESSION, interval ${INTERVAL}s"

# Let the GM finish its opening narration before the first action lands.
nap "${GM_WATCH_STARTUP_DELAY:-5}"

while true; do
  # Only act on a *sent* action. The Flask app appends to .input_queue on Send,
  # so a present file means the action is waiting for us.
  if [[ -s "$QUEUE" ]]; then
    nap 1
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
          #
          # The turn runs in the BACKGROUND and we `wait` on it: bash only runs a
          # trap between commands, so a foreground child would delay it for the
          # whole turn. `exec` makes $_turn_pid the real process, so the trap's
          # TERM/KILL reaches opencode itself and not just a wrapper subshell.
          if [[ -n "$TIMEOUT_BIN" ]]; then
            ( cd "$DISPLAY_DIR/.." && exec "$TIMEOUT_BIN" --signal=TERM --kill-after=30 900 \
                opencode run --session "$SESSION" --auto "$PAYLOAD" ) >> "$LOG" 2>&1 &
            _turn_pid=$!
            wait "$_turn_pid"; RC=$?
          else
            log "no coreutils timeout found — using the built-in watchdog"
            ( cd "$DISPLAY_DIR/.." && exec opencode run --session "$SESSION" --auto "$PAYLOAD" ) \
                >> "$LOG" 2>&1 &
            _turn_pid=$!
            # Polls once a second so that killing it leaves no long sleep behind.
            ( _t=$_turn_pid; _n=0
              while kill -0 "$_t" 2>/dev/null; do
                sleep 1; _n=$((_n+1))
                if [[ $_n -ge 900 ]]; then
                  kill -TERM "$_t" 2>/dev/null; sleep 30; kill -KILL "$_t" 2>/dev/null; break
                fi
              done ) &
            _watchdog_pid=$!
            wait "$_turn_pid"; RC=$?
            kill "$_watchdog_pid" 2>/dev/null
            # A signal death (>128) is the watchdog firing. Written as an `if`, not
            # `[[ A ]] || [[ B ]] && C`: bash reads that left to right as
            # `(A || B) && C`, which turned every successful turn (RC=0) into 124.
            if [[ $RC -gt 128 ]]; then RC=124; fi
          fi
          _turn_pid=""; _watchdog_pid=""

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
  nap "$INTERVAL"
done
