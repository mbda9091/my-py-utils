# mac-cache-cleaner

Clean common macOS build caches for Android, iOS, Harmony, and KMP development.

The command always runs every cleanup task. It prints progress and the result of each task,
including failure reasons, and keeps going after individual failures.

```bash
uv run --package mac-cache-cleaner mac-cache-cleaner
```

Each run also writes a detailed log file under:

```text
~/Library/Logs/mac-cache-cleaner/
```

The terminal output starts with the exact log path:

```text
[LOG] /Users/you/Library/Logs/mac-cache-cleaner/20260702-153000.log
```

Sections:

- Android: Gradle, Maven, lldb, kwaipilot, Android Studio vcs/index caches
- iOS: gundam cache command/static libs, CocoaPods, Xcode DerivedData, lldb
- Harmony: hvigor, lldb, ohpm, npm, DevEco Studio caches
- KMP: Kotlin/Native compiler cache

Android Studio cache directories are matched as `AndroidStudio*` so the command works across
versions without hard-coding one IDE release.
