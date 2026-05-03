#!/usr/bin/env bash
set -euo pipefail

AVD_NAME="${AVD_NAME:-api36-mitm}"
MITM_HOST="${MITM_HOST:-127.0.0.1}"
MITM_PORT="${MITM_PORT:-8080}"
TEST_URL="${TEST_URL:-https://example.com}"
SERIAL="${SERIAL:-emulator-5554}"
RUN_DIR="${XDG_RUNTIME_DIR:-/tmp}/android-mitm-lab"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMAND="${1:-help}"
[[ $# -gt 0 ]] && shift

export ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_HOME}"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$HOME/.local/bin:$PATH"

log() {
  printf '[lab] %s\n' "$*"
}

pid_alive() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(<"$pid_file")"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

require_android_tools() {
  if ! command -v adb >/dev/null 2>&1 || ! command -v emulator >/dev/null 2>&1; then
    echo "ERROR: Android SDK tools are not on PATH. Run ./scripts/bootstrap-android-mitm-lab.sh first." >&2
    exit 1
  fi
}

require_mitmweb() {
  if ! command -v mitmweb >/dev/null 2>&1; then
    echo "ERROR: mitmweb is not on PATH. Run ./scripts/bootstrap-android-mitm-lab.sh first." >&2
    exit 1
  fi
}

wait_for_boot() {
  adb -s "$SERIAL" wait-for-device
  log "Waiting for Android boot."
  until [[ "$(adb -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; do
    sleep 2
  done
}

url_host() {
  local value="${1#http://}"
  value="${value#https://}"
  value="${value%%/*}"
  printf '%s\n' "$value"
}

start_emulator() {
  require_android_tools
  mkdir -p "$RUN_DIR"
  if adb devices | awk -v serial="$SERIAL" '$1 == serial && $2 == "device" { found = 1 } END { exit !found }'; then
    log "Emulator $SERIAL is already running."
    return
  fi
  log "Starting emulator $AVD_NAME."
  emulator "@${AVD_NAME}" \
    -no-window \
    -no-audio \
    -gpu swiftshader_indirect \
    -writable-system \
    -no-snapshot \
    -wipe-data &
  echo "$!" > "$RUN_DIR/emulator.pid"
  wait_for_boot
}

start_proxy() {
  require_mitmweb
  mkdir -p "$RUN_DIR"
  if pid_alive "$RUN_DIR/mitmweb.pid"; then
    log "mitmweb is already running."
    return
  fi
  log "Starting mitmweb on ${MITM_HOST}:${MITM_PORT}."
  mitmweb --listen-host "$MITM_HOST" --listen-port "$MITM_PORT" >/tmp/android-mitm-lab-mitmweb.log 2>&1 &
  echo "$!" > "$RUN_DIR/mitmweb.pid"

  log "Waiting for mitmproxy CA generation."
  for _ in {1..30}; do
    [[ -f "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" ]] && return
    sleep 1
  done
  echo "ERROR: mitmproxy CA was not generated at ~/.mitmproxy/mitmproxy-ca-cert.pem." >&2
  exit 1
}

set_proxy() {
  require_android_tools
  log "Configuring Android global proxy to emulator host 10.0.2.2:${MITM_PORT}."
  adb -s "$SERIAL" shell settings put global http_proxy "10.0.2.2:${MITM_PORT}"
  adb -s "$SERIAL" shell am force-stop com.android.chrome || true
}

clear_proxy() {
  require_android_tools
  adb -s "$SERIAL" shell settings put global http_proxy :0
  adb -s "$SERIAL" shell settings delete global http_proxy || true
}

open_site() {
  require_android_tools
  local url="${1:-$TEST_URL}"
  adb -s "$SERIAL" shell am start -a android.intent.action.VIEW -d "$url"
}

install_ca() {
  local url="${1:-$TEST_URL}"
  python3 "$ROOT_DIR/mac_to_android_corp_cas.py" \
    --skip-keychain \
    --extra-ca-pem "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" \
    --host "$(url_host "$url")" \
    --proxy "${MITM_HOST}:${MITM_PORT}" \
    --serial "$SERIAL"
}

stop_from_pidfile() {
  local name="$1"
  local pid_file="$2"
  if pid_alive "$pid_file"; then
    local pid
    pid="$(<"$pid_file")"
    log "Stopping $name (pid $pid)."
    kill "$pid"
  fi
  rm -f "$pid_file"
}

status() {
  require_android_tools
  adb devices
  if pid_alive "$RUN_DIR/mitmweb.pid"; then
    log "mitmweb pid $(<"$RUN_DIR/mitmweb.pid") is running."
  else
    log "mitmweb pid file is not active."
  fi
}

usage() {
  cat <<EOF
Usage: $0 <command> [args]

Commands:
  start-emulator       Start the API 36 writable-system emulator
  start-proxy          Start mitmweb on ${MITM_HOST}:${MITM_PORT}
  set-proxy            Route Android global proxy through mitmproxy
  open-site [url]      Open an HTTPS URL in Android Chrome
  install-ca [url]     Install ~/.mitmproxy/mitmproxy-ca-cert.pem into Android
  clear-proxy          Remove Android global proxy
  status               Show adb devices and mitmweb status
  stop-proxy           Stop mitmweb started by this script
  stop-emulator        Stop emulator process started by this script
  all [url]            Start emulator/proxy, set proxy, and open URL
EOF
}

case "$COMMAND" in
  start-emulator) start_emulator ;;
  start-proxy) start_proxy ;;
  set-proxy) set_proxy ;;
  open-site) open_site "${1:-$TEST_URL}" ;;
  install-ca) install_ca "${1:-$TEST_URL}" ;;
  clear-proxy) clear_proxy ;;
  status) status ;;
  stop-proxy) stop_from_pidfile "mitmweb" "$RUN_DIR/mitmweb.pid" ;;
  stop-emulator) stop_from_pidfile "emulator" "$RUN_DIR/emulator.pid" ;;
  all)
    start_emulator
    start_proxy
    set_proxy
    open_site "${1:-$TEST_URL}"
    ;;
  help|--help|-h) usage ;;
  *)
    usage >&2
    exit 2
    ;;
esac
