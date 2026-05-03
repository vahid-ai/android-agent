#!/usr/bin/env bash
set -euo pipefail

ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_HOME}"
ANDROID_API="${ANDROID_API:-36}"
ANDROID_IMAGE="${ANDROID_IMAGE:-system-images;android-${ANDROID_API};google_apis;x86_64}"
AVD_NAME="${AVD_NAME:-api${ANDROID_API}-mitm}"
CMDLINE_TOOLS_VERSION="${CMDLINE_TOOLS_VERSION:-11076708}"
VALIDATE_ONLY=0

if [[ "${1:-}" == "--validate-only" ]]; then
  VALIDATE_ONLY=1
  shift
fi

export ANDROID_HOME ANDROID_SDK_ROOT
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$HOME/.local/bin:$PATH"

log() {
  printf '[lab] %s\n' "$*"
}

need_sudo() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

install_apt_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    log "Skipping apt package install; apt-get was not found."
    return
  fi

  log "Installing Linux packages."
  need_sudo apt-get update
  need_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    cpu-checker \
    curl \
    libvirt-clients \
    libvirt-daemon-system \
    openjdk-17-jdk \
    python3 \
    python3-pip \
    qemu-kvm \
    unzip
}

install_android_cmdline_tools() {
  if command -v sdkmanager >/dev/null 2>&1; then
    log "Android command-line tools already installed."
    return
  fi

  log "Installing Android command-line tools into $ANDROID_HOME."
  mkdir -p "$ANDROID_HOME/cmdline-tools"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT
  curl -fsSL \
    "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS_VERSION}_latest.zip" \
    -o "$tmpdir/cmdline-tools.zip"
  unzip -q "$tmpdir/cmdline-tools.zip" -d "$tmpdir"
  rm -rf "$ANDROID_HOME/cmdline-tools/latest"
  mv "$tmpdir/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
}

install_android_packages() {
  log "Accepting Android SDK licenses."
  yes | sdkmanager --licenses >/dev/null || true

  log "Installing Android API $ANDROID_API emulator packages."
  sdkmanager --install \
    "platform-tools" \
    "emulator" \
    "platforms;android-${ANDROID_API}" \
    "$ANDROID_IMAGE"
}

create_avd() {
  if avdmanager list avd | grep -q "Name: $AVD_NAME"; then
    log "AVD $AVD_NAME already exists."
    return
  fi

  log "Creating AVD $AVD_NAME from $ANDROID_IMAGE."
  echo "no" | avdmanager create avd \
    -n "$AVD_NAME" \
    -k "$ANDROID_IMAGE" \
    -d pixel_8
}

install_python_tools() {
  log "Installing mitmproxy for the current user."
  python3 -m pip install --user --upgrade mitmproxy
}

write_env_file() {
  cat > "$HOME/.android-mitm-lab.env" <<EOF
export ANDROID_HOME="$ANDROID_HOME"
export ANDROID_SDK_ROOT="$ANDROID_SDK_ROOT"
export PATH="\$ANDROID_HOME/platform-tools:\$ANDROID_HOME/emulator:\$ANDROID_HOME/cmdline-tools/latest/bin:\$HOME/.local/bin:\$PATH"
export AVD_NAME="$AVD_NAME"
EOF
  log "Wrote $HOME/.android-mitm-lab.env."
}

validate_kvm() {
  log "Validating nested KVM availability."
  if [[ ! -e /dev/kvm ]]; then
    log "WARNING: /dev/kvm is missing. Android Emulator will be too slow or fail."
    return 1
  fi
  if command -v kvm-ok >/dev/null 2>&1; then
    kvm-ok
    return
  fi
  log "/dev/kvm exists; install cpu-checker for kvm-ok details."
}

main() {
  if [[ "$VALIDATE_ONLY" -eq 1 ]]; then
    validate_kvm
    return
  fi

  install_apt_packages
  install_android_cmdline_tools
  install_android_packages
  create_avd
  install_python_tools
  write_env_file
  validate_kvm || true

  log "Setup complete."
  log "Run: source ~/.android-mitm-lab.env"
  log "Run emulator: emulator @${AVD_NAME} -no-window -no-audio -gpu swiftshader_indirect -writable-system -no-snapshot -wipe-data"
  log "Run proxy: mitmweb --listen-host 127.0.0.1 --listen-port 8080"
}

main "$@"
