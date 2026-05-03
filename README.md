# Android Agent - Appium Inspector Setup

## Android Emulator TLS-Inspection Lab

This repo includes a reproducible lab for validating Android API 36 emulator
trust-store changes against a local TLS-inspection proxy. It runs locally on
Linux with KVM, on Google Cloud Compute Engine with nested virtualization, and
on DigitalOcean Droplets that expose `/dev/kvm`.

### Provider setup

**Local Linux VM or bare metal:**

```bash
./scripts/bootstrap-android-mitm-lab.sh
```

The bootstrap script installs Linux packages, Android command-line tools, the
API 36 `google_apis;x86_64` image, and `mitmproxy`. It writes
`~/.android-mitm-lab.env` with the Android SDK paths used by the lab.

**Google Cloud:**

```bash
./scripts/gcp-create-android-mitm-lab.sh
gcloud compute ssh android-mitm-lab --zone=us-central1-a
git clone <this-repo-url>
cd <repo>
./scripts/bootstrap-android-mitm-lab.sh
```

The GCP helper creates an `n2-standard-2` Spot VM with nested virtualization
enabled. Override `MACHINE_TYPE=n2-standard-4` for a roomier emulator host.

**DigitalOcean:**

```bash
./scripts/digitalocean-create-android-mitm-lab.sh
ssh root@<droplet-ip>
git clone <this-repo-url>
cd <repo>
./scripts/bootstrap-android-mitm-lab.sh --validate-only
./scripts/bootstrap-android-mitm-lab.sh
```

Destroy the Droplet if validation reports missing `/dev/kvm`; the Android
emulator needs KVM acceleration for this lab.

### End-to-end lab flow

In the lab VM:

```bash
source ~/.android-mitm-lab.env
./scripts/run-mitm-lab.sh start-emulator
./scripts/run-mitm-lab.sh start-proxy
./scripts/run-mitm-lab.sh set-proxy
./scripts/run-mitm-lab.sh open-site https://example.com
```

Before installing the CA, Chrome should show a certificate/privacy error. Then
install the mitmproxy CA into Android's legacy and Conscrypt APEX stores:

```bash
python3 mac_to_android_corp_cas.py \
  --skip-keychain \
  --extra-ca-pem ~/.mitmproxy/mitmproxy-ca-cert.pem \
  --host example.com \
  --proxy 127.0.0.1:8080
adb shell am force-stop com.android.chrome
./scripts/run-mitm-lab.sh open-site https://example.com
```

Chrome should now load the HTTPS page through mitmproxy. Re-run the installer
after emulator reboot because the Android 14+ Conscrypt APEX bind mount is
temporary.

On macOS, omit `--skip-keychain` to probe target hosts and select matching
corporate Keychain CAs. Cloud runs should pass explicit PEM files with
`--extra-ca-pem`.

Useful Taskfile shortcuts:

```bash
task validate-kvm
task start-mitm-emulator
task start-mitmproxy
task configure-android-proxy
task run-mitm-lab -- open-site https://example.com
task install-mitm-ca
task clear-android-proxy
task stop
```

## Quick Start

```bash
# Run the setup script to install everything
chmod +x setup-appium.sh
./setup-appium.sh
```

The script installs and configures:
- **Appium** (automation server)
- **UiAutomator2** (Android driver for Appium)
- **Appium Inspector** (GUI for inspecting app elements)

It also checks for required prerequisites (Node.js, Java, Android SDK, adb) and installs any that are missing.

## Prerequisites

These are handled automatically by the setup script, but for reference:

| Dependency | Purpose |
|---|---|
| Homebrew | macOS package manager |
| Node.js + npm | Runs the Appium server |
| Java 17 (JDK) | Required by UiAutomator2 |
| Android SDK | Provides platform tools and emulator |
| adb | Communicates with Android devices/emulators |

## Usage

### 1. Connect a Device or Start an Emulator

**Physical device:**
- Enable Developer Options on your Android device (Settings > About Phone > tap Build Number 7 times).
- Enable USB Debugging in Developer Options.
- Connect via USB and confirm the debugging prompt on the device.
- Verify the connection:
  ```bash
  adb devices
  ```

**Emulator:**
- Launch an emulator from Android Studio (Device Manager) or from the command line:
  ```bash
  emulator -avd <your_avd_name>
  ```
- List available AVDs:
  ```bash
  emulator -list-avds
  ```

### 2. Start the Appium Server

Open a terminal and run:

```bash
appium
```

The server starts on `http://127.0.0.1:4723` by default. Keep this terminal open.

To use a custom port:

```bash
appium --port 4726
```

### 3. Open Appium Inspector

Launch from the terminal:

```bash
open '/Applications/Appium Inspector.app'
```

Or open it from Spotlight / Applications.

> **Note:** On first launch, macOS Gatekeeper may block the app. Right-click the app > Open > confirm to bypass.

### 4. Configure Desired Capabilities

In Appium Inspector, set the **Remote Host** to `127.0.0.1`, **Port** to `4723`, and **Path** to `/`.

Then add your desired capabilities as JSON. Below are common configurations.

**Launch an APK file:**

```json
{
  "platformName": "Android",
  "appium:automationName": "UiAutomator2",
  "appium:deviceName": "Android",
  "appium:app": "package:com.zoho.inventory"
}
```

**Attach to an already-installed app:**

```json
{
  "platformName": "Android",
  "appium:automationName": "UiAutomator2",
  "appium:deviceName": "Android",
  "appium:appPackage": "com.example.myapp",
  "appium:appActivity": "com.example.myapp.MainActivity"
}
```

**Attach to whatever app is currently on screen:**

```json
{
  "platformName": "Android",
  "appium:automationName": "UiAutomator2",
  "appium:deviceName": "Android",
  "appium:autoLaunch": false
}
```

> **Tip:** Find an app's package and activity with:
> ```bash
> adb shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'
> ```

### 5. Start an Inspector Session

Click **Start Session** in Appium Inspector. The Inspector will:

1. Connect to the Appium server.
2. Launch (or attach to) your app on the device/emulator.
3. Display a screenshot with an interactive element hierarchy.

### 6. Inspect Elements

Once the session is running you can:

- **Tap an element** in the screenshot to see its attributes (resource-id, text, class, content-desc, bounds).
- **Use the element tree** on the right panel to browse the full view hierarchy.
- **Copy locator strategies** — Inspector shows selectors you can use in your test scripts:
  - `id` (resource-id)
  - `accessibility id` (content-desc)
  - `xpath`
  - `class name`
  - `-android uiautomator` (UiSelector expressions)
- **Tap / Send Keys / Clear** — interact with elements directly from the Inspector.
- **Refresh** the screenshot to see updated UI state.
- **Record actions** — use the recording feature to generate code in your preferred language (Python, Java, JavaScript, etc.).

### 7. Use Locators in Test Scripts

Example using the Python Appium client:

```python
from appium import webdriver
from appium.options import UiAutomator2Options

options = UiAutomator2Options()
options.platform_name = "Android"
options.device_name = "Android"
options.app_package = "com.example.myapp"
options.app_activity = "com.example.myapp.MainActivity"

driver = webdriver.Remote("http://127.0.0.1:4723", options=options)

# Use locators found in Appium Inspector
element = driver.find_element("id", "com.example.myapp:id/login_button")
element.click()

driver.quit()
```

## Screen Mirroring with scrcpy

[scrcpy](https://github.com/Genymobile/scrcpy) mirrors your Android device or emulator screen on your computer with low latency. It also lets you control the device with your keyboard and mouse.

### Install

```bash
task install-scrcpy
```

Or manually:

```bash
brew install scrcpy
```

### Usage

**Mirror the emulator:**

```bash
scrcpy -s emulator-5554
```

**Mirror a physical device (USB):**

```bash
scrcpy -s <device_serial>
```

Find your device serial with `adb devices`.

### Useful Flags

| Flag | Description |
|---|---|
| `--max-size 1024` | Limit resolution to 1024px (reduces latency) |
| `--bit-rate 2M` | Set video bitrate (default 8M) |
| `--record file.mp4` | Record the session to a file |
| `--no-audio` | Disable audio forwarding |
| `--turn-screen-off` | Turn off the device screen while mirroring |
| `--stay-awake` | Prevent the device from sleeping while connected |
| `--window-title "My Device"` | Set a custom window title |

### Examples

```bash
# Mirror emulator at reduced resolution and record
scrcpy -s emulator-5554 --max-size 1024 --record session.mp4

# Mirror physical device, keep screen off on device
scrcpy --turn-screen-off --stay-awake

# Just record without displaying a window
scrcpy --no-playback --record session.mp4
```

## Automated Screen Capture

Drive the device with scrcpy while a background script automatically captures the XML hierarchy and a screenshot every time the screen changes. This is useful for recording human interaction traces.

### How It Works

1. **Appium server** runs in the background and maintains a UiAutomator2 session.
2. **scrcpy** mirrors the device so you can interact with it naturally.
3. **Capture poller** (`server/main.py`) polls Appium's `/session/:id/source` endpoint, hashes the XML, and saves a new XML + PNG pair whenever the screen changes.

### Quick Start

Open **three terminals** (or use the combined task from step 3):

**Terminal 1 — Appium server:**

```bash
task start-appium
```

**Terminal 2 — scrcpy mirror:**

```bash
task start-scrcpy
```

**Terminal 3 — capture poller:**

```bash
# Attach to whatever app is on screen
task start-capture

# Attach to a specific package
task start-capture -- --package com.zoho.inventory

# Reuse an Appium Inspector session
task start-capture -- --session-id <id>

# Custom poll interval (default 0.5s)
task start-capture -- --interval 0.3
```

Or run scrcpy and the capture poller together (start Appium first):

```bash
task capture
```

### Capture Output

Captures are saved to `server/captures/` as sequentially numbered pairs:

```
server/captures/
  0001_20260415T120000Z.xml
  0001_20260415T120000Z.png
  0002_20260415T120005Z.xml
  0002_20260415T120005Z.png
  ...
```

Each `.xml` file is the full UiAutomator2 accessibility hierarchy. Each `.png` is a device screenshot taken at the same moment.

### CLI Reference

```
uv run main.py [OPTIONS]

Options:
  --host TEXT        Appium server host (default: 127.0.0.1)
  --port INT         Appium server port (default: 4723)
  --session-id TEXT  Reuse an existing Appium session
  --package TEXT     Android app package to attach to
  --activity TEXT    Launch activity (auto-resolved if omitted)
  --interval FLOAT   Polling interval in seconds (default: 0.5)
```

## Common Appium Server Commands

```bash
# Start server (default port 4723)
appium

# Start server with verbose logging
appium --log-level debug

# Start server on a custom port
appium --port 4726

# List installed drivers
appium driver list --installed

# Update UiAutomator2 driver
appium driver update uiautomator2
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `adb devices` shows nothing | Check USB cable, enable USB debugging, accept the prompt on the device |
| Appium Inspector won't open | Right-click the app > Open to bypass Gatekeeper |
| "Could not find adb" | Ensure `$ANDROID_HOME/platform-tools` is on your `PATH` |
| "JAVA_HOME is not set" | Run: `export JAVA_HOME=$(/usr/libexec/java_home)` and add it to `~/.zshrc` |
| Session fails to start | Make sure the Appium server is running before clicking Start Session |
| "UiAutomator2 not installed" | Run: `appium driver install uiautomator2` |
| Emulator not detected | Start the emulator first, then verify with `adb devices` |
