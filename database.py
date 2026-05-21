"""
SQLite access, schema creation, and lightweight migrations for the college
attendance system. Keeps connection settings tuned for Windows / OneDrive.
"""
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "app.db"

DEFAULT_DEPARTMENTS = ("CSE", "CSE-AIML", "EEE", "ECE", "Civil")


def get_db_connection():
    """
    Open SQLite with settings that reduce 'database is locked' errors on Windows
    (e.g. OneDrive folders, Flask reloader, concurrent tabs): longer busy timeout,
    WAL journal, and foreign keys enabled.
    """
    conn = sqlite3.connect(
        str(DATABASE_PATH),
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _table_columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def init_db():
    """Create tables, migrate legacy shapes, and seed minimal reference data."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users_login (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY(department_id) REFERENCES departments(id),
            UNIQUE(department_id, name)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY(branch_id) REFERENCES branches(id),
            UNIQUE(branch_id, name)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department_id INTEGER NOT NULL,
            branch_id INTEGER NOT NULL,
            section_id INTEGER NOT NULL,
            FOREIGN KEY(department_id) REFERENCES departments(id),
            FOREIGN KEY(branch_id) REFERENCES branches(id),
            FOREIGN KEY(section_id) REFERENCES sections(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS encodings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            encoding BLOB NOT NULL,
            FOREIGN KEY(student_id) REFERENCES students(student_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT,
            name TEXT,
            department TEXT,
            branch TEXT,
            section TEXT,
            date TEXT,
            time TEXT
        )
        """
    )

    # --- encodings legacy migration ---
    enc_cols = _table_columns(cursor, "encodings")
    if enc_cols and "user_id" in enc_cols and "student_id" not in enc_cols:
        cursor.execute("ALTER TABLE encodings RENAME COLUMN user_id TO student_id")

    # Rebuild encodings if missing id column or no FK metadata (old DBs / schema drift).
    enc_cols = _table_columns(cursor, "encodings")
    cursor.execute("PRAGMA foreign_key_list(encodings)")
    enc_fks = cursor.fetchall()
    if enc_cols and ("id" not in enc_cols or not enc_fks):
        cursor.execute(
            """
            CREATE TABLE encodings_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                encoding BLOB NOT NULL,
                FOREIGN KEY(student_id) REFERENCES students(student_id)
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO encodings_new (student_id, encoding)
            SELECT e.student_id, e.encoding
            FROM encodings e
            JOIN students s ON s.student_id = e.student_id
            """
        )
        cursor.execute("DROP TABLE encodings")
        cursor.execute("ALTER TABLE encodings_new RENAME TO encodings")

    # --- attendance legacy migration ---
    att_cols = _table_columns(cursor, "attendance")
    if att_cols and "user_id" in att_cols and "student_id" not in att_cols:
        cursor.execute("ALTER TABLE attendance RENAME TO attendance_legacy")
        cursor.execute(
            """
            CREATE TABLE attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT,
                name TEXT,
                department TEXT,
                branch TEXT,
                section TEXT,
                date TEXT,
                time TEXT
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO attendance (student_id, name, department, branch, section, date, time)
            SELECT user_id, name, '', '', '', date, time FROM attendance_legacy
            """
        )
        cursor.execute("DROP TABLE attendance_legacy")

    # --- students: year, username, password ---
    st_cols = _table_columns(cursor, "students")
    if "year" not in st_cols:
        cursor.execute("ALTER TABLE students ADD COLUMN year TEXT NOT NULL DEFAULT '1st'")
    if "username" not in st_cols:
        cursor.execute("ALTER TABLE students ADD COLUMN username TEXT")
    if "password" not in st_cols:
        cursor.execute("ALTER TABLE students ADD COLUMN password TEXT")

    # --- attendance extended columns ---
    att_cols = _table_columns(cursor, "attendance")
    if "subject" not in att_cols:
        cursor.execute("ALTER TABLE attendance ADD COLUMN subject TEXT DEFAULT ''")
    if "period" not in att_cols:
        cursor.execute("ALTER TABLE attendance ADD COLUMN period INTEGER DEFAULT 1")
    if "status" not in att_cols:
        cursor.execute("ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'Present'")
    if "attendance_type" not in att_cols:
        cursor.execute(
            "ALTER TABLE attendance ADD COLUMN attendance_type TEXT DEFAULT 'automatic'"
        )
    if "marked_time" not in att_cols:
        cursor.execute("ALTER TABLE attendance ADD COLUMN marked_time TEXT")

    # Backfill marked_time from legacy time where missing
    cursor.execute(
        """
        UPDATE attendance
        SET marked_time = COALESCE(marked_time, time, '09:20:00')
        WHERE marked_time IS NULL OR marked_time = ''
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS lecturers (
            lecturer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department TEXT NOT NULL,
            year TEXT NOT NULL,
            section TEXT NOT NULL,
            subject TEXT NOT NULL,
            faculty TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            type TEXT NOT NULL,
            date TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS holidays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS college_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    try:
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_student_date_period
            ON attendance(student_id, date, period)
            """
        )
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_students_username
            ON students(username) WHERE username IS NOT NULL AND username != ''
            """
        )
    except sqlite3.OperationalError:
        pass

    # Role rename: teacher -> lecturer
    cursor.execute(
        "UPDATE users_login SET role = 'lecturer' WHERE role = 'teacher'"
    )

    # Seed default admin / lecturer if empty
    if cursor.execute("SELECT COUNT(*) AS c FROM users_login").fetchone()["c"] == 0:
        cursor.execute(
            "INSERT INTO users_login (username, password, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("admin123"), "admin"),
        )
        cursor.execute(
            "INSERT INTO users_login (username, password, role) VALUES (?, ?, ?)",
            ("lecturer", generate_password_hash("lecturer123"), "lecturer"),
        )

    # Seed default departments (college structure)
    existing_depts = {
        r["name"] for r in cursor.execute("SELECT name FROM departments").fetchall()
    }
    for dname in DEFAULT_DEPARTMENTS:
        if dname not in existing_depts:
            try:
                cursor.execute("INSERT INTO departments (name) VALUES (?)", (dname,))
            except sqlite3.IntegrityError:
                pass

    # Default college name
    row = cursor.execute(
        "SELECT 1 FROM college_settings WHERE key = ?", ("college_name",)
    ).fetchone()
    if not row:
        cursor.execute(
            "INSERT INTO college_settings (key, value) VALUES (?, ?)",
            ("college_name", "College Attendance Portal"),
        )

    conn.commit()
    conn.close()
