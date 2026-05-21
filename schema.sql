-- College Attendance Management System — SQLite schema
-- Applied by `database.init_db()`; this file documents the full layout.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users_login (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    department_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    FOREIGN KEY (department_id) REFERENCES departments (id),
    UNIQUE (department_id, name)
);

CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    FOREIGN KEY (branch_id) REFERENCES branches (id),
    UNIQUE (branch_id, name)
);

CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL,
    branch_id INTEGER NOT NULL,
    section_id INTEGER NOT NULL,
    year TEXT NOT NULL DEFAULT '1st',
    username TEXT,
    password TEXT,
    FOREIGN KEY (department_id) REFERENCES departments (id),
    FOREIGN KEY (branch_id) REFERENCES branches (id),
    FOREIGN KEY (section_id) REFERENCES sections (id)
);

CREATE TABLE IF NOT EXISTS encodings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    encoding BLOB NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students (student_id)
);

CREATE TABLE IF NOT EXISTS lecturers (
    lecturer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT,
    name TEXT,
    department TEXT,
    branch TEXT,
    section TEXT,
    date TEXT,
    time TEXT,
    subject TEXT DEFAULT '',
    period INTEGER DEFAULT 1,
    status TEXT DEFAULT 'Present',
    attendance_type TEXT DEFAULT 'automatic',
    marked_time TEXT
);

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
);

CREATE TABLE IF NOT EXISTS holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS college_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_student_date_period
ON attendance (student_id, date, period);
