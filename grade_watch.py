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
from urllib.parse import urljoin
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


def scrape_gradebook(page, want_mp=None):
    """Returns (student name, marking period shown, course rows, {course key: assignments page link}).
    want_mp switches the page to that marking period first; the portal remembers the choice for the session."""
    page.goto(GRADEBOOK_URL)
    page.wait_for_load_state("networkidle")
    if want_mp and page.locator("select[name=selectMP]").input_value(timeout=3000) != str(want_mp):
        with page.expect_navigation():  # the dropdown submits its form on change
            page.select_option("select[name=selectMP]", str(want_mp))
        page.wait_for_load_state("networkidle")
    body = page.inner_text("body")
    m = re.search(r"([A-Z][\w'.-]+(?: [A-Z][\w'.-]+)+)\s+Grade:\s*\d+", body)
    name = m.group(1) if m else "Unknown"
    try:
        mp = page.locator("select").first.input_value(timeout=3000)
    except Exception:
        mp = "?"
    rows, links = {}, {}
    for tr in page.query_selector_all("tr"):
        cells = [c.inner_text().strip() for c in tr.query_selector_all("td")]
        # course rows look like: "English 9 Honors\nENE1HF / 02" | "100.00/ 100" | teacher
        # second line "ENE1HF / 02" marks a real course row (skips the outer "Current Average" row)
        if len(cells) >= 2 and re.match(r"\d+(\.\d+)?\s*/", cells[1]) and re.search(r"\n\s*\S+\s*/\s*\d+", cells[0]):
            key = f"{first_name(name)} | MP{mp} | {cells[0].splitlines()[0]}"
            rows[key] = " ".join(cells[1].split())
            a = tr.query_selector("a[href*='gradebookdetail.cfm']")
            if a:
                links[key] = a.get_attribute("href")
    return name, mp, rows, links


def scrape_assignments(page, href, course_key):
    """One course's assignments page (for the marking period the session is on).
    Ungraded assignments are skipped so a grade showing up later alerts as new."""
    page.goto(urljoin(BASE, href))
    page.wait_for_load_state("networkidle")
    # each assignment row may have a ">" button that expands a hidden row holding its description
    found = page.eval_on_selector_all("#mainTable > tbody > tr:not(.collapse):not(.table-row-subheader)", """trs => trs.map(tr => {
        const b = tr.querySelector('button[data-target]');
        const d = b && document.querySelector(b.dataset.target + ' td.text-left');
        return {id: b ? b.dataset.target : '', desc: d ? d.textContent : '', cells: [...tr.cells].map(c => c.innerText)};
    })""")
    rows = {}
    for f in found:
        c = [" ".join(x.split()) for x in f["cells"]]
        if len(c) < 4 or not re.search(r"[A-Za-z0-9]", c[2]):  # c = title, due date, grade, total points
            continue
        desc = " ".join(f["desc"].split())
        label = f"{c[0]} - {desc}" if desc and desc != c[0] else c[0]
        # same title twice in one course: tell them apart by due date, then by the portal's row id
        for extra in ("", f" ({c[1]})", f" ({c[1]}) {f['id']}"):
            key = f"{course_key} | {label}{extra}"
            if key not in rows:
                break
        rows[key] = f"{c[2]} / {c[3]}" if c[3] else c[2]
    return rows


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
    rows, empty, seen, current, cur = {}, [], set(), {}, None
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
            name, mp, kid_rows, _ = scrape_gradebook(page)
            if first_name(name) in seen:
                break
            seen.add(first_name(name))
            if cur is None and mp.isdigit():  # only the first load after login shows the real current MP
                cur = int(mp)
            if kid_rows and cur:
                current[first_name(name)] = cur
                kid_rows = {}
                for m in range(1, cur + 1):  # earlier marking periods too, so their final grades get reported
                    _, _, mp_rows, links = scrape_gradebook(page, m)
                    kid_rows.update(mp_rows)
                    for course_key, href in links.items():
                        kid_rows.update(scrape_assignments(page, href, course_key))
            if not kid_rows:
                kid_rows, courses = scrape_grades_page(page, name)
                if not courses:  # neither page had courses; keep the HTML so the layout can be diagnosed
                    STATE.with_name(f"grades_{first_name(name)}.html").write_text(page.content(), encoding="utf-8")
                    empty.append(name)
            rows.update(kid_rows)
            if not switch_to_next(page, seen):
                break
    return rows, empty, current


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


def chunks(lines, limit=3500):
    """ntfy rejects messages over 4096 bytes, so long updates go out as several notifications."""
    out, size = [[]], 0
    for line in lines:
        line = line.encode()[:limit].decode(errors="ignore")
        n = len(line.encode()) + 1
        if out[-1] and size + n > limit:
            out.append([])
            size = 0
        out[-1].append(line)
        size += n
    return ["\n".join(c) for c in out if c]


def notify(text):
    for part in chunks(text.split("\n")):
        req = urllib.request.Request(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}",
                                     data=part.encode(), headers={"Title": "Grade update"})
        urllib.request.urlopen(req, timeout=30)


FINALS = "_finals_sent"  # state key listing the "Kid | MP1" marking periods whose final grades were already sent


def is_assignment(k):
    return k.count(" | ") > 2  # "Kid | MP1 | Course | Assignment" vs "Kid | MP1 | Course"


def build(old, new, empty, current=None):
    """Returns (state_to_save, messages). current = {kid: current marking period number}.
    Grades missing from this run are kept from the last one, so a scrape glitch never wipes them
    or fires fake 'new' alerts next time."""
    current = current or {}
    prev = {k: v for k, v in (old or {}).items() if k != FINALS}
    sent = set((old or {}).get(FINALS, []))
    for kid, cur in current.items():
        # on file for a later MP than the current one = last school year; start that kid fresh
        if any(int(m.group(1)) > cur for k in prev if (m := re.match(rf"{re.escape(kid)} \| MP(\d+) \|", k))):
            prev = {k: v for k, v in prev.items() if not k.startswith(kid + " | ")}
            sent = {s for s in sent if not s.startswith(kid + " | ")}
    state = {**prev, **new}
    msgs = [f"⚠️ Couldn't read grades for {e} - page layout may have changed" for e in empty]
    finals, covered = [], set()
    for kid, cur in current.items():
        for m in range(1, cur):  # marking periods that have ended
            tag = f"{kid} | MP{m}"
            rows = {k: v for k, v in new.items() if k.startswith(tag + " | ") and not is_assignment(k)}
            if rows and tag not in sent:
                finals += [f"🏁 {kid} MP{m} final grades:"] + [f"  {k.split(' | ', 2)[2]}: {v}" for k, v in rows.items()]
                sent.add(tag)
                covered |= set(rows)
    if old is None:
        msgs += ["📋 Grade watcher is set up. Current grades:"]
        msgs += [f"{k}: {v}" for k, v in new.items() if k not in covered and not is_assignment(k)]
    else:
        # first run with assignments: record them quietly instead of announcing every old one as new
        seeding = not any(is_assignment(k) for k in prev)
        msgs += diff(prev, {k: v for k, v in state.items() if k not in covered and not (seeding and is_assignment(k))})
        if seeding and any(is_assignment(k) for k in new):
            msgs.append(f"📝 Now also watching individual assignments ({sum(map(is_assignment, new))} graded so far)")
    state[FINALS] = sorted(sent)
    return state, msgs + finals


def main():
    old = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None
    new, empty, current = scrape()
    state, msgs = build(old, new, empty, current)
    if msgs:
        notify("\n".join(msgs))
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
        # MP1 ended (now MP2) and its grades were never sent: one final summary, sent once
        o = {"Avery | MP2 | Geometry H": "90.00/ 90"}
        n = {"Avery | MP1 | Geometry H": "96.70/ 97", "Avery | MP2 | Geometry H": "90.00/ 90",
             "Avery | MP1 | Geometry H | Quiz 1 - Angles": "95", "Avery | MP2 | Geometry H | Quiz 5": "18 / 20"}
        state, msgs = build(o, n, [], {"Avery": 2})
        assert msgs == ["📝 Now also watching individual assignments (2 graded so far)",
                        "🏁 Avery MP1 final grades:", "  Geometry H: 96.70/ 97"], msgs
        assert state[FINALS] == ["Avery | MP1"]
        # next day: finals not repeated; a new assignment, a changed one and a late MP1 correction alert
        n2 = {**n, "Avery | MP2 | Geometry H | Quiz 6": "100", "Avery | MP2 | Geometry H | Quiz 5": "20 / 20",
              "Avery | MP1 | Geometry H": "97.00/ 97"}
        state2, msgs = build(state, n2, [], {"Avery": 2})
        assert sorted(s[0] for s in msgs) == sorted(["🆕", "📈", "📈"]), msgs
        # a course page that failed to load keeps its assignments on file (no fake "new" later)
        state3, msgs = build(state2, {"Avery | MP2 | Geometry H": "90.00/ 90"}, [], {"Avery": 2})
        assert not msgs and state3["Avery | MP2 | Geometry H | Quiz 6"] == "100", msgs
        # new school year (back to MP1): last year's grades and sent-markers are dropped
        state4, msgs = build(state3, {"Avery | MP1 | Algebra 2": "88.00/ 88"}, [], {"Avery": 1})
        assert state4 == {"Avery | MP1 | Algebra 2": "88.00/ 88", FINALS: []} and msgs[0].startswith("🆕"), (state4, msgs)
        # first run mid-year: current grades, then finals for the ended MP, no assignment flood
        _, msgs = build(None, n, [], {"Avery": 2})
        assert msgs == ["📋 Grade watcher is set up. Current grades:", "Avery | MP2 | Geometry H: 90.00/ 90",
                        "🏁 Avery MP1 final grades:", "  Geometry H: 96.70/ 97"], msgs
        parts = chunks(["📈 " + "x" * 1000] * 8)
        assert len(parts) == 3 and all(len(p.encode()) <= 3500 for p in parts), [len(p) for p in parts]
        print("ok", out)
    elif "--show" in sys.argv:
        rows, empty, current = scrape()
        print(json.dumps(rows, indent=1), "\nno grades for:", empty, "\ncurrent marking period:", current)
    else:
        main()
