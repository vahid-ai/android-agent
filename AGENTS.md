## Cursor Cloud specific instructions

- Prefer Firebase Test Lab for Android device validation in Cursor Cloud; local AVDs are not viable here because x86/x86_64 images require KVM and this VM does not expose `/dev/kvm`.
- Firebase Test Lab via `gcloud firebase test android run` submits APKs to managed devices and returns test results; it does not provide a live `adb`/`scrcpy` device connection. For interactive remote-device `adb`, use Android Device Streaming from Android Studio with a Firebase project that has direct-access permissions.
- Build the app and instrumentation APKs from `android-nav-automator` with Gradle, then submit `app/build/outputs/apk/debug/app-debug.apk` and `app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk` to Test Lab once `gcloud` is authenticated and a Firebase/GCP project is configured.
