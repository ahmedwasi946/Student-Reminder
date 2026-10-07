"""
Academic calendar (Aug-Dec 2026) and timetable presets.

Everything the app knows about the college schedule lives in this file.
If a date is wrong, fix it here and restart the app.

Calendar source: "GN Academic Calendar, Session Aug-Dec 2026" (B.Tech 1st, 3rd, 5th, 7th).
Timetable source: CS&IT-V timetable, effective 07-09-2026.
"""
from datetime import date, time


def d(month, day):
    return date(2026, month, day)


DEFAULT_SEMESTER = 5

# Start and end time of each period. Lunch is 1:35 to 2:10, between periods 4 and 5.
PERIOD_TIMES = {
    1: (time(9, 35), time(10, 35)),
    2: (time(10, 35), time(11, 35)),
    3: (time(11, 35), time(12, 35)),
    4: (time(12, 35), time(13, 35)),
    5: (time(14, 10), time(15, 10)),
    6: (time(15, 10), time(16, 10)),
}

# Holidays that apply to every semester. Each event: (name, first day, last day, kind).
# kind is one of: holiday, training, mst, exam. No regular classes on any of these days.
HOLIDAYS = [
    ("Janmashtami", d(9, 4), d(9, 4), "holiday"),
    ("Ganesh Chaturthi", d(9, 14), d(9, 14), "holiday"),
    ("Gandhi Jayanti", d(10, 2), d(10, 2), "holiday"),
    ("Durga Navmi", d(10, 19), d(10, 19), "holiday"),
    ("Vijay Dashmi", d(10, 20), d(10, 20), "holiday"),
    ("Diwali holidays", d(11, 6), d(11, 10), "holiday"),
    ("Gas Tragedy day", d(12, 3), d(12, 3), "holiday"),
    ("Christmas", d(12, 25), d(12, 25), "holiday"),
]


def _events(extra):
    return sorted(HOLIDAYS + extra, key=lambda e: e[1])


# start = teaching day 1, end = last teaching day.
# off_saturdays = which Saturdays of the month have no classes (1 = first Saturday ...).
SEMESTERS = {
    1: {
        "label": "B.Tech 1st semester",
        "start": d(8, 31),
        "end": d(12, 16),
        "off_saturdays": (1, 3),
        "events": _events([
            ("MST-I", d(10, 21), d(10, 27), "mst"),
            ("MST-II", d(12, 17), d(12, 24), "mst"),
        ]),
    },
    3: {
        "label": "B.Tech 3rd semester",
        "start": d(9, 21),
        "end": d(12, 16),
        "off_saturdays": (1, 3),
        "events": _events([
            ("Training", d(9, 7), d(9, 12), "training"),
            ("Training", d(9, 15), d(9, 18), "training"),
            ("MST-I", d(11, 23), d(11, 28), "mst"),
            ("MST-II", d(12, 17), d(12, 24), "mst"),
        ]),
    },
    5: {
        "label": "B.Tech 5th semester",
        "start": d(9, 7),
        "end": d(12, 17),
        "off_saturdays": (1, 3),
        "events": _events([
            ("Training", d(9, 21), d(10, 1), "training"),
            ("MST-I", d(11, 24), d(11, 28), "mst"),
            ("MST-II", d(12, 18), d(12, 24), "mst"),
        ]),
    },
    7: {
        "label": "B.Tech 7th semester",
        "start": d(8, 31),
        "end": d(11, 27),
        "off_saturdays": (1, 2, 3, 4, 5),   # every Saturday is off for this batch
        "events": _events([
            ("MST-I", d(10, 27), d(10, 30), "mst"),
            ("MST-II", d(11, 30), d(12, 2), "mst"),
            ("MST-II", d(12, 4), d(12, 4), "mst"),
            ("RGPV Exam", d(12, 7), d(12, 24), "exam"),
            ("RGPV Exam", d(12, 26), d(12, 31), "exam"),
        ]),
    },
}

# ---------------------------------------------------------------------------
# Timetable presets. A student can load one of these from the Timetable page,
# or add classes by hand. To add another preset, copy the block below.
#
# Each class: (day, first period, last period, subject, counts for attendance)
# day: Monday = 0 ... Saturday = 5. A two-period lab is one class.
# ---------------------------------------------------------------------------
OS = "Operating Systems"
COMM = "Communication Skills"
WEB = "Web Technology"
POPL = "Principles of Prog. Lang."
CN = "Computer Networks"

TIMETABLE_PRESETS = {
    "csit-5": {
        "label": "CS&IT 5th semester (SISTec, Room 309, from 7 Sep 2026)",
        "semester": 5,
        "classes": [
            (0, 1, 1, OS, True), (0, 2, 2, COMM, True), (0, 3, 3, WEB, True),
            (0, 4, 4, POPL, True), (0, 5, 6, "Computer Networks Lab", True),

            (1, 1, 2, "Aptitude", True), (1, 3, 4, "Operating Systems Lab", True),
            (1, 5, 5, POPL, True), (1, 6, 6, CN, True),

            (2, 1, 2, COMM, True), (2, 3, 3, POPL, True), (2, 4, 4, OS, True),
            (2, 5, 5, "Library", False), (2, 6, 6, CN, True),

            (3, 1, 2, "Minor Project Lab", True), (3, 3, 3, OS, True),
            (3, 4, 4, WEB, True), (3, 5, 6, "Web Technology Lab", True),

            (4, 1, 1, WEB, True), (4, 2, 2, CN, True), (4, 3, 4, "Linux Lab", True),
            (4, 5, 6, "AMCAT", True),

            (5, 1, 1, CN, True), (5, 2, 2, POPL, True), (5, 3, 4, "Minor Project Lab", True),
            (5, 5, 5, OS, True), (5, 6, 6, WEB, True),
        ],
    },
}
