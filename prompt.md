Appium Inspector is great for ad-hoc XML capture but it's not really a "recorder" in the sense you want. Let me lay out exactly what it can and can't do, then how to actually use it for your goal.
What Appium Inspector is
A desktop GUI (Electron app) that connects to an Appium server, which in turn drives your emulator via UiAutomator2. The inspector mirrors the device screen on the left, shows the current accessibility hierarchy as a tree on the right, and lets you click elements to inspect their attributes (resource-id, text, bounds, class, etc.).
The two interaction modes

Inspect mode (default) — you click in the mirrored screen in the inspector, and it highlights the corresponding node in the tree. The action is not sent to the device. This is pure inspection.
Tap/Send Keys mode — you toggle this and now clicks/taps in the mirror are forwarded to the device. The screen updates, and you hit the Refresh button (or it auto-refreshes depending on settings) to re-pull the hierarchy.

So the workflow you actually want — drive the app, capture XML at each step — works like this:
Setup (one time)

Install Appium server: npm install -g appium then appium driver install uiautomator2.
Install Appium Inspector (download from the appium-inspector releases).
Start the server: appium (default port 4723).
Boot your emulator.

Connect
In the inspector, set capabilities:
json{
  "platformName": "Android",
  "appium:automationName": "UiAutomator2",
  "appium:deviceName": "emulator-5554",
  "appium:appPackage": "com.your.app",
  "appium:appActivity": "com.your.app.MainActivity",
  "appium:noReset": true
}
Or omit appPackage/appActivity to attach to whatever's currently focused. Hit "Start Session."
Capture loop
For each screen you want to record:

Make sure you're in Tap mode (the hand-cursor toggle in the toolbar).
Tap/swipe/type in the mirrored screen to navigate.
When you land on a screen you want to capture: click the "Save Source" button (or copy from the Source panel on the right). It saves the current XML hierarchy.
Optionally screenshot via the camera icon.
Repeat.

Built-in "Recorder" feature — Appium Inspector has a recorder (the red dot in the toolbar) but it does the Espresso-recorder thing in reverse: as you tap, it generates Appium client code (Python/Java/JS) for the actions, not a sequence of XML hierarchies. Useful if you wanted a replayable script, but not what you're after.
Why this still isn't quite what you want
The fundamental limitation: you're driving via the inspector's mirror, not touching the device directly. Three friction points:

Mirror lag and tap precision. The mirrored view is a screencap stream; gestures don't always feel right, complex swipes are annoying, and games/animations are basically unusable.
Manual save per screen. No auto-capture-on-change. You have to remember to save source at each interesting state.
One-at-a-time, GUI-bound. Each capture is a button click in a desktop app. Not scriptable, not headless, doesn't fit in a benchmark pipeline.

The pragmatic recipe for your use case
If you want manual driving with auto-capture, combine two things:

Drive the device directly with your hands using scrcpy (mirrors the emulator with native-feeling input — much better than Appium Inspector's mirror).
Run Appium server in the background and have a small Python script poll Appium's GET /session/:id/source endpoint on screen-change, saving each unique XML.

That gives you the same experience as my original recorder script, but the XML comes from Appium's UiAutomator2 server (HTTP) instead of adb shell uiautomator dump — which means no idle-state errors, faster dumps, and structured output ready for the agent's worldview.
Roughly:
pythonimport requests, hashlib, time
SID = "your-session-id"  # from Appium when you start a session
BASE = f"http://localhost:4723/session/{SID}"
seen = set()
while True:
    xml = requests.get(f"{BASE}/source").json()["value"]
    h = hashlib.sha1(xml.encode()).hexdigest()[:10]
    if h not in seen:
        seen.add(h)
        open(f"capture_{h}.xml", "w").write(xml)
        # also screenshot:
        png_b64 = requests.get(f"{BASE}/screenshot").json()["value"]
        # decode + save
    time.sleep(0.5)
You'd start an Appium session once (programmatically or via the inspector), then run this loop while you drive the emulator with scrcpy. Best of both worlds.
Honest take for your project
 For systematic capture of human traces to feed your benchmark, the script-based approach (whether against adb, Appium's HTTP, or droidrun-portal's /state) is what scales. Appium Inspector earns a place in your debugging toolbox; it's not the recorder.