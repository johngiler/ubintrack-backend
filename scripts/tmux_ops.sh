#!/usr/bin/env bash
# Ubintrack ops dashboard in tmux (API host).
#
# Layout (same family as PortPax/Publivalla):
#   ┌─────────────────────────────────────────┐
#   │  gunicorn logs (error + access)         │  (full width, ccze)
#   ├─────────────┬─────────────┬─────────────┤
#   │ celery.log  │ daphne.log  │    btop     │  (equal width; celery/daphne
#   └─────────────┴─────────────┴─────────────┘   panes idle until those land)
#
# Usage:
#   ./scripts/tmux_ops.sh start|stop|restart|status|attach|run
#
# Install systemd unit (API server, as root):
#   cp scripts/systemd/tmux.service /etc/systemd/system/tmux.service
#   systemctl daemon-reload
#   systemctl enable --now tmux.service
#
# Attach as git:
#   tmux attach -t ubintrack-ops

set -euo pipefail

SESSION="${UBINTRACK_TMUX_SESSION:-ubintrack-ops}"
# Ubintrack unit writes under /var/log/gunicorn/ (subdir), unlike Publivalla flat paths.
GUNICORN_LOG="${UBINTRACK_GUNICORN_LOG:-/var/log/gunicorn/gunicorn.log}"
GUNICORN_ACCESS_LOG="${UBINTRACK_GUNICORN_ACCESS_LOG:-/var/log/gunicorn/gunicorn-access.log}"
GUNICORN_ERROR_LOG="${UBINTRACK_GUNICORN_ERROR_LOG:-/var/log/gunicorn/gunicorn-error.log}"
CELERY_LOG="${UBINTRACK_CELERY_LOG:-/var/log/celery.log}"
DAPHNE_LOG="${UBINTRACK_DAPHNE_LOG:-/var/log/daphne.log}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BTOP_CONF_SRC="${UBINTRACK_BTOP_CONF:-$SCRIPT_DIR/btop.conf}"
BTOP_CONF_DST="${HOME:-/home/git}/.config/btop/btop.conf"

colorize_pipe() {
  if command -v ccze >/dev/null 2>&1; then
    echo "| ccze -A"
  else
    echo ""
  fi
}

ensure_btop_config() {
  if [[ ! -f "$BTOP_CONF_SRC" ]]; then
    return 0
  fi
  mkdir -p "$(dirname "$BTOP_CONF_DST")"
  cp -f "$BTOP_CONF_SRC" "$BTOP_CONF_DST"
}

btop_cmd() {
  ensure_btop_config
  if command -v btop >/dev/null 2>&1; then
    echo "btop"
  elif command -v htop >/dev/null 2>&1; then
    echo "htop"
  else
    echo "top"
  fi
}

session_exists() {
  tmux has-session -t "$SESSION" 2>/dev/null
}

cmd_start() {
  if session_exists; then
    echo "tmux session '$SESSION' already running"
    return 0
  fi

  if ! command -v tmux >/dev/null 2>&1; then
    echo "error: tmux is not installed" >&2
    return 1
  fi

  local colorize
  colorize="$(colorize_pipe)"
  # Bootstrap size only — systemd has no TTY. After the layout exists we switch
  # to window-size latest so attach fills the real client terminal (no fixed width).
  local boot_cols=120
  local boot_rows=40
  local bottom_rows=$(( boot_rows / 2 ))

  tmux new-session -d -s "$SESSION" -n ops \
    "tail -n 200 -F ${GUNICORN_LOG} ${GUNICORN_ACCESS_LOG} ${GUNICORN_ERROR_LOG} 2>/dev/null ${colorize}"

  tmux set-window-option -t "${SESSION}:ops" aggressive-resize off
  tmux set-window-option -t "${SESSION}:ops" window-size manual
  tmux resize-window -t "${SESSION}:ops" -x "$boot_cols" -y "$boot_rows"

  # Bottom row (50% height): celery | daphne | btop
  tmux split-window -v -t "${SESSION}:ops.0" -l "$bottom_rows" \
    "tail -n 200 -F ${CELERY_LOG} 2>/dev/null ${colorize}"
  tmux split-window -h -t "${SESSION}:ops.1" \
    "tail -n 200 -F ${DAPHNE_LOG} 2>/dev/null ${colorize}"
  tmux split-window -h -t "${SESSION}:ops.2" \
    "$(btop_cmd)"

  # Top = full width; bottom three = equal columns; vertical split stays 50/50
  tmux select-pane -t "${SESSION}:ops.0"
  tmux select-layout -t "${SESSION}:ops" main-horizontal
  tmux resize-pane -t "${SESSION}:ops.0" -y "50%"

  # Follow the attaching client size (not the bootstrap 120x40).
  tmux set-window-option -t "${SESSION}:ops" window-size latest
  tmux set-window-option -t "${SESSION}:ops" aggressive-resize on

  echo "started tmux session '$SESSION' (layout adapts on attach)"
  echo "attach: tmux attach -t $SESSION"
}

cmd_stop() {
  if session_exists; then
    tmux kill-session -t "$SESSION"
    echo "stopped tmux session '$SESSION'"
  else
    echo "tmux session '$SESSION' is not running"
  fi
}

cmd_restart() {
  cmd_stop || true
  sleep 0.3
  cmd_start
}

cmd_status() {
  if session_exists; then
    echo "active: $SESSION"
    tmux list-panes -t "${SESSION}:ops" -F \
      '#{pane_index} #{pane_width}x#{pane_height} #{pane_current_command}'
    return 0
  fi
  echo "inactive: $SESSION"
  return 3
}

cmd_attach() {
  if ! session_exists; then
    cmd_start
  fi
  exec tmux attach -t "$SESSION"
}

# Foreground watchdog for systemd Type=simple + Restart=always
cmd_run() {
  cleanup() {
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    exit 0
  }
  trap cleanup TERM INT

  cmd_start
  while session_exists; do
    sleep 5
  done
  echo "tmux session '$SESSION' exited unexpectedly" >&2
  exit 1
}

usage() {
  echo "Usage: $0 {start|stop|restart|status|attach|run}" >&2
  exit 2
}

cmd="${1:-}"
case "$cmd" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  attach) cmd_attach ;;
  run) cmd_run ;;
  *) usage ;;
esac
