"""database.py — SPAS v5.1 | Consolidated Master Schema | Request-scoped connection via Flask g"""
import sqlite3
import os
import secrets
import string
from datetime import datetime
from werkzeug.security import generate_password_hash

# Paths
DB_PATH   = os.path.join(os.path.dirname(__file__), "instance", "spas.db")
CRED_PATH = os.path.join(os.path.dirname(__file__), "instance", "credential.txt")

# ── Connection Management ──────────────────────────────────────────────
def get_db():
    """Returns a thread-safe connection using Flask g when available."""
    try:
        from flask import g
        if not hasattr(g, "_spas_db"):
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            g._spas_db = conn
        return g._spas_db
    except RuntimeError:
        # Outside request context (seeding, scripts)
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

def close_db(e=None):
    try:
        from flask import g
        db = g.pop("_spas_db", None)
        if db is not None:
            db.close()
    except RuntimeError:
        pass

def dict_row(row):   return dict(row) if row else None
def dict_rows(rows): return [dict(r) for r in rows]

def generate_strong_password(length=14) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = ''.join(secrets.choice(chars) for _ in range(length))
        if (any(c.isupper() for c in pwd) and any(c.islower() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in "!@#$%^&*" for c in pwd)):
            return pwd

def make_admin_username(college_name: str, college_code: str) -> str:
    prefix = ''.join(c.lower() for c in college_name if c.isalpha())[:4]
    return f"{prefix}_{college_code}"

# ── Master Schema ──────────────────────────────────────────────────────
MASTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS colleges (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    college_code     TEXT UNIQUE NOT NULL,
    university       TEXT DEFAULT 'RGPV Bhopal',
    city             TEXT,
    state            TEXT DEFAULT 'Madhya Pradesh',
    contact_email    TEXT DEFAULT '',
    contact_phone    TEXT DEFAULT '',
    website          TEXT DEFAULT '',
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT UNIQUE NOT NULL,
    email            TEXT UNIQUE,
    password         TEXT NOT NULL,
    role             TEXT NOT NULL, -- super_admin, admin, teacher, student
    full_name        TEXT,
    department       TEXT,
    college_id       INTEGER REFERENCES colleges(id),
    student_id       INTEGER,
    must_change_password INTEGER DEFAULT 0,
    profile_photo    TEXT,
    bio              TEXT,
    phone            TEXT,
    is_active        INTEGER DEFAULT 1,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS college_registrations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    college_name     TEXT NOT NULL,
    college_code     TEXT UNIQUE NOT NULL,
    city             TEXT,
    admin_email      TEXT NOT NULL,
    admin_full_name  TEXT NOT NULL,
    status           TEXT DEFAULT 'pending',
    submitted_at     TEXT DEFAULT (datetime('now')),
    reviewed_at      TEXT
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token            TEXT UNIQUE NOT NULL,
    expires_at       TEXT NOT NULL,
    used             INTEGER DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS students (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_no    TEXT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    email            TEXT UNIQUE,
    branch           TEXT,
    branch_code      TEXT,
    programme        TEXT DEFAULT 'BE',
    current_semester INTEGER DEFAULT 1,
    college_id       INTEGER REFERENCES colleges(id),
    college_code     TEXT,
    gender           TEXT,
    profile_photo    TEXT,
    bio              TEXT,
    phone            TEXT,
    parent_phone     TEXT,
    address          TEXT,
    last_synced      TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS student_registrations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_no    TEXT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    email            TEXT UNIQUE,
    phone            TEXT,
    parent_phone     TEXT,
    address          TEXT,
    branch           TEXT,
    branch_code      TEXT,
    programme        TEXT DEFAULT 'BE',
    semester         INTEGER DEFAULT 1,
    college_id       INTEGER REFERENCES colleges(id),
    college_code     TEXT,
    gender           TEXT,
    registered_by    TEXT DEFAULT 'self',
    teacher_id       INTEGER REFERENCES users(id),
    status           TEXT DEFAULT 'pending',
    reject_reason    TEXT,
    submitted_at     TEXT DEFAULT (datetime('now')),
    reviewed_at      TEXT
);

CREATE TABLE IF NOT EXISTS semester_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    semester_no   INTEGER NOT NULL,
    sgpa          REAL, cgpa REAL,
    total_marks   REAL, max_marks REAL,
    percentage    REAL, result TEXT,
    attendance    REAL, backlog_count INTEGER DEFAULT 0,
    fetched_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(student_id, semester_no)
);

CREATE TABLE IF NOT EXISTS subject_marks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    semester_record_id INTEGER NOT NULL REFERENCES semester_records(id) ON DELETE CASCADE,
    subject_code TEXT, subject_name TEXT NOT NULL,
    subject_type TEXT DEFAULT 'Theory',
    internal_marks REAL, external_marks REAL,
    total_marks REAL, max_marks REAL DEFAULT 100,
    grade TEXT, grade_point REAL, credits REAL DEFAULT 4,
    status TEXT DEFAULT 'PASS', attendance_pct REAL,
    UNIQUE(semester_record_id, subject_code, subject_type)
);

CREATE TABLE IF NOT EXISTS fetch_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    college_id    INTEGER REFERENCES colleges(id),
    triggered_by  INTEGER REFERENCES users(id),
    college_code  TEXT, branch_code TEXT, branch_name TEXT, semester INTEGER,
    status        TEXT DEFAULT 'pending',
    total_fetched INTEGER DEFAULT 0, total_errors INTEGER DEFAULT 0,
    error_detail  TEXT, source TEXT DEFAULT 'demo',
    started_at    TEXT DEFAULT (datetime('now')), finished_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    message    TEXT NOT NULL,
    type       TEXT DEFAULT 'info',
    link       TEXT,
    is_read    INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ml_predictions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id     INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    semester_no    INTEGER,
    risk_level     TEXT,
    risk_score     REAL,
    predicted_gpa  REAL,
    current_gpa    REAL,
    risk_factors   TEXT,
    recommendations TEXT,
    model_version  TEXT DEFAULT 'v2.1',
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS interventions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id       INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    college_id       INTEGER REFERENCES colleges(id),
    logged_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    intervention_type TEXT NOT NULL,
    notes            TEXT,
    status           TEXT DEFAULT 'Pending',
    follow_up_date   TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS upload_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    college_id    INTEGER REFERENCES colleges(id),
    uploaded_by   INTEGER REFERENCES users(id),
    upload_type   TEXT NOT NULL,
    file_name     TEXT,
    branch_code   TEXT,
    semester_no   INTEGER,
    rows_success  INTEGER DEFAULT 0,
    rows_failed   INTEGER DEFAULT 0,
    error_detail  TEXT,
    status        TEXT DEFAULT 'pending',
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username     TEXT,
    role         TEXT,
    action       TEXT NOT NULL,
    endpoint     TEXT,
    method       TEXT,
    status_code  INTEGER,
    latency_ms   INTEGER,
    ip_address   TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS blocked_ips (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT UNIQUE NOT NULL,
    reason     TEXT,
    blocked_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    college_id       INTEGER REFERENCES colleges(id) ON DELETE CASCADE,
    uploaded_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    upload_type      TEXT NOT NULL,
    semester_no      INTEGER,
    version_no       INTEGER NOT NULL,
    row_count        INTEGER DEFAULT 0,
    snapshot_json    TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS anomaly_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    college_id       INTEGER REFERENCES colleges(id) ON DELETE CASCADE,
    student_id       INTEGER REFERENCES students(id) ON DELETE CASCADE,
    anomaly_type     TEXT,
    description      TEXT,
    severity         TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);
"""

# ── Database Initialization ────────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.executescript(MASTER_SCHEMA)
    conn.commit()
    conn.close()
    upgrade_db()
    _seed()

def upgrade_db():
    """Safely adds missing columns and tables to an existing database."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    # Students table
    for col in ["profile_photo TEXT", "bio TEXT", "phone TEXT", "parent_phone TEXT", "address TEXT", "email TEXT"]:
        try: conn.execute(f"ALTER TABLE students ADD COLUMN {col}")
        except: pass
    # Users table
    for col in ["profile_photo TEXT", "bio TEXT", "phone TEXT", "email TEXT", "is_active INTEGER DEFAULT 1"]:
        try: conn.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except: pass
    # Student registrations table
    for col in ["parent_phone TEXT", "address TEXT", "semester INTEGER DEFAULT 1"]:
        try: conn.execute(f"ALTER TABLE student_registrations ADD COLUMN {col}")
        except: pass
    # Notifications table
    try: conn.execute("ALTER TABLE notifications ADD COLUMN link TEXT")
    except: pass
    # New tables added in v5.1
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dataset_versions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            college_id       INTEGER REFERENCES colleges(id) ON DELETE CASCADE,
            uploaded_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
            upload_type      TEXT NOT NULL,
            semester_no      INTEGER,
            version_no       INTEGER NOT NULL,
            row_count        INTEGER DEFAULT 0,
            snapshot_json    TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anomaly_logs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            college_id       INTEGER REFERENCES colleges(id) ON DELETE CASCADE,
            student_id       INTEGER REFERENCES students(id) ON DELETE CASCADE,
            anomaly_type     TEXT,
            description      TEXT,
            severity         TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        )
    """)
    
    conn.commit()
    conn.close()

def upgrade_db_v2():
    upgrade_db()

# ── Seeding ────────────────────────────────────────────────────────────
def _seed():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        conn.close(); return

    c = conn.cursor()
    # Super Admin
    sa_pwd = generate_strong_password(16)
    c.execute("INSERT INTO users(username,email,password,role,full_name) VALUES(?,?,?,?,?)",
              ("superadmin", "superadmin@spas.edu", generate_password_hash(sa_pwd),
               "super_admin", "Super Administrator"))

    with open(CRED_PATH, "w") as f:
        f.write("=" * 50 + "\n  SPAS — SUPER ADMIN CREDENTIALS\n" + "=" * 50 + "\n")
        f.write(f"  Username : superadmin\n  Password : {sa_pwd}\n")
        f.write("=" * 50 + "\n")
    
    # Demo Data
    c.execute("INSERT INTO colleges(name,college_code,city) VALUES(?,?,?)",
              ("Techno College of Engineering", "0115", "Bhopal"))
    col1 = c.lastrowid
    
    pwd = generate_strong_password()
    c.execute("INSERT INTO users(username,password,role,full_name,college_id,must_change_password) VALUES(?,?,?,?,?,1)",
              ("admin_tce", generate_password_hash(pwd), "admin", "TCE Admin", col1))

    import random
    for bcode, bname in [("CSE", "Computer Science"), ("AIML", "AI & Machine Learning")]:
        demo_stus = []
        for i in range(1, 11):
            enr = f"0115{bcode}{21:02d}{i:03d}"
            demo_stus.append({
                "enrollment_no": enr, "name": f"Student {bcode} {i:02d}",
                "semester_no": 4, "sgpa": round(random.uniform(6, 10), 2),
                "cgpa": round(random.uniform(6.5, 9.5), 2), "attendance": round(random.uniform(65, 98), 1),
                "gender": random.choice(["Male", "Female"]), "result": "PASS"
            })
        persist_fetch(conn, demo_stus, bcode, bname, col1, "0115", 4)

    for row in conn.execute("SELECT id, enrollment_no FROM students").fetchall():
        pwd = generate_strong_password(10)
        c.execute("INSERT INTO users(username,password,role,college_id,student_id) VALUES(?,?,?,?,?)",
                  (row["enrollment_no"].lower(), generate_password_hash(pwd), "student", col1, row["id"]))
    
    conn.commit()
    conn.close()

def persist_fetch(conn, students, branch_code, branch_name, college_id, college_code, max_sem):
    c = conn.cursor()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    for sd in students:
        enr = sd["enrollment_no"]
        sem = sd.get("semester_no", max_sem)
        c.execute("""INSERT OR IGNORE INTO students 
            (enrollment_no,name,branch,branch_code,programme,current_semester,college_id,college_code,gender,last_synced)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (enr, sd["name"], branch_name, branch_code, "BE", sem, college_id, college_code, sd.get("gender", ""), now))
        
        row = c.execute("SELECT id FROM students WHERE enrollment_no=?", (enr,)).fetchone()
        if row:
            sid = row["id"]
            c.execute("""INSERT OR REPLACE INTO semester_records 
                (student_id,semester_no,sgpa,cgpa,attendance,result,fetched_at)
                VALUES(?,?,?,?,?,?,?)""",
                (sid, sem, sd.get("sgpa", 0), sd.get("cgpa", 0), sd.get("attendance", 75), sd.get("result", "PASS"), now))
    conn.commit()

if __name__ == "__main__":
    init_db()
