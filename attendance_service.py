"""
College period timing, holiday checks, grace windows, attendance continuity,
and summary statistics (including half-day classification).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

# Default college day: 9:20–16:00, lunch 12:40–13:30, seven 50-minute teaching slots.
PERIODS: List[Dict[str, Any]] = [
    {"period": 1, "start": (9, 20), "end": (10, 10), "after_lunch": False},
    {"period": 2, "start": (10, 10), "end": (11, 0), "after_lunch": False},
    {"period": 3, "start": (11, 0), "end": (11, 50), "after_lunch": False},
    {"period": 4, "start": (11, 50), "end": (12, 40), "after_lunch": False},
    {"period": 5, "start": (13, 30), "end": (14, 20), "after_lunch": True},
    {"period": 6, "start": (14, 20), "end": (15, 10), "after_lunch": True},
    {"period": 7, "start": (15, 10), "end": (16, 0), "after_lunch": True},
]

LUNCH_START = (12, 40)
LUNCH_END = (13, 30)
GRACE_MINUTES = 5


def _to_minutes(t: Tuple[int, int]) -> int:
    return t[0] * 60 + t[1]


def is_lunch_time(now: Optional[dt.datetime] = None) -> bool:
    cur = now or dt.datetime.now()
    m = cur.hour * 60 + cur.minute
    ls = _to_minutes(LUNCH_START)
    le = _to_minutes(LUNCH_END)
    return ls <= m < le


def is_public_holiday(conn, iso_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM holidays WHERE date = ? LIMIT 1", (iso_date,)
    ).fetchone()
    return row is not None


def current_period_info(now: Optional[dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """
    Return active period metadata for `now`, or None if outside teaching slots
    (includes lunch and outside 9:20–16:00).
    """
    cur = now or dt.datetime.now()
    m = cur.hour * 60 + cur.minute
    lunch_s, lunch_e = _to_minutes(LUNCH_START), _to_minutes(LUNCH_END)
    if lunch_s <= m < lunch_e:
        return None

    for slot in PERIODS:
        s = _to_minutes(slot["start"])
        e = _to_minutes(slot["end"])
        if s <= m < e:
            return {
                "period": slot["period"],
                "after_lunch": slot["after_lunch"],
                "slot_start_min": s,
                "slot_end_min": e,
            }
    return None


def period_deadline_minutes(period_number: int) -> Optional[int]:
    """Minute-of-day threshold: after this, grace has expired for the period start."""
    for slot in PERIODS:
        if slot["period"] == period_number:
            return _to_minutes(slot["start"]) + GRACE_MINUTES
    return None


def minutes_from_datetime(cur: dt.datetime) -> int:
    return cur.hour * 60 + cur.minute


def grace_elapsed_for_period(period_number: int, now: Optional[dt.datetime] = None) -> bool:
    """True if current clock is past the grace window from period start."""
    cur = now or dt.datetime.now()
    deadline = period_deadline_minutes(period_number)
    if deadline is None:
        return False
    return minutes_from_datetime(cur) >= deadline


def fetch_schedule_rows(
    conn,
    department: str,
    year: str,
    section: str,
    iso_date: str,
) -> List[Any]:
    return conn.execute(
        """
        SELECT id, department, year, section, subject, faculty, start_time, end_time, type, date
        FROM schedules
        WHERE department = ? AND year = ? AND section = ? AND date = ?
        ORDER BY start_time
        """,
        (department, year, section, iso_date),
    ).fetchall()


def schedule_subject_for_period(
    conn,
    department: str,
    year: str,
    section: str,
    iso_date: str,
    period_number: int,
) -> Tuple[str, str]:
    """
    Resolve subject and faculty for a calendar period using schedule rows.
    Falls back to generic labels when no row exists.
    """
    rows = fetch_schedule_rows(conn, department, year, section, iso_date)
    slot = next((p for p in PERIODS if p["period"] == period_number), None)
    if not slot:
        return (f"Period {period_number}", "TBD")

    slot_start = "%02d:%02d" % slot["start"]
    slot_end = "%02d:%02d" % slot["end"]

    for row in rows:
        if row["type"] == "lab":
            try:
                sh, sm = map(int, row["start_time"].split(":")[:2])
                eh, em = map(int, row["end_time"].split(":")[:2])
            except (ValueError, AttributeError):
                continue
            rs, re_ = sh * 60 + sm, eh * 60 + em
            ps = _to_minutes(slot["start"])
            if rs <= ps < re_:
                return (row["subject"], row["faculty"])
        else:
            if row["start_time"] == slot_start and row["end_time"] == slot_end:
                return (row["subject"], row["faculty"])
    return (f"Period {period_number}", "TBD")


def attendance_for_day(conn, student_id: str, iso_date: str) -> Dict[int, Any]:
    rows = conn.execute(
        """
        SELECT period, status, subject, attendance_type, marked_time
        FROM attendance
        WHERE student_id = ? AND date = ?
        """,
        (student_id, iso_date),
    ).fetchall()
    return {int(r["period"]): r for r in rows}


def upsert_attendance(
    conn,
    *,
    student_id: str,
    name: str,
    department: str,
    branch: str,
    section: str,
    iso_date: str,
    period: int,
    subject: str,
    status: str,
    attendance_type: str,
    marked_time: str,
):
    conn.execute(
        """
        INSERT INTO attendance (
            student_id, name, department, branch, section, date, time,
            subject, period, status, attendance_type, marked_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, date, period) DO UPDATE SET
            status = excluded.status,
            attendance_type = excluded.attendance_type,
            marked_time = excluded.marked_time,
            subject = excluded.subject,
            time = excluded.marked_time,
            name = excluded.name,
            department = excluded.department,
            branch = excluded.branch,
            section = excluded.section
        """,
        (
            student_id,
            name,
            department,
            branch,
            section,
            iso_date,
            marked_time,
            subject,
            period,
            status,
            attendance_type,
            marked_time,
        ),
    )


def _prev_period(p: int) -> Optional[int]:
    return p - 1 if p > 1 else None


def apply_automatic_rules(
    conn,
    *,
    student_id: str,
    name: str,
    department: str,
    branch: str,
    year_str: str,
    section: str,
    iso_date: str,
    now: Optional[dt.datetime] = None,
):
    """
    Apply grace-based absent marking and present continuity for the given day.

    Past grace with no row -> Absent (automatic), unless the immediately
    previous teaching period was Present (continuity roll-forward). Period 1
    never rolls forward from a previous day.
    """
    cur = now or dt.datetime.now()
    if is_public_holiday(conn, iso_date):
        return
    if cur.date().isoformat() != iso_date:
        return

    by_period = attendance_for_day(conn, student_id, iso_date)
    cur_min = minutes_from_datetime(cur)

    for slot in PERIODS:
        p = slot["period"]
        if p in by_period:
            continue
        deadline = _to_minutes(slot["start"]) + GRACE_MINUTES
        if cur_min < deadline:
            continue

        subject, _fac = schedule_subject_for_period(
            conn, department, year_str, section, iso_date, p
        )
        prev = _prev_period(p)
        prev_present = (
            prev is not None
            and prev in by_period
            and (by_period[prev]["status"] or "").strip().lower() == "present"
        )
        if prev_present and p > 1:
            status = "Present"
            att_type = "automatic"
        else:
            status = "Absent"
            att_type = "automatic"

        marked = cur.time().strftime("%H:%M:%S")
        upsert_attendance(
            conn,
            student_id=student_id,
            name=name,
            department=department,
            branch=branch,
            section=section,
            iso_date=iso_date,
            period=p,
            subject=subject,
            status=status,
            attendance_type=att_type,
            marked_time=marked,
        )
        by_period = attendance_for_day(conn, student_id, iso_date)


def student_day_stats(conn, student_id: str, iso_date: str) -> Dict[str, Any]:
    """Aggregates for dashboards: counts, percentage, half-day flag."""
    if is_public_holiday(conn, iso_date):
        return {
            "holiday": True,
            "present": 0,
            "absent": 0,
            "half_day": False,
            "full_day": False,
            "pct": None,
            "by_period": {},
        }

    by_period = attendance_for_day(conn, student_id, iso_date)
    present = absent = 0
    morning_slots = [s for s in PERIODS if not s["after_lunch"]]
    afternoon_slots = [s for s in PERIODS if s["after_lunch"]]

    morning_present = all(
        by_period.get(s["period"])
        and (by_period[s["period"]]["status"] or "").strip().lower() == "present"
        for s in morning_slots
    )
    afternoon_absent = any(
        by_period.get(s["period"])
        and (by_period[s["period"]]["status"] or "").strip().lower() == "absent"
        for s in afternoon_slots
    )

    for slot in PERIODS:
        p = slot["period"]
        row = by_period.get(p)
        if not row:
            continue
        st = (row["status"] or "").strip().lower()
        if st == "present":
            present += 1
        elif st == "absent":
            absent += 1

    recorded = present + absent
    pct = round(100.0 * present / recorded, 1) if recorded else None
    half_day = morning_present and afternoon_absent
    full_day = recorded == len(PERIODS) and present == len(PERIODS)

    return {
        "holiday": False,
        "present": present,
        "absent": absent,
        "half_day": half_day,
        "full_day": full_day,
        "pct": pct,
        "by_period": by_period,
    }


def month_calendar_flags(
    conn,
    student_id: str,
    year: int,
    month: int,
) -> Dict[str, str]:
    """
    Return map iso_date -> 'holiday' | 'present' | 'absent' | 'mixed' for a month.
    """
    import calendar

    _, days_in_month = calendar.monthrange(year, month)
    out: Dict[str, str] = {}
    for day in range(1, days_in_month + 1):
        d = dt.date(year, month, day)
        iso = d.isoformat()
        if is_public_holiday(conn, iso):
            out[iso] = "holiday"
            continue
        stats = student_day_stats(conn, student_id, iso)
        if stats["present"] == len(PERIODS):
            out[iso] = "present"
        elif stats["absent"] == len(PERIODS):
            out[iso] = "absent"
        elif stats["present"] or stats["absent"]:
            out[iso] = "mixed"
        else:
            out[iso] = "none"
    return out
