import calendar as pycal
import os
import sqlite3
from datetime import date, datetime, timedelta

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

import calendar_data as cal

app = Flask(__name__)
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")
TARGET = 75  # minimum attendance percentage
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
LUNCH_AFTER = 4  # lunch sits between period 4 and period 5


# ---------- small helpers ----------

def fmt_time(t):
    return f"{t.hour % 12 or 12}:{t.minute:02d}"


def fmt_day(d):
    return f"{d.day} {d.strftime('%b')}"


def format_range(start, end):
    if start == end:
        return d_long(start)
    return f"{fmt_day(start)} to {fmt_day(end)}"


def d_long(d):
    return f"{d.strftime('%a')}, {fmt_day(d)}"


def plural(n, word, suffix="es"):
    return f"{n} {word}" if n == 1 else f"{n} {word}{suffix}"


# ---------- database ----------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_auto_subject(db, name):
    """Make sure a timetable subject exists as an attendance subject."""
    row = db.execute("SELECT id, auto, attended, total FROM subjects WHERE name = ?", (name,)).fetchone()
    if row is None:
        db.execute("INSERT INTO subjects (name, auto) VALUES (?, 1)", (name,))
    elif not row[1]:
        db.execute("UPDATE subjects SET auto = 1, missed = ? WHERE id = ?", (max(row[3] - row[2], 0), row[0]))


def load_preset(db, key):
    preset = cal.TIMETABLE_PRESETS[key]
    for day, first, last, subject, tracked in preset["classes"]:
        db.execute(
            "INSERT INTO timetable (day, start_period, end_period, subject, tracked) VALUES (?, ?, ?, ?, ?)",
            (day, first, last, subject, 1 if tracked else 0),
        )
        if tracked:
            ensure_auto_subject(db, subject)


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subjects (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT NOT NULL UNIQUE,
            attended INTEGER NOT NULL DEFAULT 0,   -- manual subjects only
            total    INTEGER NOT NULL DEFAULT 0,   -- manual subjects only
            auto     INTEGER NOT NULL DEFAULT 0,   -- 1 = classes counted from the timetable
            missed   INTEGER NOT NULL DEFAULT 0    -- timetable subjects: classes you missed
        );
        CREATE TABLE IF NOT EXISTS assignments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL,
            title      TEXT NOT NULL,
            deadline   TEXT NOT NULL,          -- ISO date: YYYY-MM-DD
            done       INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS timetable (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            day          INTEGER NOT NULL,     -- Monday = 0 ... Saturday = 5
            start_period INTEGER NOT NULL,
            end_period   INTEGER NOT NULL,
            subject      TEXT NOT NULL,
            tracked      INTEGER NOT NULL DEFAULT 1
        );
        """
    )

    # Upgrade a database made by an earlier version of the app.
    cols = {r[1] for r in db.execute("PRAGMA table_info(subjects)")}
    if "auto" not in cols:
        db.execute("ALTER TABLE subjects ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")
    if "missed" not in cols:
        db.execute("ALTER TABLE subjects ADD COLUMN missed INTEGER NOT NULL DEFAULT 0")

    # The previous version stored the CS&IT-V timetable in code. Move it into the database.
    had_old_timetable = db.execute("SELECT 1 FROM settings WHERE key = 'timetable_seeded'").fetchone()
    has_rows = db.execute("SELECT 1 FROM timetable LIMIT 1").fetchone()
    if had_old_timetable and not has_rows:
        load_preset(db, "csit-5")
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('semester', '5')")
    if had_old_timetable:
        db.execute("DELETE FROM settings WHERE key = 'timetable_seeded'")
    db.commit()
    db.close()


def get_setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    db.commit()


def get_name():
    return get_setting("name")


def get_semester():
    try:
        n = int(get_setting("semester", cal.DEFAULT_SEMESTER))
    except ValueError:
        n = cal.DEFAULT_SEMESTER
    return n if n in cal.SEMESTERS else cal.DEFAULT_SEMESTER


def get_sem():
    return cal.SEMESTERS[get_semester()]


def reset_semester_data(db):
    """Switching semester clears the timetable and the subjects built from it."""
    db.execute("DELETE FROM assignments WHERE subject_id IN (SELECT id FROM subjects WHERE auto = 1)")
    db.execute("DELETE FROM subjects WHERE auto = 1")
    db.execute("DELETE FROM timetable")


# ---------- calendar logic ----------

def event_on(day, sem):
    """Return (name, kind) if a holiday / training / MST / exam covers this date."""
    for name, start, end, kind in sem["events"]:
        if start <= day <= end:
            return name, kind
    return None


def is_off_saturday(day, sem):
    return day.weekday() == 5 and ((day.day - 1) // 7 + 1) in sem["off_saturdays"]


def is_class_day(day, sem):
    if day < sem["start"] or day > sem["end"]:
        return False
    if day.weekday() == 6 or is_off_saturday(day, sem):
        return False
    return event_on(day, sem) is None


def teaching_numbers(sem):
    """date -> teaching day number, the same numbers printed on the academic calendar."""
    numbers, n, day = {}, 0, sem["start"]
    while day <= sem["end"]:
        if is_class_day(day, sem):
            n += 1
            numbers[day] = n
        day += timedelta(days=1)
    return numbers


def upcoming_events(sem, limit=None):
    today = date.today()
    out = []
    for name, start, end, kind in sorted(sem["events"], key=lambda e: e[1]):
        if end < today:
            continue
        if start <= today:
            when, days = "Happening now", 0
        else:
            days = (start - today).days
            when = "Tomorrow" if days == 1 else f"In {days} days"
        out.append({"name": name, "kind": kind, "when": when, "days": days, "range": format_range(start, end)})
    return out[:limit] if limit else out


# ---------- timetable logic ----------

def timetable_rows():
    return get_db().execute("SELECT * FROM timetable ORDER BY day, start_period").fetchall()


def get_schedule():
    """subject -> list of datetimes at which each of its classes ends (this semester)."""
    if "schedule" in g:
        return g.schedule
    sem = get_sem()
    by_day = {}
    for e in timetable_rows():
        if e["tracked"]:
            by_day.setdefault(e["day"], []).append(e)
    schedule = {}
    day = sem["start"]
    while day <= sem["end"]:
        if is_class_day(day, sem):
            for e in by_day.get(day.weekday(), []):
                end_t = cal.PERIOD_TIMES[e["end_period"]][1]
                schedule.setdefault(e["subject"], []).append(datetime.combine(day, end_t))
        day += timedelta(days=1)
    g.schedule = schedule
    return schedule


def timetable_grid(rows):
    """Rows of cells for the weekly table. None means 'covered by a taller cell above'."""
    starts = {day: {} for day in range(6)}
    covered = {day: set() for day in range(6)}
    for e in rows:
        starts[e["day"]][e["start_period"]] = e
        for p in range(e["start_period"] + 1, e["end_period"] + 1):
            covered[e["day"]].add(p)

    grid = []
    for p in range(1, 7):
        cells = []
        for day in range(6):
            if p in covered[day]:
                cells.append(None)
            elif p in starts[day]:
                e = starts[day][p]
                span = e["end_period"] - e["start_period"] + 1
                cls = "block" if span > 1 else "single"
                if not e["tracked"]:
                    cls = "untracked"
                cells.append({"subject": e["subject"], "span": span, "cls": cls})
            else:
                cells.append({"subject": "", "span": 1, "cls": "empty"})
        start_t, end_t = cal.PERIOD_TIMES[p]
        grid.append({"period": p, "time": f"{fmt_time(start_t)} to {fmt_time(end_t)}", "cells": cells})
    return grid


def todays_classes(sem, now=None):
    """(list of today's classes, reason string if there are none)."""
    now = now or datetime.now()
    today = now.date()
    rows = timetable_rows()
    if not rows:
        return [], "Add your timetable to see today's classes."
    if today < sem["start"]:
        return [], f"Classes start on {d_long(sem['start'])}."
    if today > sem["end"]:
        return [], "Teaching for this semester is over."
    ev = event_on(today, sem)
    if ev:
        return [], f"No classes today: {ev[0]}."
    if today.weekday() == 6:
        return [], "No classes today. It's Sunday."
    if is_off_saturday(today, sem):
        return [], "No classes today. It's an off Saturday."

    items = []
    for e in rows:
        if e["day"] != today.weekday():
            continue
        start_t = cal.PERIOD_TIMES[e["start_period"]][0]
        end_t = cal.PERIOD_TIMES[e["end_period"]][1]
        if now >= datetime.combine(today, end_t):
            status = "done"
        elif now >= datetime.combine(today, start_t):
            status = "now"
        else:
            status = "next"
        items.append({
            "subject": e["subject"],
            "time": f"{fmt_time(start_t)} to {fmt_time(end_t)}",
            "status": status,
            "tracked": bool(e["tracked"]),
        })
    if not items:
        return [], "Nothing on your timetable today."
    return items, None


# ---------- deadline logic ----------

def days_left(deadline_str):
    return (date.fromisoformat(deadline_str) - date.today()).days


def describe_days(n):
    if n < 0:
        return f"Overdue by {-n} day{'s' if n != -1 else ''}"
    if n == 0:
        return "Due today"
    if n == 1:
        return "Due tomorrow"
    return f"Due in {n} days"


def badge_text(n):
    if n < 0:
        return f"{-n}d late"
    if n == 0:
        return "Today"
    if n == 1:
        return "1 day"
    return f"{n} days"


def urgency(n):
    if n <= 0:
        return "urgent"
    if n <= 2:
        return "soon"
    return "later"


def deadline_note(deadline, sem):
    """Warn when a deadline clashes with the academic calendar."""
    ev = event_on(deadline, sem)
    if ev:
        return f"Falls during {ev[0]}"
    if deadline.weekday() == 6:
        return "Falls on a Sunday"
    if is_off_saturday(deadline, sem):
        return "Falls on an off Saturday"
    for name, start, _end, kind in sem["events"]:
        if kind in ("mst", "exam") and 0 < (start - deadline).days <= 3:
            return f"{name} starts {d_long(start)}"
    return None


def build_assignment(row, sem):
    n = days_left(row["deadline"])
    deadline = date.fromisoformat(row["deadline"])
    return {
        "id": row["id"],
        "title": row["title"],
        "subject": row["subject"],
        "done": bool(row["done"]),
        "days": n,
        "level": urgency(n),
        "badge": badge_text(n),
        "text": describe_days(n),
        "date_text": f"{deadline.strftime('%a')}, {fmt_day(deadline)} {deadline.year}",
        "note": deadline_note(deadline, sem),
    }


def load_assignments(sem):
    rows = get_db().execute(
        """SELECT a.id, a.title, a.deadline, a.done, s.name AS subject
           FROM assignments a JOIN subjects s ON s.id = a.subject_id
           ORDER BY a.deadline, a.id"""
    ).fetchall()
    return [build_assignment(r, sem) for r in rows]


# ---------- attendance logic ----------

def attendance_info(row, now=None, schedule=None):
    now = now or datetime.now()
    auto = bool(row["auto"])

    if auto:
        schedule = schedule if schedule is not None else get_schedule()
        ends = schedule.get(row["name"], [])
        held = sum(1 for e in ends if e <= now)
        remaining = len(ends) - held
        missed = min(row["missed"], held)
        attended = held - missed
    else:
        held, attended, remaining = row["total"], row["attended"], None
        missed = held - attended

    info = {
        "id": row["id"], "name": row["name"], "auto": auto,
        "held": held, "attended": attended, "missed": missed, "remaining": remaining,
    }

    if held == 0:
        info.update(pct=None, status="none", note="No classes held yet.")
        return info

    pct = attended / held * 100
    info["pct"] = pct

    if pct < TARGET:
        # smallest x with (a + x) / (h + x) >= 0.75  ->  x >= 3h - 4a
        need = 3 * held - 4 * attended
        info["status"] = "low"
        if remaining is not None and need > remaining:
            best = (attended + remaining) / (held + remaining) * 100
            info["note"] = f"Even attending all {remaining} remaining classes, you would finish at {best:.0f}%."
        else:
            info["note"] = f"Attend the next {plural(need, 'class')} in a row to get back to {TARGET}%."
    else:
        info["status"] = "ok"
        if remaining is None:
            # largest y with a / (h + y) >= 0.75  ->  y <= (4a - 3h) / 3
            can_miss = (4 * attended - 3 * held) // 3
            if can_miss <= 0:
                info["note"] = f"Right at {TARGET}%. Missing the next class drops you below."
            else:
                info["note"] = f"You can miss {plural(can_miss, 'more class')} and stay at {TARGET}% or above."
        elif remaining == 0:
            info["note"] = "Classes for this semester are over."
        else:
            # final attendance >= 75%  ->  total misses <= floor(final_total / 4)
            spare = (held + remaining) // 4 - missed
            if spare <= 0:
                info["note"] = f"Don't miss any of the remaining {remaining} classes if you want to finish at {TARGET}%."
            else:
                info["note"] = f"You can miss {spare} of the remaining {remaining} classes and still finish at {TARGET}%."
    return info


def load_subjects():
    rows = get_db().execute("SELECT * FROM subjects ORDER BY auto DESC, name").fetchall()
    return [attendance_info(r) for r in rows]


def attendance_summary(subjects):
    rated = [s for s in subjects if s["pct"] is not None]
    attended = sum(s["attended"] for s in rated)
    held = sum(s["held"] for s in rated)
    return {
        "overall": attended / held * 100 if held else None,
        "held": held,
        "low": sum(1 for s in rated if s["status"] == "low"),
        "lowest": min(rated, key=lambda s: s["pct"]) if rated else None,
    }


# ---------- calendar page ----------

def build_months(sem, assignments):
    today = date.today()
    numbers = teaching_numbers(sem)
    due = {}
    for a in assignments:
        if not a["done"]:
            due.setdefault(a["iso"], []).append(a["title"])

    year = sem["start"].year
    months = []
    for month in range(sem["start"].month, 13):
        weeks = []
        for week in pycal.Calendar(0).monthdatescalendar(year, month):
            cells = []
            for day in week:
                if day.month != month:
                    cells.append(None)
                    continue
                ev = event_on(day, sem)
                label, num = None, None
                if ev:
                    kind, label = ev[1], ev[0]
                elif day < sem["start"] or day > sem["end"]:
                    kind = "outside"
                elif day.weekday() == 6 or is_off_saturday(day, sem):
                    kind = "off"
                else:
                    kind, num = "class", numbers[day]
                titles = due.get(day.isoformat(), [])
                if label:
                    tip = f"{day.strftime('%A')}, {fmt_day(day)}: {label}"
                elif num:
                    tip = f"{day.strftime('%A')}, {fmt_day(day)}: teaching day {num}"
                else:
                    tip = f"{day.strftime('%A')}, {fmt_day(day)}"
                if titles:
                    tip += ". Due: " + ", ".join(titles)
                cells.append({
                    "day": day.day, "kind": kind, "label": label, "num": num,
                    "today": day == today, "due": len(titles), "tip": tip,
                })
            if any(c and c["kind"] != "outside" for c in cells):
                weeks.append(cells)
        if weeks:
            months.append({"title": date(year, month, 1).strftime("%B %Y"), "weeks": weeks})
    return months


# ---------- request hooks ----------

@app.before_request
def require_name():
    if request.endpoint in ("welcome", "static", "api_reminders"):
        return None
    if not get_name():
        return redirect(url_for("welcome"))
    return None


@app.context_processor
def inject_globals():
    name = get_name()
    return {
        "name": name,
        "target": TARGET,
        "sem_label": get_sem()["label"] if name else "",
    }


# ---------- pages ----------

@app.route("/welcome", methods=["GET", "POST"])
def welcome():
    error = None
    stored = get_setting("semester")
    if request.method == "POST":
        entered = request.form.get("name", "").strip()
        semester = request.form.get("semester", type=int)
        if not entered:
            error = "Enter your name to continue."
        elif len(entered) > 60:
            error = "Keep the name under 60 characters."
        elif semester not in cal.SEMESTERS:
            error = "Choose your semester."
        else:
            db = get_db()
            if stored is not None and int(stored) != semester:
                reset_semester_data(db)
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('name', ?)", (entered,))
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('semester', ?)", (str(semester),))
            db.commit()
            return redirect(url_for("dashboard"))
    return render_template(
        "welcome.html",
        error=error,
        current=get_name(),
        stored_semester=stored,
        current_sem=int(stored) if stored else cal.DEFAULT_SEMESTER,
        semesters=sorted(cal.SEMESTERS.items()),
    )


@app.route("/")
def dashboard():
    sem = get_sem()
    items = load_assignments(sem)
    pending = [a for a in items if not a["done"]]
    alerts = [a for a in pending if a["days"] <= 2]
    subjects = load_subjects()
    summary_att = attendance_summary(subjects)
    classes, no_class_reason = todays_classes(sem)
    events = upcoming_events(sem)

    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"

    if not pending:
        summary = "Nothing pending right now."
    else:
        summary = f"{len(pending)} assignment{'s' if len(pending) != 1 else ''} pending"
        summary += f", {len(alerts)} needing attention soon." if alerts else ", none due in the next 2 days."

    upcoming_class = next((c for c in classes if c["status"] in ("now", "next")), None)
    rated = sorted((s for s in subjects if s["pct"] is not None), key=lambda s: s["pct"])

    return render_template(
        "dashboard.html",
        greeting=greeting,
        summary=summary,
        alerts=alerts,
        pending=pending,
        soon_count=len(alerts),
        att=summary_att,
        attention=rated[:4],
        subject_count=len(subjects),
        classes=classes,
        no_class_reason=no_class_reason,
        upcoming_class=upcoming_class,
        events=events,
        next_event=events[0] if events else None,
    )


# --- assignments ---

@app.route("/assignments", methods=["GET", "POST"])
def assignments():
    db = get_db()
    sem = get_sem()
    subjects = db.execute("SELECT id, name FROM subjects ORDER BY name").fetchall()
    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        deadline = request.form.get("deadline", "").strip()
        subject_id = request.form.get("subject_id", type=int)
        valid_ids = {s["id"] for s in subjects}
        try:
            date.fromisoformat(deadline)
            date_ok = True
        except ValueError:
            date_ok = False

        if subject_id not in valid_ids:
            error = "Choose a subject."
        elif not title:
            error = "Enter the assignment name."
        elif not date_ok:
            error = "Pick a valid deadline."
        else:
            db.execute(
                "INSERT INTO assignments (subject_id, title, deadline) VALUES (?, ?, ?)",
                (subject_id, title, deadline),
            )
            db.commit()
            return redirect(url_for("assignments"))

    items = load_assignments(sem)
    return render_template(
        "assignments.html",
        subjects=subjects,
        error=error,
        today=date.today().isoformat(),
        overdue=[a for a in items if not a["done"] and a["days"] < 0],
        upcoming=[a for a in items if not a["done"] and a["days"] >= 0],
        completed=[a for a in items if a["done"]],
    )


@app.post("/assignments/<int:aid>/done")
def mark_done(aid):
    db = get_db()
    db.execute("UPDATE assignments SET done = 1 - done WHERE id = ?", (aid,))
    db.commit()
    return redirect(request.referrer or url_for("assignments"))


@app.post("/assignments/<int:aid>/delete")
def delete_assignment(aid):
    db = get_db()
    db.execute("DELETE FROM assignments WHERE id = ?", (aid,))
    db.commit()
    return redirect(request.referrer or url_for("assignments"))


# --- attendance ---

@app.route("/attendance", methods=["GET", "POST"])
def attendance():
    """Also handles adding a custom subject that is not in the timetable."""
    error = None
    if request.method == "POST":
        subject = request.form.get("name", "").strip()
        attended = request.form.get("attended", type=int)
        total = request.form.get("total", type=int)
        if not subject:
            error = "Enter the subject name."
        elif attended is None or total is None or attended < 0 or total < 0:
            error = "Classes attended and total must be numbers, 0 or more."
        elif attended > total:
            error = "Attended classes can't be more than total classes."
        else:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO subjects (name, attended, total, auto) VALUES (?, ?, ?, 0)",
                    (subject, attended, total),
                )
                db.commit()
                return redirect(url_for("attendance"))
            except sqlite3.IntegrityError:
                error = f"{subject} is already added."
    sem = get_sem()
    subjects = load_subjects()
    return render_template(
        "attendance.html",
        subjects=subjects,
        att=attendance_summary(subjects),
        has_timetable=bool(timetable_rows()),
        error=error,
        sem_start=d_long(sem["start"]),
        sem_end=d_long(sem["end"]),
    )


@app.post("/attendance/<int:sid>/present")
def mark_present(sid):
    """Manual subjects only: one more class attended."""
    db = get_db()
    db.execute(
        "UPDATE subjects SET attended = attended + 1, total = total + 1 WHERE id = ? AND auto = 0", (sid,)
    )
    db.commit()
    return redirect(url_for("attendance"))


@app.post("/attendance/<int:sid>/absent")
def mark_absent(sid):
    """One more class missed. Timetable subjects raise 'missed'; manual ones raise 'total'."""
    db = get_db()
    db.execute("UPDATE subjects SET missed = missed + 1 WHERE id = ? AND auto = 1", (sid,))
    db.execute("UPDATE subjects SET total = total + 1 WHERE id = ? AND auto = 0", (sid,))
    db.commit()
    return redirect(url_for("attendance"))


@app.post("/attendance/<int:sid>/set-missed")
def set_missed(sid):
    missed = request.form.get("missed", type=int)
    if missed is not None and missed >= 0:
        db = get_db()
        db.execute("UPDATE subjects SET missed = ? WHERE id = ? AND auto = 1", (missed, sid))
        db.commit()
    return redirect(url_for("attendance"))


@app.post("/attendance/<int:sid>/delete")
def delete_subject(sid):
    db = get_db()
    db.execute("DELETE FROM assignments WHERE subject_id = ?", (sid,))
    db.execute("DELETE FROM subjects WHERE id = ?", (sid,))
    db.commit()
    return redirect(url_for("attendance"))


# --- timetable ---

def render_timetable(error=None):
    sem_key = get_semester()
    rows = timetable_rows()
    today_idx = date.today().weekday()
    return render_template(
        "timetable.html",
        grid=timetable_grid(rows),
        days=DAY_NAMES,
        today_idx=today_idx if today_idx < 6 else -1,
        rows=rows,
        day_names=DAY_NAMES,
        presets=[(k, p["label"]) for k, p in cal.TIMETABLE_PRESETS.items() if p["semester"] == sem_key],
        subject_names=sorted({r["subject"] for r in rows}),
        periods=sorted(cal.PERIOD_TIMES),
        error=error,
    )


@app.route("/timetable")
def timetable():
    return render_timetable()


@app.post("/timetable/add")
def add_class():
    db = get_db()
    day = request.form.get("day", type=int)
    subject = request.form.get("subject", "").strip()
    first = request.form.get("start_period", type=int)
    last = request.form.get("end_period", type=int)
    tracked = 1 if request.form.get("tracked") else 0

    error = None
    if day is None or not 0 <= day <= 5:
        error = "Choose a day."
    elif not subject or len(subject) > 60:
        error = "Enter a subject name (up to 60 characters)."
    elif first is None or last is None or not (1 <= first <= last <= 6):
        error = "Choose a valid period range."
    elif first <= LUNCH_AFTER < last:
        error = "A class can't run across the lunch break."
    else:
        for e in db.execute("SELECT * FROM timetable WHERE day = ?", (day,)):
            if not (last < e["start_period"] or first > e["end_period"]):
                error = f"That clashes with {e['subject']} on {DAY_NAMES[day]}."
                break
    if error:
        return render_timetable(error)

    db.execute(
        "INSERT INTO timetable (day, start_period, end_period, subject, tracked) VALUES (?, ?, ?, ?, ?)",
        (day, first, last, subject, tracked),
    )
    if tracked:
        ensure_auto_subject(db, subject)
    db.commit()
    return redirect(url_for("timetable"))


@app.post("/timetable/<int:cid>/delete")
def delete_class(cid):
    db = get_db()
    db.execute("DELETE FROM timetable WHERE id = ?", (cid,))
    db.commit()
    return redirect(url_for("timetable"))


@app.post("/timetable/load-preset")
def load_timetable_preset():
    key = request.form.get("preset", "")
    preset = cal.TIMETABLE_PRESETS.get(key)
    db = get_db()
    if preset and preset["semester"] == get_semester() and not timetable_rows():
        load_preset(db, key)
        db.commit()
    return redirect(url_for("timetable"))


# --- calendar ---

@app.route("/calendar")
def calendar_page():
    sem = get_sem()
    assignments_list = load_assignments(sem)
    # add the ISO date to each assignment so the calendar can mark deadlines
    iso = {r["id"]: r["deadline"] for r in get_db().execute("SELECT id, deadline FROM assignments")}
    for a in assignments_list:
        a["iso"] = iso[a["id"]]
    return render_template(
        "calendar.html",
        months=build_months(sem, assignments_list),
        events=upcoming_events(sem),
        sem=sem,
        teaching_days=len(teaching_numbers(sem)),
        start_text=d_long(sem["start"]),
        end_text=d_long(sem["end"]),
    )


# --- notifications ---

@app.route("/api/reminders")
def api_reminders():
    """Reminders that the browser turns into notifications. One key per item per day."""
    if not get_name():
        return jsonify(items=[])
    today = date.today().isoformat()
    items = []
    for a in load_assignments(get_sem()):
        if not a["done"] and a["days"] <= 2:
            items.append({
                "key": f"{today}:deadline:{a['id']}",
                "title": a["title"],
                "body": f"{a['subject']}: {a['text']}",
            })
    for sub in load_subjects():
        if sub["status"] == "low":
            items.append({
                "key": f"{today}:attendance:{sub['id']}",
                "title": f"Low attendance: {sub['name']}",
                "body": f"{sub['pct']:.0f}%. {sub['note']}",
            })
    return jsonify(items=items)


init_db()

if __name__ == "__main__":
    app.run(debug=True)
