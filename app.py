"""Grade Watcher - double-click setup for parents.

GradeWatcher.exe          -> setup window (run again any time to change settings)
GradeWatcher.exe --run    -> one silent check (what the daily scheduled task runs)
python app.py --test      -> self-check, no login
"""
import json, os, re, secrets, subprocess, sys, traceback
from pathlib import Path

import grade_watch

APP = "GradeWatcher"
DATA = Path(os.environ.get("APPDATA", Path.home())) / APP
CONFIG = DATA / "config.json"
grade_watch.STATE = DATA / "grades_state.json"  # an exe's own folder is temporary, so keep state here


def load():
    return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}


def validate(district, user, password, topic, time):
    if not re.fullmatch(r"[a-z0-9_-]{2,40}", district):
        return "Enter your district name - the word after fridayparentportal.com/ in your portal link."
    if not user or not password:
        return "Enter your parent portal username and password."
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", topic):
        return "The alert channel name must be 8+ letters, numbers or dashes."
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time):
        return "Enter the check time as HH:MM in 24-hour time, e.g. 16:00."
    return None


def ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


def schedule_cmd(time):
    if getattr(sys, "frozen", False):  # the packaged .exe runs itself
        exe, args = sys.executable, "--run"
    else:
        exe, args = sys.executable, f'"{Path(__file__).resolve()}" --run'
    # StartWhenAvailable: a laptop asleep at check time catches up when it wakes
    ps = (f"Register-ScheduledTask -TaskName {APP} -Force "
          f"-Trigger (New-ScheduledTaskTrigger -Daily -At {ps_quote(time)}) "
          f"-Action (New-ScheduledTaskAction -Execute {ps_quote(exe)} -Argument {ps_quote(args)}) "
          f"-Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)")
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]


def run_check():
    """One check. Returns an error message, or None on success."""
    from datetime import datetime
    DATA.mkdir(parents=True, exist_ok=True)
    log = lambda msg: open(DATA / "run.log", "a", encoding="utf-8").write(f"{datetime.now():%Y-%m-%d %H:%M} {msg}\n")
    try:
        import keyring
        cfg = load()
        if not cfg:
            raise RuntimeError("Not set up yet - open Grade Watcher and click Save and start")
        os.environ.update(PORTAL_USER=cfg["user"], NTFY_TOPIC=cfg["topic"], PORTAL_DISTRICT=cfg.get("district", "lacey"),
                          PORTAL_PASS=keyring.get_password(APP, cfg["user"]) or "")
        grade_watch.main()
        state = json.loads(grade_watch.STATE.read_text(encoding="utf-8")) if grade_watch.STATE.exists() else {}
        log(f"ok - {len(state)} grades on file")
        return None
    except Exception as e:
        log(f"FAILED - {e}")
        (DATA / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        try:  # the scheduled run has no window, so the phone is the only place to say it failed
            grade_watch.notify(f"⚠️ Grade Watcher couldn't check grades: {e}"[:500])
        except Exception:
            pass
        return str(e)


class Api:
    """Called from ui.html via window.pywebview.api.* (each call runs off the UI thread)."""

    def get_config(self):
        import keyring
        cfg = load()
        return {"district": cfg.get("district", ""), "user": cfg.get("user", ""), "topic": cfg.get("topic") or "grades-" + secrets.token_hex(6),
                "time": cfg.get("time", "16:00"),
                "has_password": bool(cfg.get("user") and keyring.get_password(APP, cfg["user"]))}

    def save(self, form):
        import keyring
        user, topic, time = (form.get(k, "").strip() for k in ("user", "topic", "time"))
        district = grade_watch.district_slug(form.get("district", ""))
        password = form.get("password") or (keyring.get_password(APP, user) if user else "") or ""
        err = validate(district, user, password, topic, time)
        if err:
            return {"error": err}
        DATA.mkdir(parents=True, exist_ok=True)
        keyring.set_password(APP, user, password)  # Windows Credential Manager, not a plain file
        CONFIG.write_text(json.dumps({"district": district, "user": user, "topic": topic, "time": time}), encoding="utf-8")
        r = subprocess.run(schedule_cmd(time), capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode:
            return {"error": "Couldn't create the daily check: " + (r.stderr or r.stdout)[-500:]}
        err = run_check()
        return {"error": f"{err} - check your district, username and password, then try again."} if err else {"ok": True}


def ui_path():
    # PyInstaller unpacks bundled files to sys._MEIPASS
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "ui.html"


def setup_window():
    import webview
    webview.create_window("Grade Watcher", str(ui_path()), js_api=Api(), width=520, height=720, resizable=False)
    webview.start()

if __name__ == "__main__":
    if "--test" in sys.argv:
        assert validate("lacey", "u", "p", "grades-abc123", "16:00") is None
        assert validate("lacey", "", "p", "grades-abc123", "16:00")
        assert validate("lacey", "u", "p", "short", "16:00") and validate("lacey", "u", "p", "bad topic!!", "16:00")
        assert validate("lacey", "u", "p", "grades-abc123", "4pm") and validate("lacey", "u", "p", "grades-abc123", "24:00")
        assert validate("bad district!", "u", "p", "grades-abc123", "16:00") and validate("", "u", "p", "grades-abc123", "16:00")
        assert ps_quote("C:\\Users\\O'Brien\\a.exe") == "'C:\\Users\\O''Brien\\a.exe'"
        cmd = schedule_cmd("07:30")[-1]
        assert "-At '07:30'" in cmd and "-StartWhenAvailable" in cmd and "--run" in cmd, cmd
        print("ok")
    elif "--run" in sys.argv:
        sys.exit(1 if run_check() else 0)
    else:
        setup_window()
