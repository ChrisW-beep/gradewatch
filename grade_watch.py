"""Daily Friday Parent Portal grade watcher -> phone push via ntfy.sh.

Setup (once):
  pip install playwright && playwright install chromium
  Install the free ntfy app on your phone, subscribe to a hard-to-guess topic name.

Env vars:
  PORTAL_USER, PORTAL_PASS   your parent portal login
  NTFY_TOPIC                 your ntfy topic name
  PORTAL_DISTRICT            district name or portal link (default: lacey)

Run `python grade_watch.py --show` to log in and print what it scrapes (no alert, no save).
Run `python grade_watch.py --test` to check the diff logic without logging in.
"""
import json, os, re, sys, urllib.request
from pathlib import Path

BASE = "https://www.fridayparentportal.com/portal/"


def district_slug(s):
    """'lacey', 'Lacey ' or a pasted 'https://www.fridayparentportal.com/lacey/' -> 'lacey'"""
    m = re.search(r"fridayparentportal\.com/([^/?#\s]+)", s, re.I)
    return (m.group(1) if m else s).strip().strip("/").lower()

GRADEBOOK_URL = BASE + "index.cfm?f=gradebook.cfm"
GRADES_URL = BASE + "index.cfm?f=grades.cfm"
STATE = Path(__file__).with_name("grades_state.json")


def first_name(s):
    # header shows "Avery Jane Doe", switch list shows "Doe, Jordan"
    return (s.split(", ", 1)[1] if ", " in s else s).split()[0]


def scrape_gradebook(page):
    page.goto(GRADEBOOK_URL)
    page.wait_for_load_state("networkidle")
    body = page.inner_text("body")
    m = re.search(r"([A-Z][\w'.-]+(?: [A-Z][\w'.-]+)+)\s+Grade:\s*\d+", body)
    name = m.group(1) if m else "Unknown"
    try:
        mp = page.locator("select").first.input_value(timeout=3000)
    except Exception:
        mp = "?"
    rows = {}
    for tr in page.query_selector_all("tr"):
        cells = [c.inner_text().strip() for c in tr.query_selector_all("td")]
        # course rows look like: "English 9 Honors\nENE1HF / 02" | "100.00/ 100" | teacher
        # second line "ENE1HF / 02" marks a real course row (skips the outer "Current Average" row)
        if len(cells) >= 2 and re.match(r"\d+(\.\d+)?\s*/", cells[1]) and re.search(r"\n\s*\S+\s*/\s*\d+", cells[0]):
            rows[f"{first_name(name)} | MP{mp} | {cells[0].splitlines()[0]}"] = " ".join(cells[1].split())
    return name, rows


def scrape_grades_page(page, name):
    """Report-card style Grades page (elementary kids with no gradebook): Title | Teacher | MP1 | MP2 ...
    Returns (rows, course_count); blank MP cells are skipped so a grade showing up later alerts as new."""
    page.goto(GRADES_URL)
    page.wait_for_load_state("networkidle")
    for table in page.query_selector_all("table"):
        heads = [h.inner_text().strip() for h in table.query_selector_all(":scope > thead > tr > th, :scope > tbody > tr > th, :scope > tr > th")]
        mp_cols = [(i, h) for i, h in enumerate(heads) if re.fullmatch(r"MP\s*\d+", h)]
        if mp_cols:
            break
    else:
        return {}, 0
    rows, courses = {}, 0
    # direct child rows only: the ">" buttons expand hidden detail rows that must not be read as courses
    for tr in table.query_selector_all(":scope > tbody > tr, :scope > tr"):
        cells = [c.inner_text().strip() for c in tr.query_selector_all(":scope > td")]
        title = next((c for c in cells if re.search(r"\n\s*\S+\s*/\s*\d+", c)), None)
        if not title or len(cells) != len(heads):  # columns must line up with the headers exactly
            continue
        courses += 1
        for i, mp in mp_cols:
            # cells carry their column label and a comments note, e.g. "MP1 No Comments" when ungraded
            grade = re.sub(rf"^{re.escape(mp)}\b", "", " ".join(cells[i].split()))
            grade = re.sub(r"\bNo Comments\b", "", grade, flags=re.I).strip()
            if re.search(r"[A-Za-z0-9]", grade):  # blank, "-" or &nbsp; means not graded yet
                rows[f"{first_name(name)} | {mp} | {title.splitlines()[0]}"] = grade
    return rows, courses


def switch_to_next(page, seen):
    """Open Link/Switch Students and click 'Change to Student' for a kid not yet scraped."""
    # menu item lives in the hidden hamburger dropdown; dispatch the click instead of opening the menu
    link = page.get_by_text("Link/Switch Students").first
    if not link.count():  # account with a single student
        return False
    link.dispatch_event("click")
    page.wait_for_load_state("networkidle")
    names = re.findall(r"^\s*([^\n,]+, [^\n\t]+?)\s*(?:\t|\n)+\s*Change to Student", page.inner_text("body"), re.M)
    for i, n in enumerate(names):
        if first_name(n) not in seen:
            page.get_by_text("Change to Student").nth(i).click()
            page.wait_for_load_state("networkidle")
            return True
    return False


def scrape():
    from playwright.sync_api import sync_playwright
    rows, empty, seen = {}, [], set()
    with sync_playwright() as p:
        # Edge ships with Windows, so the packaged app needs no browser download
        page = p.chromium.launch(channel="msedge" if os.name == "nt" else None).new_page()
        district = district_slug(os.environ.get("PORTAL_DISTRICT", "lacey"))
        page.goto(f"https://www.fridayparentportal.com/{district}")  # sets the district, then redirects to login
        if "nodistrict" in page.url.lower():
            raise RuntimeError(f"No Friday Parent Portal district named '{district}'")
        try:
            page.fill("input[name=username]", os.environ["PORTAL_USER"], timeout=15000)
        except Exception:
            page.screenshot(path=str(STATE.with_name("login_debug.png")), full_page=True)
            raise RuntimeError("Login form not found - see login_debug.png")
        page.fill("input[name=password]", os.environ["PORTAL_PASS"])
        page.press("input[name=password]", "Enter")
        page.wait_for_load_state("networkidle")
        for _ in range(10):  # guard against looping forever if switching silently fails
            name, kid_rows = scrape_gradebook(page)
            if first_name(name) in seen:
                break
            seen.add(first_name(name))
            if not kid_rows:
                kid_rows, courses = scrape_grades_page(page, name)
                if not courses:  # neither page had courses; keep the HTML so the layout can be diagnosed
                    STATE.with_name(f"grades_{first_name(name)}.html").write_text(page.content(), encoding="utf-8")
                    empty.append(name)
            rows.update(kid_rows)
            if not switch_to_next(page, seen):
                break
    return rows, empty


def first_num(s):
    m = re.search(r"\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def diff(old, new):
    msgs = []
    for k, v in new.items():
        if k not in old:
            msgs.append(f"🆕 {k}: {v}")
        elif old[k] != v:
            a, b = first_num(old[k]), first_num(v)
            arrow = "📈" if a is not None and b is not None and b > a else "📉" if a is not None and b is not None and b < a else "✏️"
            msgs.append(f"{arrow} {k}: {old[k]} → {v}")
    return msgs


def notify(text):
    req = urllib.request.Request(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}",
                                 data=text.encode(), headers={"Title": "Grade update"})
    urllib.request.urlopen(req, timeout=30)


def build(old, new, empty):
    """Returns (state_to_save, messages). A kid whose page failed keeps last run's grades
    so a scrape glitch never wipes them or fires fake 'new' alerts next time."""
    failed = {first_name(e) for e in empty}
    state = {**{k: v for k, v in (old or {}).items() if k.split(" | ")[0] in failed}, **new}
    msgs = [f"⚠️ Couldn't read grades for {e} - page layout may have changed" for e in empty]
    if old is None:
        msgs += ["📋 Grade watcher is set up. Current grades:"] + [f"{k}: {v}" for k, v in new.items()]
    else:
        msgs += diff(old, state)
    return state, msgs


def main():
    old = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None
    new, empty = scrape()
    state, msgs = build(old, new, empty)
    if msgs:
        notify("\n".join(msgs)[:3500])
    if new:  # nothing scraped at all = login/site problem; keep the old snapshot
        STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if "--test" in sys.argv:
        assert district_slug("Lacey ") == district_slug("https://www.fridayparentportal.com/lacey/") == "lacey"
        assert first_name("Doe, Jordan") == "Jordan" and first_name("Avery Jane Doe") == "Avery"
        o = {"Avery | MP1 | Geometry H": "96.70/ 97", "Avery | MP1 | AP World History": "89.25/ 89", "Sam | MP1 | Art": "A"}
        n = {"Avery | MP1 | Geometry H": "98.10/ 98", "Avery | MP1 | AP World History": "85.00/ 85", "Sam | MP1 | Art": "B",
             "Jordan | MP1 | Spanish 1": "91.15/ 91"}
        out = diff(o, n)
        assert [s[0] for s in out] == ["📈", "📉", "✏", "🆕"], out
        # Sam's page failed: their old grade is kept, only a warning goes out, no fake "new" next run
        state, msgs = build(o, {"Avery | MP1 | Geometry H": "96.70/ 97", "Avery | MP1 | AP World History": "89.25/ 89"}, ["Sam Lee Doe"])
        assert state["Sam | MP1 | Art"] == "A" and len(msgs) == 1 and msgs[0].startswith("⚠️"), msgs
        # first run sends the full summary
        _, msgs = build(None, {"Jordan | MP1 | Art 7": "100.00/ 100"}, [])
        assert msgs[0].startswith("📋") and "Art 7" in msgs[1], msgs
        print("ok", out)
    elif "--show" in sys.argv:
        rows, empty = scrape()
        print(json.dumps(rows, indent=1), "\nno grades for:", empty)
    else:
        main()
