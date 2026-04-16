#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# setup-appium.sh
# Installs Appium, the UiAutomator2 Android driver, and
# Appium Inspector on macOS. Checks all prerequisites first.
# ─────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── 1. Check prerequisites ──────────────────────────────────

info "Checking prerequisites..."

# Homebrew
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found. Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    info "Homebrew found: $(brew --version | head -1)"
fi

# Node.js
if ! command -v node &>/dev/null; then
    warn "Node.js not found. Installing via Homebrew..."
    brew install node
else
    info "Node.js found: $(node --version)"
fi

# npm
if ! command -v npm &>/dev/null; then
    error "npm not found. Please install Node.js which includes npm."
fi
info "npm found: $(npm --version)"

# Java
if ! command -v java &>/dev/null; then
    warn "Java not found. Installing OpenJDK 17 via Homebrew..."
    brew install openjdk@17
    echo 'export PATH="/opt/homebrew/opt/openjdk@17/bin:$PATH"' >> ~/.zshrc
    export PATH="/opt/homebrew/opt/openjdk@17/bin:$PATH"
else
    info "Java found: $(java --version 2>&1 | head -1)"
fi

# JAVA_HOME
if [ -z "${JAVA_HOME:-}" ]; then
    warn "JAVA_HOME is not set. Attempting to set it..."
    if [ -d "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home" ]; then
        export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
        echo "export JAVA_HOME=\"$JAVA_HOME\"" >> ~/.zshrc
        info "JAVA_HOME set to $JAVA_HOME"
    else
        warn "Could not auto-detect JAVA_HOME. Set it manually in your shell profile."
    fi
else
    info "JAVA_HOME is set: $JAVA_HOME"
fi

# Android SDK
if [ -z "${ANDROID_HOME:-}" ]; then
    if [ -d "$HOME/Library/Android/sdk" ]; then
        export ANDROID_HOME="$HOME/Library/Android/sdk"
        echo 'export ANDROID_HOME="$HOME/Library/Android/sdk"' >> ~/.zshrc
        echo 'export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"' >> ~/.zshrc
        info "ANDROID_HOME set to $ANDROID_HOME"
    else
        warn "Android SDK not found. Install Android Studio or set ANDROID_HOME manually."
        warn "Download: https://developer.android.com/studio"
    fi
else
    info "ANDROID_HOME is set: $ANDROID_HOME"
fi

# adb
if ! command -v adb &>/dev/null; then
    warn "adb not found on PATH. Make sure \$ANDROID_HOME/platform-tools is in your PATH."
else
    info "adb found: $(adb version | head -1)"
fi

echo ""
info "All prerequisites checked."
echo ""

# ── 2. Install Appium ───────────────────────────────────────

if command -v appium &>/dev/null; then
    info "Appium already installed: $(appium --version)"
else
    info "Installing Appium globally via npm..."
    npm install -g appium
    info "Appium installed: $(appium --version)"
fi

# ── 3. Install UiAutomator2 driver ──────────────────────────

if appium driver list --installed 2>&1 | grep -q "uiautomator2"; then
    info "UiAutomator2 driver already installed."
else
    info "Installing Appium UiAutomator2 driver..."
    appium driver install uiautomator2
    info "UiAutomator2 driver installed."
fi

# ── 4. Install Appium Inspector (desktop app) ───────────────

if [ -d "/Applications/Appium Inspector.app" ]; then
    info "Appium Inspector already installed."
else
    info "Installing Appium Inspector via Homebrew Cask..."
    brew install --cask appium-inspector
    info "Appium Inspector installed."
fi

# ── 5. Summary ───────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Setup complete! Installed versions:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Appium:             $(appium --version)"
echo "  UiAutomator2:       $(appium driver list --installed 2>&1 | grep uiautomator2 | sed 's/.*@//' | sed 's/ .*//')"
echo "  Node.js:            $(node --version)"
echo "  npm:                $(npm --version)"
echo "  Java:               $(java --version 2>&1 | head -1)"
echo "  adb:                $(adb version 2>/dev/null | head -1 || echo 'not found')"
echo "  Appium Inspector:   /Applications/Appium Inspector.app"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Next steps:"
echo "  1. Start Appium server:  appium"
echo "  2. Open Appium Inspector: open '/Applications/Appium Inspector.app'"
echo "  3. See README.md for full usage instructions."
echo ""
