# Android Emulator TLS-Inspection Cloud Lab Summary

## Goal

Set up a reproducible lab for testing a Python script that installs corporate TLS-inspection CA certificates into a rooted Android API 36 emulator so Chrome inside the emulator can access the same HTTPS sites that work on a corporate-managed MacBook.

The original real-world problem involved:

- A non-Google-Play Android emulator image, API level 36.
- Running on a corporate MacBook Pro.
- Network/security stack involving Netskope and Palo Alto Networks GlobalProtect.
- Chrome inside the emulator failing to access sites that Chrome on the MacBook can access.
- Likely cause: corporate TLS inspection CA is trusted by macOS/Chrome on the Mac, but not by Android inside the emulator.

## Mac-side certificate installation script

A Python script was proposed to run on the MacBook. Its purpose was to:

1. Probe HTTPS certificate chains for selected target hosts.
2. Dump certificates from macOS Keychain.
3. Identify corporate/proxy CA certificates that signed the observed TLS chains.
4. Convert those CA certificates to Android's hashed CA filename format.
5. Push them to the emulator using `adb`.
6. Handle modern Android CA-store behavior, including Android 14+ / API 34+ Conscrypt APEX paths.

Important implementation details:

- Android apps targeting API 24+ do not automatically trust user-installed CAs.
- On Android 14+ and newer, including API 36, trusted CA handling involves the Conscrypt APEX path:

```text
/apex/com.android.conscrypt/cacerts
```

- Older guidance that only pushes to this path may be incomplete:

```text
/system/etc/security/cacerts
```

- For API 36 emulator images, the script should detect and handle both legacy and APEX-style CA stores.
- The APEX bind-mount method is temporary and must be repeated after emulator reboot.
- Certificate pinning can still break access even when the CA is installed correctly.

## Cloud lab design

The recommended lab topology was:

```text
MacBook / SSH
   ↓
Cloud Linux VM with nested KVM
   ├── Android API 36 emulator
   └── mitmproxy acting like a corporate TLS-inspection proxy
```

The idea is to reproduce the same class of failure as Netskope or GlobalProtect without needing those products:

1. Run an Android emulator in the cloud VM.
2. Run `mitmproxy` on the same VM.
3. Force Android emulator traffic through `mitmproxy`.
4. Observe Chrome failing with certificate authority / privacy / HSTS-style errors.
5. Install the `mitmproxy` CA into Android's trust store using the script.
6. Verify Chrome can then load HTTPS sites through the proxy.

## Why mitmproxy works for this test

A full Palo Alto or Netskope deployment is not necessary to test the CA-install logic.

The relevant failure mode is:

```text
Host or proxy trusts TLS-inspection CA
Android emulator does not trust that CA
Chrome in emulator rejects inspected HTTPS traffic
```

`mitmproxy` reproduces this condition cleanly.

## Recommended cloud setup

The most important requirement is not raw price but whether the provider exposes usable nested virtualization / KVM to the VM.

Android Emulator on Linux needs hardware acceleration through KVM for practical x86/x86_64 emulator performance.

Validation commands after provisioning any server:

```bash
ls -l /dev/kvm
egrep -c '(vmx|svm)' /proc/cpuinfo
sudo apt update
sudo apt install -y cpu-checker
kvm-ok
```

If `/dev/kvm` is missing or `kvm-ok` fails, the provider is not suitable for this setup.

## GCP recommendation

The most reliable recommendation was Google Cloud Compute Engine with nested virtualization enabled.

Cheapest disposable option:

```text
GCP n2-standard-2 Spot
2 vCPU / 8 GB RAM
```

More comfortable option:

```text
GCP n2-standard-4 Spot
4 vCPU / 16 GB RAM
```

Example GCP VM creation command:

```bash
gcloud compute instances create android-mitm-lab \
  --zone=us-central1-a \
  --machine-type=n2-standard-2 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=100GB \
  --boot-disk-type=pd-standard \
  --enable-nested-virtualization \
  --min-cpu-platform="Intel Haswell" \
  --provisioning-model=SPOT \
  --instance-termination-action=STOP
```

Notes:

- GCP E2 machines should be avoided for this use case because nested virtualization support is restricted.
- Spot instances are cheapest but may be interrupted.
- Boot disks can continue billing even when compute is stopped.

## AWS option

AWS can work on newer instance families that support nested virtualization, such as C8i, M8i, and R8i.

It was considered viable but usually more expensive than GCP for this lab.

## Hetzner option

Hetzner Cloud VPS was not recommended because Hetzner Cloud does not support nested virtualization.

Hetzner Server Auction dedicated servers may work because they provide bare metal, but they are less convenient for short-lived testing.

## DigitalOcean, Hostinger, Railway, and Fly.io

### DigitalOcean

DigitalOcean was considered the only serious candidate among DigitalOcean, Hostinger, Railway, and Fly.io.

Reason:

- DigitalOcean Droplets may expose nested virtualization/KVM.
- Performance may be poor, so it needs validation.

Suggested sizes:

```text
Minimum to try: 2 vCPU / 4 GB RAM
Better:         4 vCPU / 8 GB RAM
```

Validate immediately after provisioning:

```bash
egrep -c '(vmx|svm)' /proc/cpuinfo
ls -l /dev/kvm
sudo apt update
sudo apt install -y cpu-checker
kvm-ok
```

If KVM is unavailable, destroy the Droplet.

### Hostinger

Hostinger was described as potentially cheap but uncertain.

Important distinction:

```text
A provider saying it uses KVM for VPS hosting does not guarantee nested KVM is exposed inside the guest VPS.
```

Question to ask Hostinger support:

```text
Does your VPS expose /dev/kvm and CPU vmx/svm flags inside the guest, so I can run nested KVM/QEMU or Android Emulator hardware acceleration?
```

Hostinger KVM 4 would be a better target than KVM 2 if support confirms nested KVM exposure.

### Railway

Railway was not recommended.

Reason:

- Railway is an app/container PaaS.
- It is useful for APIs, dashboards, orchestration, or logging services.
- It is not appropriate for running an Android emulator that needs privileged VM/KVM access.

### Fly.io

Fly.io was not recommended.

Reason:

- Fly Machines are useful for distributed services.
- Fly does not support nested virtualization.
- It could host supporting services, but not the emulator VM.

## Android SDK and emulator setup on the cloud VM

Install base packages:

```bash
sudo apt update
sudo apt install -y unzip openjdk-17-jdk qemu-kvm libvirt-daemon-system \
  libvirt-clients bridge-utils curl python3 python3-pip
```

Set SDK environment variables:

```bash
export ANDROID_HOME="$HOME/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
```

Install API 36 non-Play image, for example:

```bash
sdkmanager --install \
  "platform-tools" \
  "emulator" \
  "platforms;android-36" \
  "system-images;android-36;google_apis;x86_64"
```

Create the AVD:

```bash
avdmanager create avd \
  -n api36-mitm \
  -k "system-images;android-36;google_apis;x86_64" \
  -d pixel_8
```

Start the emulator:

```bash
emulator @api36-mitm \
  -no-window \
  -no-audio \
  -gpu swiftshader \
  -writable-system \
  -no-snapshot \
  -wipe-data
```

Prepare ADB root:

```bash
adb wait-for-device
adb root
adb wait-for-device
adb shell getprop ro.build.version.sdk
```

Expected SDK version:

```text
36
```

## mitmproxy setup

Install and start mitmproxy:

```bash
python3 -m pip install --user mitmproxy
export PATH="$HOME/.local/bin:$PATH"

mitmweb --listen-host 0.0.0.0 --listen-port 8080
```

Do not expose port `8080` to the public internet.

Verify generated CA files:

```bash
ls -l ~/.mitmproxy/
```

Expected files include:

```text
mitmproxy-ca-cert.pem
mitmproxy-ca.pem
```

## Configure Android to use the proxy

From the Android emulator, `10.0.2.2` points back to the emulator host.

Set the global Android proxy:

```bash
adb shell settings put global http_proxy 10.0.2.2:8080
adb shell am force-stop com.android.chrome || true
```

Open a site:

```bash
adb shell am start \
  -a android.intent.action.VIEW \
  -d https://example.com
```

Expected before installing the CA:

```text
Chrome shows a certificate/privacy/HSTS-style error.
```

This confirms the lab is reproducing the desired failure mode.

## Why not just use emulator `-http-proxy`?

The emulator `-http-proxy` flag is useful for routing, but it is not the best way to reproduce TLS inspection failures.

For this test, explicitly configuring Android's global proxy is clearer because it causes Android browser traffic to go through `mitmproxy`, which then generates the untrusted CA failure.

## Patch to test with mitmproxy CA directly

For cloud testing, the previous Mac Keychain script should support an explicit CA file input, because the CA to install is `~/.mitmproxy/mitmproxy-ca-cert.pem`.

Suggested CLI additions:

```python
ap.add_argument("--extra-ca-pem", action="append", default=[])
ap.add_argument("--skip-keychain", action="store_true")
```

Suggested certificate-loading logic:

```python
keychain_certs = []

if not args.skip_keychain:
    print("[mac] Dumping certificates from macOS Keychain...")
    keychain_certs.extend(dump_keychain_certs(args.verbose))
    print(f"[mac] Parsed {len(keychain_certs)} unique Keychain certificates.")

for pem_path in args.extra_ca_pem:
    text = pathlib.Path(pem_path).read_text(encoding="utf-8")
    for pem in extract_pems(text):
        keychain_certs.append(cert_meta(pem, verbose=args.verbose))

print(f"[certs] Loaded {len(keychain_certs)} candidate CA certificates.")
```

Also make the macOS `security` tool check conditional when `--skip-keychain` is used.

Run the script in the cloud VM:

```bash
python3 mac_to_android_corp_cas.py \
  --skip-keychain \
  --extra-ca-pem ~/.mitmproxy/mitmproxy-ca-cert.pem \
  --host example.com \
  --proxy 127.0.0.1:8080
```

Then restart Chrome and test again:

```bash
adb shell am force-stop com.android.chrome
adb shell am start \
  -a android.intent.action.VIEW \
  -d https://example.com
```

Expected after installing the CA:

```text
Chrome loads the HTTPS page through mitmproxy successfully.
```

## More realistic corporate-network simulation

After the explicit proxy test works, a more realistic topology could use mitmproxy transparent mode or a two-VM setup:

```text
Android emulator VM
   ↓ default route
Proxy/firewall VM running mitmproxy transparent mode
   ↓
Internet
```

But the explicit proxy setup should be tested first because it is much easier to debug.

## Cleanup commands

Remove Android proxy:

```bash
adb shell settings put global http_proxy :0
adb shell settings delete global http_proxy || true
```

Kill emulator:

```bash
adb emu kill
```

Stop mitmproxy:

```bash
pkill -f mitm
```

Delete the GCP VM when done:

```bash
gcloud compute instances delete android-mitm-lab --zone=us-central1-a
```

## Final recommendation

For lowest-friction testing:

```text
Use GCP n2-standard-2 Spot first.
Use GCP n2-standard-4 Spot if the emulator is unstable or slow.
```

For providers from the later comparison:

```text
DigitalOcean: worth testing if /dev/kvm is exposed.
Hostinger: only after support confirms nested KVM exposure.
Railway: not suitable for the emulator, but useful for support services.
Fly.io: not suitable because nested virtualization is not supported.
```
