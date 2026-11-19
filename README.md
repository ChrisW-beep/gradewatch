# Grade Watcher (unofficial)

Get a phone notification when your kids' grades change on a **Friday Parent Portal** (fridayparentportal.com) district site — new grades, grades going up 📈, grades going down 📉.

> **Unofficial.** Not affiliated with or endorsed by Realtime Information Technology or any school district. It logs in with *your own* parent account, once a day, and reads what you could see yourself. It may break if the portal changes. Use at your own risk and within your district's portal terms.

## How it works

- Runs on **your own Windows PC** — your password never leaves it (stored in Windows Credential Manager).
- Once a day (Windows Task Scheduler), it logs in, reads every linked student's **Gradebook** (or the report-card **Grades** page for elementary students), and compares with the last check.
- Changes are pushed to your phone with the free [ntfy](https://ntfy.sh) app. No changes = no notification.
- Gradebook students also get an alert for each **individual assignment** grade that is posted or changed.
- When a marking period ends, its **final grades** are sent once (also the first time it runs, for any marking period that already ended). Later corrections to an ended marking period still alert.
- First run sends one summary of all current grades.

Example alert:

```
📉 Avery | MP2 | AP World History: 89.25/ 89 → 85.00/ 85
🆕 Avery | MP2 | Geometry H | Quiz 5 - Triangles: 18 / 20
🏁 Jordan MP1 final grades:
  Spanish 1: 91.15/ 91
```

## Setup (parents)

1. Download `GradeWatcher.exe` from [Releases](../../releases) and double-click it.
   Windows may say *"Windows protected your PC"* because the app isn't code-signed — click **More info → Run anyway**.
2. Enter your **district** (the word after `fridayparentportal.com/` in your portal link — or paste the whole link), your portal **username** and **password**, and a check time. Click **Save and start**.
3. On your phone, install **ntfy** (App Store / Google Play), tap **+**, and subscribe to the channel name the app shows you.

Your PC needs to be on (or wake up) for the daily check. Run the app again any time to change settings.

> ⚠️ **Keep your channel name random.** ntfy.sh channels are public: anyone who guesses the name can read your alerts. The app generates a random one for you — don't change it to something like `smith-grades`.

## Troubleshooting

Files live in `%APPDATA%\GradeWatcher` (paste that into File Explorer):

- `run.log` — one line per check: `ok` or `FAILED - reason`.
- `error.log` — details of the last failure.
- `grades_<name>.html` — saved when a student's grades page couldn't be read. **It contains your child's info — don't post it publicly**; describe the layout or redact it when opening an issue.

To check right now: Task Scheduler → **GradeWatcher** → right-click → **Run**.

## Build from source

Requires Windows. Double-click `build.bat` (installs Python via winget if needed, then PyInstaller and dependencies). Output: `dist\GradeWatcher.exe`.

Run without building:

```powershell
pip install playwright keyring pywebview
python app.py              # setup window
python app.py --run        # one check
python grade_watch.py --test   # self-checks, no login
```

Command-line only (no app): set `PORTAL_DISTRICT`, `PORTAL_USER`, `PORTAL_PASS`, `NTFY_TOPIC` and run `python grade_watch.py` (`--show` prints what it reads without saving or alerting).

## Files

| File | What |
|---|---|
| `grade_watch.py` | Portal login, scraping, diff, ntfy push |
| `app.py` | Setup window, credential storage, scheduled task |
| `ui.html` | Setup window UI (Tailwind) |
| `build.bat` | Builds the single-file `.exe` |
| `icon.ico` | App icon |

## Limitations

- Tested on one district (Lacey Township, NJ). Other districts use the same portal software but may lay pages out differently — issues welcome.
- Individual assignments and end-of-marking-period summaries need the Gradebook page; students who only have the report-card Grades page get report-card grades only.
- Windows only.

## License

MIT
