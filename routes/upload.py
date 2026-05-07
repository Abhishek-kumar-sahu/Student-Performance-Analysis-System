"""routes/upload.py — SPAS v5+
Teacher / Admin can upload CSV/XLSX files:

  • University Results  — Dynamic subject columns with [T]/[P] grade format
      Required : Roll No, Name, Result Status, SGPA, CGPA
      Optional : <SubjectCode>- [T] or <SubjectCode>- [P]  (any number)
      Optional : Fail in … (last column, auto-detected)

  • Attendance  — Subject-wise attendance per student
      Required : Roll No, Name, <SubjectCode>- [T] or [P]  (attendance % values)
      OR legacy: enrollment_no, semester_no, subject_code, subject_name, attendance_pct

Students can ONLY VIEW their own data — they have no upload access.
"""
import csv
import io
import re
from datetime import datetime
from flask import (Blueprint, abort, render_template, request, redirect, url_for,
                   flash, jsonify, Response)
from database import get_db, dict_row, dict_rows
from routes.auth import login_required, role_required, current_user
from security import validate_csrf, sanitize_text, sanitize_name, sanitize_email, sanitize_phone, sanitize_integer, sanitize_alphanumeric, contains_xss, get_client_ip

upload_bp = Blueprint("upload", __name__, url_prefix="/upload")

ALLOWED_TYPES = {"university_results", "attendance"}

# Grade → grade point mapping (10-point scale)
GRADE_POINTS = {
    "O": 10, "A+": 10, "A": 9, "B+": 8, "B": 7,
    "C+": 6, "C": 5, "D": 4, "F": 0,
    "F (ABS)": 0, "F(ABS)": 0, "AB": 0, "ABSENT": 0,
    "-": None, "": None,
}

from .branches import BRANCH_CHOICES, BRANCH_DICT, PROGRAMME_BRANCHES, PROGRAMME_CHOICES

def _grade_to_point(grade_raw):
    """Convert raw grade string to grade point float. Returns None for missing."""
    g = str(grade_raw or "").strip().upper()
    if g in GRADE_POINTS:
        return GRADE_POINTS[g]
    # Try partial match for variants like "F (ABS)"
    for key, val in GRADE_POINTS.items():
        if key and g.startswith(key):
            return val
    return None

def _is_subject_col(col_name):
    """Return True if column header looks like a subject column e.g. CS701- [T] or CS607- [P]"""
    return bool(re.search(r'\[(T|P)\]', str(col_name), re.IGNORECASE))

def _col_type(col_name):
    """Return 'T' for theory, 'P' for practical from column name."""
    m = re.search(r'\[(T|P)\]', str(col_name), re.IGNORECASE)
    return m.group(1).upper() if m else "T"

def _col_code(col_name):
    """Extract subject code from column name like 'CS701- [T]' → 'CS701'"""
    return re.sub(r'[-\s]*\[(T|P)\].*', '', str(col_name), flags=re.IGNORECASE).strip()


# ── Upload page ───────────────────────────────────────────────────────
@upload_bp.route("/", methods=["GET"])
@login_required
@role_required("teacher", "admin")
def upload_page():
    u  = current_user()
    db = get_db()
    if not u.get("college_id"):
        flash("Your account is not linked to a college.", "danger")
        return redirect(url_for("auth.login"))
    logs = dict_rows(db.execute(
        "SELECT ul.*, u.username as by_name FROM upload_logs ul "
        "LEFT JOIN users u ON ul.uploaded_by=u.id "
        "WHERE ul.college_id=? ORDER BY ul.created_at DESC LIMIT 20",
        (u["college_id"],)).fetchall())
    branches = dict_rows(db.execute(
        "SELECT DISTINCT branch_code, branch FROM students WHERE college_id=? ORDER BY branch",
        (u["college_id"],)).fetchall()) or [{"branch_code": c, "branch": n} for c, n in BRANCH_CHOICES]
    return render_template("upload/upload.html", logs=logs, branches=branches,
                           user=u, branch_choices=BRANCH_CHOICES,
                           programme_branches=PROGRAMME_BRANCHES)


# ── Template CSV download ─────────────────────────────────────────────
@upload_bp.route("/template/<upload_type>")
@login_required
@role_required("teacher", "admin")
def download_template(upload_type):
    templates = {
        "university_results":
            "Roll No,Name,Result Status,SGPA,CGPA,CS607- [P],CS701- [P],CS701- [T],CS702- [T],CS703- [T],CS704- [P]\n"
            "0115CS22001,RAHUL SHARMA,PASS,8.50,7.20,A+,A,B+,B,B+,A\n"
            "0115CS22002,PRIYA PATEL,PASS,7.80,6.90,B+,A,C+,B,B,A\n"
            "0115CS22003,AMIT SINGH,\"Fail in CS701- [T]\",5.40,5.80,B+,A,F,B,B,A\n",
        "attendance":
            "Roll No,Name,CS607- [P],CS701- [P],CS701- [T],CS702- [T],CS703- [T],CS704- [P]\n"
            "0115CS22001,RAHUL SHARMA,82.5,90.0,78.0,85.5,76.0,88.0\n"
            "0115CS22002,PRIYA PATEL,67.0,72.5,65.0,80.0,70.5,75.0\n",
    }
    if upload_type not in templates:
        flash("Invalid template type.", "danger")
        return redirect(url_for("upload.upload_page"))
    return Response(
        templates[upload_type],
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=template_{upload_type}.csv"}
    )



# ── File reader helper ────────────────────────────────────────────────
def _read_csv_rows(raw_bytes, ext):
    """Parse CSV or XLSX bytes into list of dicts. Returns (rows, error_str)."""
    if ext == "csv":
        content  = raw_bytes.decode("utf-8-sig")
        rows_raw = list(csv.DictReader(io.StringIO(content)))
        return rows_raw, None
    try:
        import openpyxl
        wb   = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        ws   = wb.active
        all_rows = list(ws.rows)
        if not all_rows:
            return [], "Excel file is empty."
        headers   = [str(c.value or "").strip() for c in all_rows[0]]
        rows_raw  = []
        for row in all_rows[1:]:
            vals = [str(c.value if c.value is not None else "").strip() for c in row]
            if any(v for v in vals):
                rows_raw.append(dict(zip(headers, vals)))
        return rows_raw, None
    except ImportError:
        return [], "openpyxl not installed — use CSV format."
    except Exception as ex:
        return [], f"Excel parse error: {ex}"


# ── Process upload ────────────────────────────────────────────────────
@upload_bp.route("/process", methods=["POST"])
@login_required
@role_required("teacher", "admin")
def process_upload():
    if not validate_csrf():
        abort(403)
    u  = current_user()
    db = get_db()
    if not u or not u.get("college_id"):
        flash("Your account is not linked to a college.", "danger")
        return redirect(url_for("auth.login"))

    upload_type = sanitize_text(request.form.get("upload_type", ""), 30)
    semester_no = sanitize_integer(request.form.get("semester_no", 0), default=0, min_val=0, max_val=12)
    file        = request.files.get("csv_file")

    if upload_type not in ALLOWED_TYPES:
        flash("Invalid upload type selected.", "danger")
        return redirect(url_for("upload.upload_page"))
    if not file or not file.filename:
        flash("No file provided.", "danger")
        return redirect(url_for("upload.upload_page"))
    if not semester_no:
        flash("Please select a semester number.", "danger")
        return redirect(url_for("upload.upload_page"))

    import os as _os
    safe_fname = _os.path.basename(file.filename).replace("..", "").strip()
    ext = safe_fname.rsplit(".", 1)[-1].lower() if "." in safe_fname else ""
    if ext not in {"csv", "xlsx", "xls"}:
        flash("Please upload a valid .csv, .xlsx, or .xls file.", "danger")
        return redirect(url_for("upload.upload_page"))

    raw_bytes = file.read()
    if len(raw_bytes) > 5 * 1024 * 1024:
        flash("File too large. Maximum size is 5 MB.", "danger")
        return redirect(url_for("upload.upload_page"))

    rows_iter, read_err = _read_csv_rows(raw_bytes, ext)
    if read_err:
        flash(f"File error: {read_err}", "danger")
        return redirect(url_for("upload.upload_page"))

    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    # ── Log upload attempt ────────────────────────────────────────────
    db.execute(
        "INSERT INTO upload_logs(college_id,uploaded_by,upload_type,file_name,status) "
        "VALUES(?,?,?,?,'running')",
        (u["college_id"], u["id"], upload_type, safe_fname))
    log_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()

    # ── Detect subject columns from header ───────────────────────────
    headers_raw  = list(rows_iter[0].keys()) if rows_iter else []
    subject_cols = [h for h in headers_raw if _is_subject_col(h)]

    # ══════════════════════════════════════════════════════════════════
    # PERFORMANCE OPTIMISATION — load all students in ONE query
    # instead of one SELECT per row inside the loop.
    # ══════════════════════════════════════════════════════════════════
    student_map = {
        row["enrollment_no"]: row["id"]
        for row in db.execute(
            "SELECT id, enrollment_no FROM students WHERE college_id=?",
            (u["college_id"],)).fetchall()
    }

    success = 0
    failed  = 0
    skipped = 0
    errors  = []

    # Batch collectors — all DB writes go here, committed ONCE at end
    sr_upserts        = []   # semester_records rows
    subj_inserts      = []   # subject_marks rows (results upload)
    student_updates   = []   # (semester_no, now, sid) for current_semester update
    att_sr_inserts    = []   # semester_records to INSERT OR IGNORE for attendance
    att_seen          = set()  # BUG-E: deduplicate duplicate enrollment rows in CSV

    for i, row in enumerate(rows_iter, start=2):
        try:
            enr = (row.get("Roll No") or row.get("enrollment_no") or row.get("roll_no") or "").strip().upper()
            if not enr:
                raise ValueError("Missing Roll No / enrollment_no")

            name_val = (row.get("Name") or row.get("name") or "").strip()
            sgpa_raw = (row.get("SGPA") or row.get("sgpa") or "").strip()

            # BUG-A FIX (SHOWSTOPPER): The original guard was:
            #   if name_val == "NOT FOUND" or sgpa_raw in ("-", "", "N/A"):
            # Attendance CSVs have NO SGPA column, so sgpa_raw is always "".
            # That made every attendance row match "" in ("", ...) → silently
            # skipped → 0 records ever written for any attendance upload.
            # Fix: gate the SGPA-based skip on the upload type.
            if name_val.upper() == "NOT FOUND":
                skipped += 1
                continue
            if upload_type == "university_results" and sgpa_raw in ("-", "", "N/A"):
                skipped += 1
                continue

            # Lookup from pre-loaded map — O(1), no DB round-trip
            sid = student_map.get(enr)
            if not sid:
                raise ValueError(f"Enrollment {enr} not found in your college")

            # ─── UNIVERSITY RESULTS ───────────────────────────────────
            if upload_type == "university_results":
                sgpa = float(sgpa_raw or 0)
                cgpa = float((row.get("CGPA") or row.get("cgpa") or 0))
                result_raw = (row.get("Result Status") or row.get("result") or "PASS").strip()
                result_upper = result_raw.upper()
                if result_upper == "PASS":
                    result = "PASS"
                elif result_upper in ("FAIL", "FAILED", "DETAINED", "ABSENT"):
                    result = "FAIL"
                elif result_raw.lower().startswith("fail in"):
                    result = "FAIL"
                else:
                    result = "PASS"

                if not (0 <= sgpa <= 10): raise ValueError(f"SGPA {sgpa} out of range")
                if not (0 <= cgpa <= 10): raise ValueError(f"CGPA {cgpa} out of range")

                subject_rows = []
                backlog_count = 0
                for col in subject_cols:
                    grade_raw = str(row.get(col) or "").strip()
                    if not grade_raw or grade_raw in ("-", ""):
                        continue
                    sub_code = _col_code(col)
                    sub_type = _col_type(col)
                    gp = _grade_to_point(grade_raw)
                    is_fail = (gp == 0 and grade_raw.upper().replace(" ", "") not in ("", "-"))
                    if is_fail:
                        backlog_count += 1
                    subject_rows.append((
                        sub_code, sub_code,
                        "Theory" if sub_type == "T" else "Practical",
                        grade_raw.upper(), gp if gp is not None else 0,
                        "FAIL" if is_fail else "PASS",
                        (gp or 0) * 10, 100,
                        sid, semester_no,   # stored for later lookup
                    ))

                total_marks = round(sum(s[6] for s in subject_rows), 1)
                max_marks   = len(subject_rows) * 100
                percentage  = round(total_marks / max_marks * 100, 1) if max_marks else 0

                sr_upserts.append((sid, semester_no, sgpa, cgpa, total_marks,
                                   max_marks, percentage, result, backlog_count, now))
                student_updates.append((semester_no, now, sid))

                # Store subject rows tagged with (sid, sem) for after we get sr_id
                subj_inserts.append((sid, semester_no, subject_rows))

            # ─── ATTENDANCE ──────────────────────────────────────────
            elif upload_type == "attendance":
                if not subject_cols:
                    raise ValueError(
                        "No [T]/[P] subject columns found. "
                        "Column headers must look like 'CS701- [T]' or 'CS607- [P]'."
                    )
                att_values = []
                subj_data  = []
                for col in subject_cols:
                    att_raw = str(row.get(col) or "").strip()
                    if not att_raw or att_raw in ("-", ""):
                        continue
                    try:
                        att_pct = float(att_raw)
                    except ValueError:
                        continue
                    if not (0 <= att_pct <= 100):
                        raise ValueError(f"Attendance {att_pct}% out of range for '{col}'")
                    att_values.append(att_pct)
                    sub_code = _col_code(col)
                    sub_type = _col_type(col)
                    subj_data.append((sub_code, sub_type, att_pct))

                if not att_values:
                    raise ValueError(
                        f"No valid attendance values for {enr}. "
                        "Check column headers use [T]/[P] format and values are 0–100."
                    )

                avg_att = round(sum(att_values) / len(att_values), 2)

                # BUG-E FIX: If the same enrollment appears twice in the CSV,
                # the second pass would add a duplicate (sid, semester_no) entry
                # to att_sr_inserts. That causes duplicate sr_att_updates (last
                # write wins — silent data corruption) and may double subject rows.
                # Skip any enrollment+semester pair already queued this upload.
                att_key = (sid, semester_no)
                if att_key in att_seen:
                    skipped += 1
                    continue
                att_seen.add(att_key)

                att_sr_inserts.append((sid, semester_no, now, avg_att, subj_data))
                # BUG-D FIX: Attendance upload never advanced student.current_semester.
                # A student whose only upload is attendance stays at semester=1 forever.
                student_updates.append((semester_no, now, sid))

            success += 1

        except Exception as exc:
            failed += 1
            errors.append(f"Row {i}: {exc}")
            if len(errors) >= 10:
                errors.append("…(showing first 10 errors only)")
                break

    # ══════════════════════════════════════════════════════════════════
    # BATCH WRITE — single transaction, one commit
    # Never call BEGIN manually — SQLite Python driver auto-begins
    # transactions. Just use db.commit() at the end.
    # ══════════════════════════════════════════════════════════════════
    try:

        if upload_type == "university_results" and sr_upserts:
            # 1. Bulk upsert semester_records
            db.executemany(
                "INSERT OR REPLACE INTO semester_records"
                "(student_id,semester_no,sgpa,cgpa,total_marks,max_marks,"
                " percentage,result,backlog_count,fetched_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                sr_upserts)

            # 2. Bulk update student current_semester
            db.executemany(
                "UPDATE students SET current_semester=MAX(current_semester,?), "
                "last_synced=? WHERE id=?",
                student_updates)

            # 3. Fetch all inserted sr_ids in ONE query
            if subj_inserts:
                keys = [(s_id, s_sem) for s_id, s_sem, _ in subj_inserts]
                if keys:
                    placeholders = ",".join("(?,?)" for _ in keys)
                    flat_keys = [v for pair in keys for v in pair]
                    sr_id_rows = db.execute(
                        f"SELECT student_id, semester_no, id FROM semester_records "
                        f"WHERE (student_id, semester_no) IN ({placeholders})",
                        flat_keys).fetchall()
                    sr_id_map = {(r["student_id"], r["semester_no"]): r["id"] for r in sr_id_rows}
                else:
                    sr_id_map = {}

                # 4. Delete old subject_marks in bulk
                sr_ids_to_delete = list(sr_id_map.values())
                if sr_ids_to_delete:
                    db.execute(
                        f"DELETE FROM subject_marks WHERE semester_record_id IN "
                        f"({','.join('?' for _ in sr_ids_to_delete)})",
                        sr_ids_to_delete)

                # 5. Bulk insert all subject_marks
                all_subj_rows = []
                for s_id, s_sem, srows in subj_inserts:   # use s_sem not semester_no
                    sr_id = sr_id_map.get((s_id, s_sem))
                    if sr_id:
                        for s in srows:
                            all_subj_rows.append((
                                sr_id, s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7]
                            ))
                if all_subj_rows:
                    db.executemany(
                        "INSERT INTO subject_marks"
                        "(semester_record_id,subject_code,subject_name,subject_type,"
                        " grade,grade_point,status,total_marks,max_marks)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        all_subj_rows)

        elif upload_type == "attendance" and att_sr_inserts:
            # 1. Ensure all semester records exist
            db.executemany(
                "INSERT OR IGNORE INTO semester_records"
                "(student_id,semester_no,fetched_at) VALUES(?,?,?)",
                [(r[0], r[1], r[2]) for r in att_sr_inserts])

            # 2. Fetch all sr_ids in one query
            keys = [(r[0], r[1]) for r in att_sr_inserts]
            if keys:
                placeholders = ",".join("(?,?)" for _ in keys)
                flat_keys = [v for pair in keys for v in pair]
                sr_id_rows = db.execute(
                    f"SELECT student_id, semester_no, id FROM semester_records "
                    f"WHERE (student_id, semester_no) IN ({placeholders})",
                    flat_keys).fetchall()
                sr_id_map = {(r["student_id"], r["semester_no"]): r["id"] for r in sr_id_rows}
            else:
                sr_id_map = {}

            # 3. Fetch all existing subject_marks for these sr_ids in one query
            # BUG-1 FIX: key must include subject_type to avoid collision when a student
            # has both Theory [T] and Practical [P] rows for the same subject_code.
            sr_ids = list(sr_id_map.values())
            existing_subj = {}
            if sr_ids:
                existing_rows = db.execute(
                    f"SELECT id, semester_record_id, subject_code, subject_type FROM subject_marks "
                    f"WHERE semester_record_id IN ({','.join('?' for _ in sr_ids)})",
                    sr_ids).fetchall()
                for r in existing_rows:
                    existing_subj[
                        (r["semester_record_id"], r["subject_code"], r["subject_type"])
                    ] = r["id"]

            # 4. Build UPDATE / INSERT batches
            subj_updates_batch = []
            subj_inserts_batch = []
            sr_att_updates     = []

            for a_sid, a_sem, _, avg_att, subj_data in att_sr_inserts:
                sr_id = sr_id_map.get((a_sid, a_sem))
                if not sr_id:
                    continue
                sr_att_updates.append((avg_att, sr_id))
                for sub_code, sub_type, att_pct in subj_data:
                    subj_type_full = "Theory" if sub_type == "T" else "Practical"
                    existing_id = existing_subj.get((sr_id, sub_code, subj_type_full))
                    if existing_id:
                        subj_updates_batch.append((att_pct, existing_id))
                    else:
                        # BUG-F FIX: subject_name was set to raw sub_code (e.g. "CS701").
                        # Format it as "CS701 (Theory)" / "CS701 (Practical)" so the
                        # subject shows a readable label when no results have been uploaded.
                        subj_type_full = "Theory" if sub_type == "T" else "Practical"
                        subj_inserts_batch.append((
                            sr_id, sub_code,
                            f"{sub_code} ({subj_type_full})",
                            subj_type_full,
                            att_pct
                        ))

            # 5. Execute all batches
            if subj_updates_batch:
                db.executemany(
                    "UPDATE subject_marks SET attendance_pct=? WHERE id=?",
                    subj_updates_batch)
            if subj_inserts_batch:
                # BUG-C FIX: Use INSERT OR IGNORE instead of bare INSERT.
                # With the UNIQUE(semester_record_id, subject_code, subject_type)
                # constraint in place, a bare INSERT crashes the whole batch if a
                # duplicate slips through. OR IGNORE skips duplicates gracefully.
                db.executemany(
                    "INSERT OR IGNORE INTO subject_marks"
                    "(semester_record_id,subject_code,subject_name,subject_type,attendance_pct)"
                    " VALUES(?,?,?,?,?)",
                    subj_inserts_batch)
            if sr_att_updates:
                db.executemany(
                    "UPDATE semester_records SET attendance=? WHERE id=?",
                    sr_att_updates)
            # BUG-D FIX: Advance student.current_semester for attendance uploads.
            # student_updates is populated in the attendance parse loop above.
            if student_updates:
                db.executemany(
                    "UPDATE students SET current_semester=MAX(current_semester,?), "
                    "last_synced=? WHERE id=?",
                    student_updates)

        db.commit()   # single commit — all writes or nothing

    except Exception as batch_err:
        import traceback
        traceback.print_exc()   # prints full error to Windows console
        try:
            db.rollback()
        except Exception:
            pass
        failed  = success + failed
        success = 0
        errors  = [f"Upload failed: {batch_err}"]

    # ── Dataset versioning snapshot (after commit) ────────────────────
    if success > 0 and upload_type == "university_results":
        try:
            import json as _json
            prev_ver = db.execute(
                "SELECT MAX(version_no) FROM dataset_versions WHERE college_id=? AND upload_type=?",
                (u["college_id"], "results")).fetchone()[0] or 0
            snap_rows = db.execute(
                "SELECT s.enrollment_no, s.name, s.branch, s.branch_code, "
                "  sr.semester_no, sr.sgpa, sr.cgpa, sr.attendance, "
                "  sr.result, sr.backlog_count, sr.percentage "
                "FROM students s "
                "JOIN semester_records sr ON s.id=sr.student_id "
                "  AND sr.semester_no=s.current_semester "
                "WHERE s.college_id=?",
                (u["college_id"],)).fetchall()
            snap_list = [dict(r) for r in snap_rows]
            total_s   = len(snap_list)
            avg_cgpa  = round(sum(r.get("cgpa") or 0 for r in snap_list) / total_s, 2) if total_s else 0
            avg_att_s = round(sum(r.get("attendance") or 0 for r in snap_list) / total_s, 1) if total_s else 0
            passing   = sum(1 for r in snap_list if str(r.get("result","")).upper() == "PASS")
            at_risk   = sum(1 for r in snap_list if (r.get("cgpa") or 0) < 5.0 or (r.get("attendance") or 0) < 60)
            snapshot  = {
                "students": snap_list,
                "stats": {
                    "total": total_s, "avg_cgpa": avg_cgpa, "avg_att": avg_att_s,
                    "pass_rate": round(passing / total_s * 100, 1) if total_s else 0,
                    "at_risk": at_risk,
                    "upload_label": f"Upload v{prev_ver + 1} — {now[:10]}"
                }
            }
            db.execute(
                "INSERT INTO dataset_versions"
                "(college_id,uploaded_by,upload_type,semester_no,version_no,row_count,snapshot_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (u["college_id"], u["id"], "results", semester_no,
                 prev_ver + 1, success, _json.dumps(snapshot)))
            db.commit()
        except Exception:
            pass

    # ── Finalise log ──────────────────────────────────────────────────
    status = "success" if failed == 0 else ("partial" if success > 0 else "failed")
    db.execute(
        "UPDATE upload_logs SET rows_success=?,rows_failed=?,error_detail=?,status=? WHERE id=?",
        (success, failed, "\n".join(errors) if errors else None, status, log_id))
    db.commit()

    if upload_type == "university_results" and success > 0:
        _push_atrisk_notifications(db, u)

    skip_msg = f" ({skipped} row(s) skipped)" if skipped else ""
    if success > 0:
        flash(f"✅ {success} student(s) uploaded successfully.{skip_msg}"
              + (f" ⚠️ {failed} row(s) failed." if failed else ""), "success")
    else:
        flash(f"❌ Upload failed: {'; '.join(errors[:3])}", "danger")

    return redirect(url_for("upload.upload_page"))

def _push_atrisk_notifications(db, u):
    """
    After a results upload: scan for at-risk students (CGPA<5 or attendance<60)
    and push in-app notifications to all teachers in the college.
    Blueprint Phase 10 — Performance Monitoring.
    """
    try:
        at_risk = dict_rows(db.execute(
            "SELECT s.name, s.enrollment_no, sr.cgpa, sr.attendance "
            "FROM students s "
            "JOIN semester_records sr ON s.id=sr.student_id "
            "  AND sr.semester_no=s.current_semester "
            "WHERE s.college_id=? "
            "  AND (sr.cgpa < 5.0 OR sr.attendance < 60)",
            (u["college_id"],)).fetchall())
        if not at_risk:
            return
        # Notify all teachers in this college
        teachers = dict_rows(db.execute(
            "SELECT id FROM users WHERE role='teacher' AND college_id=? AND is_active=1",
            (u["college_id"],)).fetchall())
        for t in teachers:
            db.execute(
                "INSERT INTO notifications(user_id,title,message,type,link) VALUES(?,?,?,?,?)",
                (t["id"],
                 f"⚠️ {len(at_risk)} At-Risk Student(s) Detected",
                 f"After latest data upload: {len(at_risk)} student(s) have CGPA<5 or attendance<60%. "
                 "Review and log interventions.",
                 "warning",
                 "/teacher/dashboard"))
        db.commit()
    except Exception:
        pass
