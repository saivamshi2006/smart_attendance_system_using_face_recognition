"""
College Attendance Management System — Flask application with session auth,
role-based access, iris/eye recognition, schedules, and automated attendance rules.
"""
import csv
import datetime
import io
import os
import pickle
import sqlite3
from functools import wraps
from pathlib import Path

import numpy as np
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import attendance_service as att
from database import get_db_connection, init_db
from face_utils import (
    compare_templates,
    decode_image_from_base64,
    extract_iris_template,
    load_image_from_file,
)

# -----------------------------------------------------------------------------
# App configuration
# -----------------------------------------------------------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-in-production-college-attendance")

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_setting(key: str, default: str = "") -> str:
    conn = get_db_connection()
    row = conn.execute(
        "SELECT value FROM college_settings WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


@app.context_processor
def inject_college():
    logo = get_setting("college_logo", "")
    logo_url = url_for("static", filename=logo) if logo else ""
    return {
        "college_name": get_setting("college_name", "College Attendance Portal"),
        "college_logo_url": logo_url,
    }


# -----------------------------------------------------------------------------
# Decorators — session-based RBAC
# -----------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("login_id"):
            flash("Please log in to continue.")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("login_id"):
            flash("Please log in to continue.")
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "admin":
            flash("You do not have permission to access that page.")
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapped


def lecturer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("login_id"):
            flash("Please log in to continue.")
            return redirect(url_for("login", next=request.path))
        if session.get("role") not in ("lecturer", "teacher"):
            flash("You do not have permission to access that page.")
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapped


def student_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("login_id"):
            flash("Please log in to continue.")
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "student":
            flash("You do not have permission to access that page.")
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapped


# -----------------------------------------------------------------------------
# DB helpers
# -----------------------------------------------------------------------------
def _insert_encoding_cursor(conn, student_id, encoding):
    row = conn.execute(
        "SELECT student_id FROM students WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    exists = bool(row)
    print(f"[encodings] insert student_id={student_id!r} exists_in_students={exists}", flush=True)
    if not exists:
        raise ValueError(
            f"Cannot insert encoding: no student row for student_id={student_id!r}."
        )
    blob = pickle.dumps(encoding)
    conn.execute(
        "INSERT INTO encodings (student_id, encoding) VALUES (?, ?)",
        (student_id, blob),
    )


def save_encoding(student_id, encoding):
    conn = get_db_connection()
    try:
        _insert_encoding_cursor(conn, student_id, encoding)
        conn.commit()
    except ValueError:
        conn.rollback()
        raise
    except sqlite3.IntegrityError:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_known_encodings_for_section(section_id):
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT s.student_id AS student_id, s.name AS name, e.encoding AS encoding
        FROM encodings e
        JOIN students s ON s.student_id = e.student_id
        WHERE s.section_id = ?
        """,
        (section_id,),
    ).fetchall()
    conn.close()
    known = []
    for row in rows:
        known.append(
            {
                "student_id": row["student_id"],
                "name": row["name"],
                "encoding": pickle.loads(row["encoding"]),
            }
        )
    return known


def fetch_own_encodings(student_id):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT encoding FROM encodings WHERE student_id = ?",
        (student_id,),
    ).fetchall()
    conn.close()
    return [pickle.loads(r["encoding"]) for r in rows]


def get_section_labels(section_id):
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT d.name AS department, b.name AS branch, s.name AS section
        FROM sections s
        JOIN branches b ON b.id = s.branch_id
        JOIN departments d ON d.id = b.department_id
        WHERE s.id = ?
        """,
        (section_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "department": row["department"],
        "branch": row["branch"],
        "section": row["section"],
    }


def section_exists(section_id):
    conn = get_db_connection()
    row = conn.execute("SELECT 1 FROM sections WHERE id = ?", (section_id,)).fetchone()
    conn.close()
    return row is not None


def student_profile(student_id: str):
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT s.student_id, s.name, s.year, s.username,
               d.name AS dept_name, b.name AS branch_name, sec.name AS section_name,
               s.section_id
        FROM students s
        JOIN departments d ON d.id = s.department_id
        JOIN branches b ON b.id = s.branch_id
        JOIN sections sec ON sec.id = s.section_id
        WHERE s.student_id = ?
        """,
        (student_id,),
    ).fetchone()
    conn.close()
    return row


def run_auto_attendance_for_student(student_id: str):
    """Apply grace / continuity rules for today."""
    prof = student_profile(student_id)
    if not prof:
        return
    today = datetime.date.today().isoformat()
    conn = get_db_connection()
    try:
        att.apply_automatic_rules(
            conn,
            student_id=student_id,
            name=prof["name"],
            department=prof["branch_name"],
            branch=prof["branch_name"],
            year_str=prof["year"],
            section=prof["section_name"],
            iso_date=today,
        )
        conn.commit()
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Auth routes
# -----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("login_id"):
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not username or not password:
            flash("Username and password are required.")
            return render_template("login.html")

        conn = get_db_connection()
        row = conn.execute(
            "SELECT id, username, password, role FROM users_login WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()

        if not row or not check_password_hash(row["password"], password):
            flash("Invalid username or password.")
            return render_template("login.html")

        session["login_id"] = row["id"]
        session["username"] = row["username"]
        session["role"] = row["role"]

        if row["role"] == "student":
            conn = get_db_connection()
            srow = conn.execute(
                "SELECT student_id FROM students WHERE username = ?",
                (username,),
            ).fetchone()
            conn.close()
            if not srow:
                session.clear()
                flash("Student profile is not linked to this account.")
                return render_template("login.html")
            session["student_id"] = srow["student_id"]

        flash(f"Welcome, {row['username']}.")

        next_url = request.args.get("next") or request.form.get("next") or ""
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        if row["role"] == "admin":
            return redirect(url_for("admin_dashboard"))
        if row["role"] in ("lecturer", "teacher"):
            return redirect(url_for("lecturer_dashboard"))
        if row["role"] == "student":
            return redirect(url_for("student_dashboard"))
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("login_id"):
        return redirect(url_for("index"))

    if request.method == "POST":
        role = (request.form.get("role") or "student").strip().lower()
        if role == "student":
            return _register_student()
        if role == "lecturer":
            return _register_lecturer()
        flash("Invalid role.")
        conn = get_db_connection()
        departments = conn.execute("SELECT id, name FROM departments ORDER BY name").fetchall()
        conn.close()
        return render_template("register.html", departments=departments)

    conn = get_db_connection()
    departments = conn.execute("SELECT id, name FROM departments ORDER BY name").fetchall()
    conn.close()
    return render_template("register.html", departments=departments)


def _register_student():
    student_id = (request.form.get("student_id") or "").strip()
    name = (request.form.get("name") or "").strip()
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    year = (request.form.get("year") or "1st").strip()
    department_id = request.form.get("department_id", type=int)
    branch_id = request.form.get("branch_id", type=int)
    section_id = request.form.get("section_id", type=int)

    if not all([student_id, name, username, password, department_id, branch_id, section_id]):
        flash("All student fields are required.")
        return redirect(url_for("register"))

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO users_login (username, password, role)
            VALUES (?, ?, 'student')
            """,
            (username, generate_password_hash(password)),
        )
        conn.execute(
            """
            INSERT INTO students (student_id, name, department_id, branch_id, section_id, year, username, password)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                student_id,
                name,
                department_id,
                branch_id,
                section_id,
                year,
                username,
                generate_password_hash(password),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("Username or roll number already exists.")
        return redirect(url_for("register"))
    finally:
        conn.close()

    flash("Registration successful. Please sign in.")
    return redirect(url_for("login"))


def _register_lecturer():
    lecturer_id = (request.form.get("lecturer_id") or "").strip()
    name = (request.form.get("name") or "").strip()
    department = (request.form.get("department") or "").strip()
    username = (request.form.get("lec_username") or "").strip()
    password = request.form.get("lec_password") or ""

    if not all([lecturer_id, name, department, username, password]):
        flash("All lecturer fields are required.")
        return redirect(url_for("register"))

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO users_login (username, password, role)
            VALUES (?, ?, 'lecturer')
            """,
            (username, generate_password_hash(password)),
        )
        conn.execute(
            """
            INSERT INTO lecturers (lecturer_id, name, department, username, password)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                lecturer_id,
                name,
                department,
                username,
                generate_password_hash(password),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("Lecturer ID or username already exists.")
        return redirect(url_for("register"))
    finally:
        conn.close()

    flash("Lecturer account created. Please sign in.")
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


@app.route("/")
def index():
    if not session.get("login_id"):
        return redirect(url_for("login"))
    role = session.get("role")
    if role == "admin":
        return redirect(url_for("admin_dashboard"))
    if role in ("lecturer", "teacher"):
        return redirect(url_for("lecturer_dashboard"))
    if role == "student":
        return redirect(url_for("student_dashboard"))
    return redirect(url_for("login"))


# -----------------------------------------------------------------------------
# JSON helpers for cascading dropdowns
# -----------------------------------------------------------------------------
@app.route("/api/branches")
def api_branches():
    dept_id = request.args.get("department_id", type=int)
    if not dept_id:
        return jsonify([])
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, name FROM branches WHERE department_id = ? ORDER BY name",
        (dept_id,),
    ).fetchall()
    conn.close()
    return jsonify([{"id": r["id"], "name": r["name"]} for r in rows])


@app.route("/api/sections")
def api_sections():
    branch_id = request.args.get("branch_id", type=int)
    if not branch_id:
        return jsonify([])
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, name FROM sections WHERE branch_id = ? ORDER BY name",
        (branch_id,),
    ).fetchall()
    conn.close()
    return jsonify([{"id": r["id"], "name": r["name"]} for r in rows])


# -----------------------------------------------------------------------------
# Student area
# -----------------------------------------------------------------------------
@app.route("/student_dashboard")
@student_required
def student_dashboard():
    sid = session["student_id"]
    run_auto_attendance_for_student(sid)
    prof = student_profile(sid)
    if not prof:
        flash("Student record missing.")
        return redirect(url_for("logout"))

    today = datetime.date.today().isoformat()
    conn = get_db_connection()
    try:
        day_stats = att.student_day_stats(conn, sid, today)
        schedule_display = []
        for slot in att.PERIODS:
            p = slot["period"]
            subj, fac = att.schedule_subject_for_period(
                conn,
                prof["branch_name"],
                prof["year"],
                prof["section_name"],
                today,
                p,
            )
            row = conn.execute(
                """
                SELECT status FROM attendance
                WHERE student_id = ? AND date = ? AND period = ?
                """,
                (sid, today, p),
            ).fetchone()
            schedule_display.append(
                {
                    "period": p,
                    "time": f"{slot['start'][0]:02d}:{slot['start'][1]:02d} – "
                    f"{slot['end'][0]:02d}:{slot['end'][1]:02d}",
                    "subject": subj,
                    "faculty": fac,
                    "status": row["status"] if row else "—",
                }
            )
    finally:
        conn.close()

    overall = _overall_attendance_pct(sid)

    return render_template(
        "student_dashboard.html",
        profile=prof,
        day_stats=day_stats,
        schedule_display=schedule_display,
        overall_pct=overall,
        today=today,
    )


def _overall_attendance_pct(student_id: str):
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT status FROM attendance
        WHERE student_id = ? AND (status = 'Present' OR status = 'Absent')
        """,
        (student_id,),
    ).fetchall()
    conn.close()
    if not rows:
        return None
    present = sum(1 for r in rows if (r["status"] or "").lower() == "present")
    return round(100.0 * present / len(rows), 1)


@app.route("/api/student/attendance_prompt")
@student_required
def student_attendance_prompt():
    """Tell the SPA whether to show the automatic attendance popup."""
    sid = session["student_id"]
    prof = student_profile(sid)
    if not prof:
        return jsonify({"show": False})

    today = datetime.date.today().isoformat()
    conn = get_db_connection()
    try:
        if att.is_public_holiday(conn, today):
            return jsonify({"show": False, "reason": "holiday"})

        info = att.current_period_info()
        if not info:
            return jsonify({"show": False})

        p = info["period"]
        row = conn.execute(
            "SELECT 1 FROM attendance WHERE student_id = ? AND date = ? AND period = ?",
            (sid, today, p),
        ).fetchone()
        if row:
            return jsonify({"show": False, "already_marked": True})

        slot = next(s for s in att.PERIODS if s["period"] == p)
        start_min = att._to_minutes(slot["start"])
        now_min = att.minutes_from_datetime(datetime.datetime.now())
        seconds_left = max(0, (start_min + att.GRACE_MINUTES - now_min) * 60)

        subj, fac = att.schedule_subject_for_period(
            conn, prof["branch_name"], prof["year"], prof["section_name"], today, p
        )
    finally:
        conn.close()

    return jsonify(
        {
            "show": True,
            "period": p,
            "subject": subj,
            "faculty": fac,
            "seconds_left": seconds_left,
            "grace_minutes": att.GRACE_MINUTES,
        }
    )


@app.route("/student/attendance")
@student_required
def student_attendance_page():
    return render_template("attendance.html")


@app.route("/student/mark_attendance", methods=["POST"])
@student_required
def student_mark_attendance():
    sid = session["student_id"]
    prof = student_profile(sid)
    if not prof:
        return jsonify({"ok": False, "error": "Profile not found"}), 400

    today = datetime.date.today().isoformat()
    conn_h = get_db_connection()
    try:
        if att.is_public_holiday(conn_h, today):
            return jsonify({"ok": False, "error": "Holiday"}), 400
    finally:
        conn_h.close()

    info = att.current_period_info()
    if not info:
        return jsonify({"ok": False, "error": "Outside teaching hours"}), 400

    p = info["period"]

    payload = request.get_json(silent=True) or {}
    data_url = (payload.get("captured_image") or "").strip()
    if not data_url:
        return jsonify({"ok": False, "error": "No image"}), 400

    try:
        image = decode_image_from_base64(data_url)
        unknown = extract_iris_template(image)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    templates = fetch_own_encodings(sid)
    if not templates:
        return jsonify({"ok": False, "error": "No iris templates on file. Contact admin."}), 400

    best = max(compare_templates(t, unknown) for t in templates)
    if best < 0.72:
        return jsonify({"ok": False, "error": "Verification failed. Try again."}), 400

    conn = get_db_connection()
    subj, _ = att.schedule_subject_for_period(
        conn, prof["branch_name"], prof["year"], prof["section_name"], today, p
    )
    att.upsert_attendance(
        conn,
        student_id=sid,
        name=prof["name"],
        department=prof["dept_name"],
        branch=prof["branch_name"],
        section=prof["section_name"],
        iso_date=today,
        period=p,
        subject=subj,
        status="Present",
        attendance_type="automatic",
        marked_time=datetime.datetime.now().time().strftime("%H:%M:%S"),
    )
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "message": "Attendance marked", "period": p})


@app.route("/calendar")
@student_required
def student_calendar():
    import calendar as calmod

    prof = student_profile(session["student_id"])
    year = request.args.get("year", type=int) or datetime.date.today().year
    month = request.args.get("month", type=int) or datetime.date.today().month
    conn = get_db_connection()
    flags = att.month_calendar_flags(conn, session["student_id"], year, month)
    conn.close()
    _, dim = calmod.monthrange(year, month)
    cells = []
    for day in range(1, dim + 1):
        iso = datetime.date(year, month, day).isoformat()
        cells.append({"day": day, "iso": iso, "flag": flags.get(iso, "none")})
    return render_template(
        "calendar.html",
        profile=prof,
        year=year,
        month=month,
        cells=cells,
    )


# -----------------------------------------------------------------------------
# Lecturer area
# -----------------------------------------------------------------------------
@app.route("/lecturer_dashboard")
@lecturer_required
def lecturer_dashboard():
    conn = get_db_connection()
    departments = conn.execute("SELECT id, name FROM departments ORDER BY name").fetchall()
    conn.close()
    return render_template("lecturer_dashboard.html", departments=departments)


@app.route("/teacher")
def teacher_alias():
    return redirect(url_for("lecturer_dashboard"))


@app.route("/lecturer/section", methods=["POST"])
@lecturer_required
def lecturer_select_section():
    section_id = request.form.get("section_id", type=int)
    if not section_id or not section_exists(section_id):
        flash("Please choose a valid section.")
        return redirect(url_for("lecturer_dashboard"))
    return redirect(url_for("lecturer_attendance", section_id=section_id))


@app.route("/lecturer/attendance/<int:section_id>", methods=["GET", "POST"])
@lecturer_required
def lecturer_attendance(section_id):
    if not section_exists(section_id):
        flash("Section not found.")
        return redirect(url_for("lecturer_dashboard"))

    labels = get_section_labels(section_id)
    if not labels:
        flash("Unable to resolve section hierarchy.")
        return redirect(url_for("lecturer_dashboard"))

    today = datetime.date.today().isoformat()
    conn = get_db_connection()
    holiday = att.is_public_holiday(conn, today)
    conn.close()
    if holiday:
        flash("Today is a scheduled holiday.")
        return redirect(url_for("lecturer_dashboard"))

    if request.method == "POST" and request.form.get("action") == "manual_toggle":
        return _lecturer_manual_toggle(section_id, labels)

    if request.method == "POST":
        uploaded = request.files.get("image")
        captured_image = (request.form.get("captured_image") or "").strip()

        if not captured_image and (not uploaded or uploaded.filename == ""):
            flash("Please upload an image or use the webcam capture on submit.")
            return redirect(url_for("lecturer_attendance", section_id=section_id))

        try:
            if captured_image:
                image = decode_image_from_base64(captured_image)
            else:
                image = load_image_from_file(uploaded)
            unknown_template = extract_iris_template(image)
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("lecturer_attendance", section_id=section_id))

        known_faces = fetch_known_encodings_for_section(section_id)
        if not known_faces:
            flash("No enrolled students with iris encodings in this section.")
            return redirect(url_for("lecturer_attendance", section_id=section_id))

        similarities = np.array(
            [compare_templates(f["encoding"], unknown_template) for f in known_faces]
        )
        match_index = int(np.nanargmax(similarities))
        best_similarity = float(similarities[match_index])

        if best_similarity < 0.70:
            flash("Unknown person or low confidence match for this section.")
            return redirect(url_for("lecturer_attendance", section_id=section_id))

        matched = known_faces[match_index]
        info = att.current_period_info()
        if not info:
            flash("Attendance can only be marked during class hours.")
            return redirect(url_for("lecturer_attendance", section_id=section_id))

        prof = student_profile(matched["student_id"])
        if not prof:
            flash("Student profile missing.")
            return redirect(url_for("lecturer_attendance", section_id=section_id))

        p = info["period"]
        conn = get_db_connection()
        subj, _ = att.schedule_subject_for_period(
            conn,
            prof["branch_name"],
            prof["year"],
            prof["section_name"],
            today,
            p,
        )
        att.upsert_attendance(
            conn,
            student_id=matched["student_id"],
            name=matched["name"],
            department=prof["dept_name"],
            branch=prof["branch_name"],
            section=prof["section_name"],
            iso_date=today,
            period=p,
            subject=subj,
            status="Present",
            attendance_type="automatic",
            marked_time=datetime.datetime.now().time().strftime("%H:%M:%S"),
        )
        conn.commit()
        conn.close()
        flash(f"Attendance marked for {matched['name']} (period {p}).")
        return redirect(url_for("lecturer_attendance", section_id=section_id))

    conn = get_db_connection()
    roster = conn.execute(
        """
        SELECT student_id, name, year FROM students WHERE section_id = ?
        ORDER BY name
        """,
        (section_id,),
    ).fetchall()
    conn.close()

    info = att.current_period_info()
    period = info["period"] if info else None

    return render_template(
        "attendance_section.html",
        section_id=section_id,
        labels=labels,
        students=roster,
        current_period=period,
    )


def _lecturer_manual_toggle(section_id, labels):
    student_id = (request.form.get("student_id") or "").strip()
    status = (request.form.get("status") or "Present").strip()
    period = request.form.get("period", type=int)

    if not student_id or period not in range(1, 8):
        flash("Invalid manual attendance request.")
        return redirect(url_for("lecturer_attendance", section_id=section_id))

    conn = get_db_connection()
    row = conn.execute(
        "SELECT student_id, name, year, section_id FROM students WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    conn.close()
    if not row or row["section_id"] != section_id:
        flash("Student not in this section.")
        return redirect(url_for("lecturer_attendance", section_id=section_id))

    prof = student_profile(student_id)
    today = datetime.date.today().isoformat()
    conn = get_db_connection()
    subj, _ = att.schedule_subject_for_period(
        conn,
        prof["branch_name"],
        prof["year"],
        prof["section_name"],
        today,
        period,
    )
    att.upsert_attendance(
        conn,
        student_id=student_id,
        name=row["name"],
        department=prof["dept_name"],
        branch=prof["branch_name"],
        section=prof["section_name"],
        iso_date=today,
        period=period,
        subject=subj,
        status=status,
        attendance_type="manual",
        marked_time=datetime.datetime.now().time().strftime("%H:%M:%S"),
    )
    conn.commit()
    conn.close()
    flash("Attendance updated.")
    return redirect(url_for("lecturer_attendance", section_id=section_id))


@app.route("/lecturer/attendance/edit", methods=["GET", "POST"])
@lecturer_required
def lecturer_edit_attendance():
    if request.method == "POST":
        rid = request.form.get("record_id", type=int)
        status = (request.form.get("status") or "").strip()
        if rid and status in ("Present", "Absent"):
            conn = get_db_connection()
            conn.execute(
                "UPDATE attendance SET status = ?, attendance_type = 'manual' WHERE id = ?",
                (status, rid),
            )
            conn.commit()
            conn.close()
            flash("Record updated.")
        return redirect(url_for("lecturer_edit_attendance"))

    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT id, student_id, name, date, period, subject, status, attendance_type
        FROM attendance
        ORDER BY date DESC, period DESC
        LIMIT 200
        """
    ).fetchall()
    conn.close()
    return render_template("lecturer_edit_attendance.html", records=rows)


# -----------------------------------------------------------------------------
# Admin panel
# -----------------------------------------------------------------------------
@app.route("/admin_dashboard")
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    dept_count = conn.execute("SELECT COUNT(*) AS c FROM departments").fetchone()["c"]
    student_count = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
    att_count = conn.execute("SELECT COUNT(*) AS c FROM attendance").fetchone()["c"]
    conn.close()
    return render_template(
        "admin_dashboard.html",
        dept_count=dept_count,
        student_count=student_count,
        att_count=att_count,
    )


@app.route("/admin")
@admin_required
def admin_alias():
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/department", methods=["GET", "POST"])
@admin_required
def add_department():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Department name is required.")
            return redirect(url_for("add_department"))
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO departments (name) VALUES (?)", (name,))
            conn.commit()
            flash(f"Department '{name}' added.")
        except sqlite3.IntegrityError:
            flash("A department with that name already exists.")
        finally:
            conn.close()
        return redirect(url_for("add_department"))
    return render_template("add_department.html")


@app.route("/admin/branch", methods=["GET", "POST"])
@admin_required
def add_branch():
    conn = get_db_connection()
    departments = conn.execute("SELECT id, name FROM departments ORDER BY name").fetchall()
    conn.close()

    if request.method == "POST":
        dept_id = request.form.get("department_id", type=int)
        name = (request.form.get("name") or "").strip()
        if not dept_id or not name:
            flash("Department and branch name are required.")
            return redirect(url_for("add_branch"))
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO branches (department_id, name) VALUES (?, ?)",
                (dept_id, name),
            )
            conn.commit()
            flash(f"Branch '{name}' added.")
        except sqlite3.IntegrityError:
            flash("That branch already exists under the selected department.")
        finally:
            conn.close()
        return redirect(url_for("add_branch"))

    return render_template("add_branch.html", departments=departments)


@app.route("/admin/section", methods=["GET", "POST"])
@admin_required
def add_section():
    conn = get_db_connection()
    departments = conn.execute("SELECT id, name FROM departments ORDER BY name").fetchall()
    conn.close()

    if request.method == "POST":
        branch_id = request.form.get("branch_id", type=int)
        name = (request.form.get("name") or "").strip()
        if not branch_id or not name:
            flash("Branch and section name are required.")
            return redirect(url_for("add_section"))
        conn = get_db_connection()
        row = conn.execute("SELECT 1 FROM branches WHERE id = ?", (branch_id,)).fetchone()
        if not row:
            conn.close()
            flash("Invalid branch.")
            return redirect(url_for("add_section"))
        try:
            conn.execute(
                "INSERT INTO sections (branch_id, name) VALUES (?, ?)",
                (branch_id, name),
            )
            conn.commit()
            flash(f"Section '{name}' added.")
        except sqlite3.IntegrityError:
            flash("That section already exists for the selected branch.")
        finally:
            conn.close()
        return redirect(url_for("add_section"))

    return render_template("add_section.html", departments=departments)


@app.route("/admin/student", methods=["GET", "POST"])
@admin_required
def add_student():
    conn = get_db_connection()
    departments = conn.execute("SELECT id, name FROM departments ORDER BY name").fetchall()
    conn.close()

    if request.method == "POST":
        student_id = (request.form.get("student_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        year = (request.form.get("year") or "1st").strip()
        department_id = request.form.get("department_id", type=int)
        branch_id = request.form.get("branch_id", type=int)
        section_id = request.form.get("section_id", type=int)
        captured_image = (request.form.get("captured_image") or "").strip()
        uploaded_files = request.files.getlist("images")

        if not student_id or not name or not department_id or not branch_id or not section_id:
            flash("Student ID, name, department, branch, and section are required.")
            return redirect(url_for("add_student"))

        if not captured_image and (
            not uploaded_files or all(f.filename == "" for f in uploaded_files)
        ):
            flash("Please provide at least one iris image (webcam capture or upload).")
            return redirect(url_for("add_student"))

        conn = get_db_connection()
        try:
            sec = conn.execute(
                """
                SELECT s.id, s.branch_id, b.department_id
                FROM sections s
                JOIN branches b ON b.id = s.branch_id
                WHERE s.id = ?
                """,
                (section_id,),
            ).fetchone()
        finally:
            conn.close()

        if not sec or sec["branch_id"] != branch_id or sec["department_id"] != department_id:
            flash("Selected section does not match department and branch.")
            return redirect(url_for("add_student"))

        templates = []
        errors = []
        if captured_image:
            try:
                image = decode_image_from_base64(captured_image)
                templates.append(extract_iris_template(image))
            except ValueError as exc:
                errors.append(f"Camera capture: {exc}")

        for uploaded in uploaded_files:
            if uploaded and uploaded.filename:
                try:
                    image = load_image_from_file(uploaded)
                    templates.append(extract_iris_template(image))
                except ValueError as exc:
                    errors.append(f"{uploaded.filename}: {exc}")

        if not templates:
            flash(
                "No valid iris encoding created. "
                + (" ".join(errors) + " " if errors else "")
                + "Please try again."
            )
            return redirect(url_for("add_student"))

        conn = get_db_connection()
        try:
            try:
                conn.execute(
                    """
                    INSERT INTO students (student_id, name, department_id, branch_id, section_id, year)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (student_id, name, department_id, branch_id, section_id, year),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("This student ID already exists.")
                return redirect(url_for("add_student"))

            try:
                for tmpl in templates:
                    _insert_encoding_cursor(conn, student_id, tmpl)
                conn.commit()
                flash(f"Student {name} enrolled with {len(templates)} encoding(s).")
            except (ValueError, sqlite3.IntegrityError) as exc:
                conn.rollback()
                flash(
                    "Student record was saved, but iris encodings could not be stored "
                    f"({exc}). You can add encodings later from the student list (Iris)."
                )
        finally:
            conn.close()

        return redirect(url_for("view_students"))

    return render_template("add_student.html", departments=departments)


@app.route("/admin/students")
@admin_required
def view_students():
    q = (request.args.get("q") or "").strip()
    conn = get_db_connection()
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            """
            SELECT s.student_id, s.name, s.year,
                   d.name AS department, b.name AS branch, sec.name AS section
            FROM students s
            JOIN departments d ON d.id = s.department_id
            JOIN branches b ON b.id = s.branch_id
            JOIN sections sec ON sec.id = s.section_id
            WHERE s.student_id LIKE ? OR s.name LIKE ?
            ORDER BY d.name, b.name, sec.name, s.name
            """,
            (like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT s.student_id, s.name, s.year,
                   d.name AS department, b.name AS branch, sec.name AS section
            FROM students s
            JOIN departments d ON d.id = s.department_id
            JOIN branches b ON b.id = s.branch_id
            JOIN sections sec ON sec.id = s.section_id
            ORDER BY d.name, b.name, sec.name, s.name
            """
        ).fetchall()
    conn.close()
    return render_template("view_students.html", students=rows, query=q)


@app.route("/admin/student/delete/<student_id>", methods=["POST"])
@admin_required
def delete_student(student_id):
    conn = get_db_connection()
    row = conn.execute("SELECT username FROM students WHERE student_id = ?", (student_id,)).fetchone()
    if row and row["username"]:
        conn.execute("DELETE FROM users_login WHERE username = ?", (row["username"],))
    conn.execute("DELETE FROM attendance WHERE student_id = ?", (student_id,))
    conn.execute("DELETE FROM encodings WHERE student_id = ?", (student_id,))
    conn.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
    conn.commit()
    conn.close()
    flash("Student removed (encodings deleted).")
    return redirect(url_for("view_students"))


@app.route("/admin/student/<student_id>/encodings", methods=["GET", "POST"])
@admin_required
def add_student_encodings(student_id):
    conn = get_db_connection()
    exists = conn.execute(
        "SELECT 1 FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    conn.close()
    if not exists:
        flash("Student not found.")
        return redirect(url_for("view_students"))

    if request.method == "POST":
        captured_image = (request.form.get("captured_image") or "").strip()
        uploaded_files = request.files.getlist("images")
        templates = []
        errors = []
        if captured_image:
            try:
                image = decode_image_from_base64(captured_image)
                templates.append(extract_iris_template(image))
            except ValueError as exc:
                errors.append(f"Camera capture: {exc}")
        for uploaded in uploaded_files:
            if uploaded and uploaded.filename:
                try:
                    image = load_image_from_file(uploaded)
                    templates.append(extract_iris_template(image))
                except ValueError as exc:
                    errors.append(f"{uploaded.filename}: {exc}")
        if not templates:
            flash("No valid iris samples. " + " ".join(errors))
            return redirect(url_for("add_student_encodings", student_id=student_id))
        conn = get_db_connection()
        try:
            try:
                for tmpl in templates:
                    _insert_encoding_cursor(conn, student_id, tmpl)
                conn.commit()
                flash(f"Added {len(templates)} encoding(s) for {student_id}.")
            except ValueError as exc:
                conn.rollback()
                flash(f"Could not save encodings: {exc}")
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                flash(f"Could not save encodings (database constraint): {exc}")
        finally:
            conn.close()
        return redirect(url_for("view_students"))

    return render_template("add_student_encodings.html", student_id=student_id)


@app.route("/admin/lecturers")
@admin_required
def admin_lecturers():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT lecturer_id, name, department, username FROM lecturers ORDER BY name"
    ).fetchall()
    conn.close()
    return render_template("admin_lecturers.html", lecturers=rows)


@app.route("/admin/lecturer/delete/<lecturer_id>", methods=["POST"])
@admin_required
def delete_lecturer(lecturer_id):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT username FROM lecturers WHERE lecturer_id = ?", (lecturer_id,)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM users_login WHERE username = ?", (row["username"],))
        conn.execute("DELETE FROM lecturers WHERE lecturer_id = ?", (lecturer_id,))
        conn.commit()
    conn.close()
    flash("Lecturer removed.")
    return redirect(url_for("admin_lecturers"))


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        college_name = (request.form.get("college_name") or "").strip()
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO college_settings (key, value) VALUES ('college_name', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (college_name or "College Attendance Portal",),
        )
        file = request.files.get("logo")
        if file and file.filename:
            filename = secure_filename(file.filename)
            ext = Path(filename).suffix.lower() or ".png"
            rel = f"uploads/college_logo{ext}"
            dest = UPLOAD_DIR / f"college_logo{ext}"
            file.save(dest)
            conn.execute(
                """
                INSERT INTO college_settings (key, value) VALUES ('college_logo', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (rel,),
            )
        conn.commit()
        conn.close()
        flash("College settings saved.")
        return redirect(url_for("admin_settings"))

    return render_template(
        "admin_settings.html",
        saved_college_name=get_setting("college_name", "College Attendance Portal"),
    )


@app.route("/admin/holidays", methods=["GET", "POST"])
@admin_required
def admin_holidays():
    if request.method == "POST":
        iso = (request.form.get("holiday_date") or "").strip()
        hname = (request.form.get("holiday_name") or "").strip()
        if iso and hname:
            conn = get_db_connection()
            try:
                conn.execute(
                    "INSERT INTO holidays (date, name) VALUES (?, ?)", (iso, hname)
                )
                conn.commit()
                flash("Holiday added.")
            except sqlite3.IntegrityError:
                flash("That date already exists.")
            finally:
                conn.close()
        return redirect(url_for("admin_holidays"))

    conn = get_db_connection()
    rows = conn.execute("SELECT id, date, name FROM holidays ORDER BY date").fetchall()
    conn.close()
    return render_template("admin_holidays.html", holidays=rows)


@app.route("/admin/holidays/delete/<int:hid>", methods=["POST"])
@admin_required
def admin_holiday_delete(hid):
    conn = get_db_connection()
    conn.execute("DELETE FROM holidays WHERE id = ?", (hid,))
    conn.commit()
    conn.close()
    flash("Holiday removed.")
    return redirect(url_for("admin_holidays"))


@app.route("/admin/schedule", methods=["GET", "POST"])
@admin_required
def admin_schedule():
    if request.method == "POST":
        department = (request.form.get("department") or "").strip()
        year = (request.form.get("year") or "").strip()
        section = (request.form.get("section") or "").strip()
        subject = (request.form.get("subject") or "").strip()
        faculty = (request.form.get("faculty") or "").strip()
        start_time = (request.form.get("start_time") or "").strip()
        end_time = (request.form.get("end_time") or "").strip()
        stype = (request.form.get("type") or "class").strip()
        sdate = (request.form.get("date") or "").strip()
        if not all([department, year, section, subject, faculty, start_time, end_time, sdate]):
            flash("All schedule fields are required.")
            return redirect(url_for("admin_schedule"))
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO schedules (department, year, section, subject, faculty, start_time, end_time, type, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (department, year, section, subject, faculty, start_time, end_time, stype, sdate),
        )
        conn.commit()
        conn.close()
        flash("Schedule entry added.")
        return redirect(url_for("admin_schedule"))

    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM schedules ORDER BY date DESC, start_time LIMIT 200"
    ).fetchall()
    conn.close()
    return render_template("schedule.html", schedules=rows)


@app.route("/admin/attendance")
@admin_required
def admin_view_attendance():
    dept = (request.args.get("department") or "").strip()
    year = (request.args.get("year") or "").strip()
    sec = (request.args.get("section") or "").strip()
    date_f = (request.args.get("date") or "").strip()

    conn = get_db_connection()
    query = """
        SELECT id, student_id, name, department, branch, section, date, period, subject, status,
               attendance_type, marked_time
        FROM attendance
        WHERE 1=1
    """
    params = []
    if dept:
        query += " AND department = ?"
        params.append(dept)
    if year:
        query += " AND student_id IN (SELECT student_id FROM students WHERE year = ?)"
        params.append(year)
    if sec:
        query += " AND section = ?"
        params.append(sec)
    if date_f:
        query += " AND date = ?"
        params.append(date_f)
    query += " ORDER BY date DESC, period DESC, name LIMIT 500"
    records = conn.execute(query, params).fetchall()
    conn.close()
    return render_template("admin_attendance.html", records=records)


@app.route("/admin/download")
@admin_required
def admin_download():
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT student_id, name, department, branch, section, date, period, subject, status,
               attendance_type, marked_time
        FROM attendance
        ORDER BY date DESC, period DESC
        """
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Student ID",
            "Name",
            "Department",
            "Program",
            "Section",
            "Date",
            "Period",
            "Subject",
            "Status",
            "Type",
            "Marked Time",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["student_id"],
                row["name"],
                row["department"],
                row["branch"],
                row["section"],
                row["date"],
                row["period"],
                row["subject"],
                row["status"],
                row["attendance_type"],
                row["marked_time"],
            ]
        )

    content = output.getvalue().encode("utf-8")
    return send_file(
        io.BytesIO(content),
        mimetype="text/csv",
        as_attachment=True,
        download_name="attendance_records.csv",
    )


# -----------------------------------------------------------------------------
# Legacy routes
# -----------------------------------------------------------------------------
@app.route("/teacher_dashboard")
def teacher_dashboard_legacy():
    if session.get("role") in ("lecturer", "teacher"):
        return redirect(url_for("lecturer_dashboard"))
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard_legacy():
    if session.get("role") == "admin":
        return redirect(url_for("admin_view_attendance"))
    flash("Sign in as admin to view attendance records.")
    return redirect(url_for("login"))


@app.route("/download")
def download_legacy():
    if session.get("role") == "admin":
        return redirect(url_for("admin_download"))
    flash("Sign in as admin to download attendance CSV.")
    return redirect(url_for("login"))


@app.route("/attendance")
def attendance_legacy():
    if session.get("role") == "student":
        return redirect(url_for("student_attendance_page"))
    if session.get("role") in ("lecturer", "teacher"):
        return redirect(url_for("lecturer_dashboard"))
    return redirect(url_for("login"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
