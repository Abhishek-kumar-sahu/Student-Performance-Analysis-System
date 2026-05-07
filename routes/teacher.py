"""routes/teacher.py — SPAS v5: Teacher dashboard — all analysis via SPAS AI"""
import io
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort, session
from werkzeug.security import generate_password_hash
from database import get_db, dict_row, dict_rows, generate_strong_password
from routes.auth import login_required, role_required, current_user
from utils.pdf_generator import generate_class_report, generate_student_report
from utils.mailer import send_teacher_created_student
from services.gemini_service import (
    gemini_predict, gemini_analytics_insight, get_student_prediction_data,
    gemini_class_insight, gemini_at_risk_students
)
from security import validate_csrf, sanitize_text, sanitize_name, sanitize_email, sanitize_phone, sanitize_integer, sanitize_alphanumeric, contains_xss, get_client_ip

teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")

from .branches import BRANCH_CHOICES, BRANCH_DICT, PROGRAMME_BRANCHES, PROGRAMME_CHOICES

def _get_students(db, college_id, branch_code=None, semester=None):
    if not college_id:
        return []
    
    q = """
        SELECT s.*,
               sr.sgpa, sr.cgpa, sr.attendance, sr.result,
               sr.backlog_count, sr.percentage, sr.semester_no as current_sr_sem
        FROM students s
        LEFT JOIN semester_records sr ON s.id=sr.student_id 
           AND sr.semester_no = (CASE WHEN ? IS NOT NULL THEN ? ELSE s.current_semester END)
        WHERE s.college_id=?
    """
    params = [semester, semester, college_id]
    if branch_code:
        q += " AND s.branch_code=?"; params.append(branch_code)
    if semester:
        q += " AND sr.semester_no=?"; params.append(semester)
    q += " ORDER BY s.name"
    return dict_rows(db.execute(q, params).fetchall())

def _load_semesters(db, student_id):
    recs = dict_rows(db.execute(
        "SELECT * FROM semester_records WHERE student_id=? ORDER BY semester_no",
        (student_id,)).fetchall())
    for r in recs:
        r["subjects"] = dict_rows(db.execute(
            "SELECT * FROM subject_marks WHERE semester_record_id=? ORDER BY subject_name",
            (r["id"],)).fetchall())
    return recs

def _predict_ml(student, semesters, db=None):
    """AI-powered prediction for a student given their semesters list.
    BUG-27 FIX: Check the ml_predictions cache (24-hour TTL) before calling
    the live AI API, which previously ran on every teacher page load.
    """
    if not semesters:
        return {"label": "Insufficient Data", "confidence": 0, "color": "#94a3b8", "icon": "❓",
                "risk_level": "unknown", "risk_score": 0, "predicted_gpa": 0,
                "recommendations": [], "risk_factors": []}
    latest = semesters[-1]
    sem_no = latest.get("semester_no", 1)
    data = {
        "avg_marks":      latest.get("percentage", 0) or 0,
        "avg_attendance": latest.get("attendance", 0) or 0,
        "previous_gpa":   latest.get("cgpa", 0) or 0,
        "failed_subjects": sum(s.get("backlog_count", 0) for s in semesters),
        "current_semester": sem_no,
        "gpa_trend": ((semesters[-1].get("sgpa", 0) or 0) - (semesters[-2].get("sgpa", 0) or 0)) if len(semesters) >= 2 else 0,
        "programme": student.get("programme", "BE"),
        "branch": student.get("branch", ""),
    }
    import json as _j
    if db:
        _row = db.execute(
            "SELECT * FROM ml_predictions WHERE student_id=? AND semester_no=? "
            "AND created_at > datetime('now','-1 day') ORDER BY created_at DESC LIMIT 1",
            (student["id"], sem_no)
        ).fetchone()
        if _row:
            pred = {
                "risk_level":      _row["risk_level"] or "medium",
                "risk_score":      _row["risk_score"] or 50,
                "predicted_gpa":   _row["predicted_gpa"] or 0,
                "risk_factors":    _j.loads(_row["risk_factors"] or "[]"),
                "recommendations": _j.loads(_row["recommendations"] or "[]"),
                "performance_summary": f"Risk: {(_row['risk_level'] or 'medium').title()}",
            }
            icons  = {"low": "🏆", "medium": "📈", "high": "⚠️", "critical": "🚨"}
            colors = {"low": "#00ff99", "medium": "#ffcc00", "high": "#ff8800", "critical": "#ff4444"}
            pred["icon"]       = icons.get(pred.get("risk_level", "medium"), "📊")
            pred["color"]      = colors.get(pred.get("risk_level", "medium"), "#8b949e")
            pred["label"]      = pred.get("performance_summary", pred.get("risk_level", "").title())
            pred["confidence"] = pred.get("risk_score", 50)
            return pred
    pred = gemini_predict(data)
    if db:
        try:
            from services.gemini_service import save_prediction
            save_prediction(db, student["id"], sem_no, pred)
        except Exception:
            pass
    # Add display helpers
    icons = {"low": "🏆", "medium": "📈", "high": "⚠️", "critical": "🚨"}
    colors = {"low": "#00ff99", "medium": "#ffcc00", "high": "#ff8800", "critical": "#ff4444"}
    pred["icon"]  = icons.get(pred.get("risk_level", "medium"), "📊")
    pred["color"] = colors.get(pred.get("risk_level", "medium"), "#8b949e")
    pred["label"] = pred.get("performance_summary", pred.get("risk_level", "").title())
    pred["confidence"] = pred.get("risk_score", 50)
    return pred

@teacher_bp.route("/dashboard")
@login_required
@role_required("teacher")
def dashboard():
    u  = current_user()
    db = get_db()

    if not u.get("college_id"):
        flash("Your account is not linked to a college. Contact the administrator.", "danger")
        return redirect(url_for("auth.login"))

    branch_filter = sanitize_alphanumeric(request.args.get("branch",""), 10)
    sem_filter    = request.args.get("semester","")

    students = _get_students(db, u["college_id"],
                             branch_filter if branch_filter else None,
                             int(sem_filter) if sem_filter and sem_filter.isdigit() else None)

    total    = len(students)
    passing  = sum(1 for s in students if str(s.get("result","")).upper()=="PASS")
    at_risk  = sum(1 for s in students if (s.get("cgpa",0) or 0)<5.0 or (s.get("attendance",0) or 0)<60)
    avg_att  = round(sum(s.get("attendance",0) or 0 for s in students)/total,1) if total else 0
    avg_cgpa = round(sum(s.get("cgpa",0) or 0 for s in students)/total,2) if total else 0

    grade_dist = {}
    for s in students:
        cgpa = s.get("cgpa",0) or 0
        if cgpa>=9: g="O (≥9)"
        elif cgpa>=8: g="A+ (8-9)"
        elif cgpa>=7: g="A (7-8)"
        elif cgpa>=6: g="B+ (6-7)"
        elif cgpa>=5: g="B (5-6)"
        else: g="Below 5"
        grade_dist[g] = grade_dist.get(g,0)+1

    branches = dict_rows(db.execute(
        "SELECT DISTINCT branch_code,branch FROM students WHERE college_id=? ORDER BY branch",
        (u["college_id"],)).fetchall())
    semesters_list = list(range(1, 9))

    # Only show pending regs for teacher's own branch
    teacher_branch = u.get("department", "")
    if teacher_branch:
        pending_regs = dict_rows(db.execute(
            "SELECT * FROM student_registrations WHERE college_id=? AND status='pending' "
            "AND branch_code=? ORDER BY submitted_at DESC",
            (u["college_id"], teacher_branch)).fetchall())
    else:
        pending_regs = dict_rows(db.execute(
            "SELECT * FROM student_registrations WHERE college_id=? AND status='pending' ORDER BY submitted_at DESC",
            (u["college_id"],)).fetchall())

    col = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())

    class_insight = (gemini_class_insight(students,
                        college_name=col["name"] if col else "",
                        branch=branch_filter or (u.get("department") or ""))
                    if students else {})
    at_risk_list = gemini_at_risk_students(students) if students else []

    return render_template("teacher/dashboard.html",
        u=u, col=col, students=students, total=total,
        passing=passing, failing=total-passing, at_risk=at_risk,
        avg_att=avg_att, avg_cgpa=avg_cgpa, grade_dist=grade_dist,
        branches=branches, semesters=semesters_list,
        branch_filter=branch_filter, sem_filter=sem_filter,
        pending_regs=pending_regs, branches_choices=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES,
        class_insight=class_insight, at_risk_list=at_risk_list, user=u)


@teacher_bp.route("/student-registrations")
@login_required
@role_required("teacher")
def student_registrations():
    """View all student registration requests for this teacher's branch/college."""
    db = get_db()
    u  = current_user()
    if not u.get("college_id"):
        flash("Your account is not linked to a college.", "danger")
        return redirect(url_for("auth.login"))

    teacher_branch = u.get("department", "")
    # If teacher has a department, filter by it; otherwise show all college regs
    if teacher_branch:
        regs = dict_rows(db.execute(
            "SELECT * FROM student_registrations WHERE college_id=? AND branch_code=? "
            "ORDER BY submitted_at DESC",
            (u["college_id"], teacher_branch)).fetchall())
    else:
        regs = dict_rows(db.execute(
            "SELECT * FROM student_registrations WHERE college_id=? "
            "ORDER BY submitted_at DESC",
            (u["college_id"],)).fetchall())

    return render_template("teacher/student_registrations.html", regs=regs, user=u)

@teacher_bp.route("/student/<int:sid>")
@login_required
@role_required("teacher")
def student_detail(sid):
    u  = current_user()
    db = get_db()
    if not u.get("college_id"):
        flash("Your account is not linked to a college.", "danger")
        return redirect(url_for("auth.login"))
    student = dict_row(db.execute(
        "SELECT * FROM students WHERE id=? AND college_id=?", (sid, u["college_id"])).fetchone())
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("teacher.dashboard"))
    semesters  = _load_semesters(db, sid)
    ml_pred    = _predict_ml(student, semesters, db=db)
    sgpa_trend = [{"sem":s["semester_no"],"sgpa":s.get("sgpa",0)or 0,"cgpa":s.get("cgpa",0)or 0} for s in semesters]
    return render_template("teacher/student_detail.html",
        student=student, semesters=semesters, ml_pred=ml_pred,
        sgpa_trend=sgpa_trend, user=u)

# ── Teacher registers student directly ────────────────────────────────
@teacher_bp.route("/register-student", methods=["GET","POST"])
@login_required
@role_required("teacher")
def register_student():
    u  = current_user()
    db = get_db()

    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        f             = request.form
        enrollment_no = sanitize_alphanumeric(f.get("enrollment_no",""), 30)
        full_name     = sanitize_name(f.get("full_name",""), 100)
        email         = sanitize_email(f.get("email",""))
        email         = email if email else None
        phone         = sanitize_phone(f.get("phone",""))
        branch_code   = sanitize_alphanumeric(f.get("branch_code","") or u.get("department",""), 10)
        semester      = sanitize_integer(f.get("semester","1"), default=1, min_val=1, max_val=8)
        gender        = sanitize_text(f.get("gender",""), 10)
        programme     = sanitize_text(f.get("programme","BE"), 10) or "BE"

        errs = []
        if not enrollment_no: errs.append("Enrollment number is required.")
        if not full_name:     errs.append("Full name is required.")
        if not branch_code:   errs.append("Branch is required.")
        if enrollment_no and (
            db.execute("SELECT id FROM students WHERE enrollment_no=?", (enrollment_no,)).fetchone() or
            db.execute("SELECT id FROM student_registrations WHERE enrollment_no=?", (enrollment_no,)).fetchone()
        ):
            errs.append("This enrollment number is already registered or pending.")
            
        if email and (
            db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone() or
            db.execute("SELECT id FROM student_registrations WHERE email=?", (email,)).fetchone()
        ):
            errs.append("This email is already registered or pending.")

        if errs:
            for e in errs: flash(e, "danger")
            return render_template("teacher/register_student.html",
                u=u, branches=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES, form=f, user=u)

        branch_map  = dict(BRANCH_CHOICES)
        branch_name = branch_map.get(branch_code, branch_code)

        # BUG FIX: safely fetch college — u["college_id"] may be int or None
        college_id   = u.get("college_id")
        college_code = ""
        if college_id:
            col = dict_row(db.execute("SELECT college_code FROM colleges WHERE id=?", (college_id,)).fetchone())
            if col:
                college_code = col["college_code"]

        from datetime import datetime as dt
        now = dt.utcnow().strftime('%Y-%m-%d %H:%M:%S')

        # Insert registration record (auto-approved since teacher)
        db.execute("""INSERT INTO student_registrations
            (enrollment_no,name,email,phone,branch,branch_code,programme,semester,
             college_id,college_code,gender,registered_by,teacher_id,status,reviewed_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,'teacher',?,?,datetime('now'))""",
            (enrollment_no,full_name,email,phone,branch_name,branch_code,programme,
             semester,college_id,college_code,gender,u["id"],"approved"))

        # Create student record
        db.execute("""INSERT OR IGNORE INTO students
            (enrollment_no,name,branch,branch_code,programme,current_semester,
             college_id,college_code,gender,phone,last_synced)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (enrollment_no,full_name,branch_name,branch_code,programme,semester,
             college_id,college_code,gender,phone,now))
        stu = dict_row(db.execute(
            "SELECT id FROM students WHERE enrollment_no=?", (enrollment_no,)).fetchone())

        # Create login account
        pwd      = generate_strong_password(10)
        username = enrollment_no.lower()
        base = username; idx = 1
        while db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            username = f"{base}{idx}"; idx += 1

        db.execute(
            "INSERT OR IGNORE INTO users(username,email,password,role,full_name,college_id,student_id,must_change_password) VALUES(?,?,?,?,?,?,?,1)",
            (username, email, generate_password_hash(pwd), "student", full_name, college_id, stu["id"]))
        db.commit()

        if email:
            login_url = url_for("auth.login", _external=True)
            send_teacher_created_student(email, full_name, enrollment_no,
                                         u.get("full_name") or u["username"],
                                         username, pwd, login_url)

        flash(f"✅ Student '{full_name}' registered. Login credentials have been sent to their email.", "success")
        return redirect(url_for("teacher.dashboard"))

    return render_template("teacher/register_student.html",
        u=u, branches=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES, form={}, user=u)

# ── Approve/reject student registrations ─────────────────────────────
@teacher_bp.route("/student-reg/<int:rid>/approve", methods=["POST"])
@login_required
@role_required("teacher")
def approve_student_reg(rid):
    if not validate_csrf():
        abort(403)
    u  = current_user()
    db = get_db()
    teacher_branch = u.get("department", "")
    if teacher_branch:
        reg = dict_row(db.execute(
            "SELECT * FROM student_registrations WHERE id=? AND college_id=? AND status='pending' AND branch_code=?",
            (rid, u["college_id"], teacher_branch)).fetchone())
    else:
        reg = dict_row(db.execute(
            "SELECT * FROM student_registrations WHERE id=? AND college_id=? AND status='pending'",
            (rid, u["college_id"])).fetchone())
    if not reg:
        flash("Registration not found, already processed, or not in your branch.", "danger")
        return redirect(url_for("teacher.dashboard"))

    from datetime import datetime as dt
    now = dt.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    db.execute("""INSERT OR IGNORE INTO students
        (enrollment_no,name,branch,branch_code,programme,current_semester,
         college_id,college_code,gender,phone,last_synced)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (reg["enrollment_no"],reg["name"],reg["branch"],reg["branch_code"],
         reg["programme"],reg["semester"],reg["college_id"],reg["college_code"],
         reg.get("gender",""),reg.get("phone",""),now))
    stu = dict_row(db.execute(
        "SELECT id FROM students WHERE enrollment_no=?", (reg["enrollment_no"],)).fetchone())

    pwd = generate_strong_password(10)
    username = reg["enrollment_no"].lower()
    base = username; idx = 1
    while db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        username = f"{base}{idx}"; idx += 1

    # Check for duplicate email before account creation
    email = reg.get("email")
    email = email if email else None
    if email and db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        flash(f"Error: Email {email} is already registered to another user account.", "danger")
        return redirect(url_for("teacher.dashboard"))

    db.execute(
        "INSERT OR IGNORE INTO users(username,email,password,role,full_name,college_id,student_id,must_change_password) VALUES(?,?,?,?,?,?,?,1)",
        (username, email, generate_password_hash(pwd), "student",
         reg["name"], u["college_id"], stu["id"]))
    db.execute("UPDATE student_registrations SET status='approved',reviewed_at=? WHERE id=?", (now, rid))
    db.commit()

    login_url = url_for("auth.login", _external=True)
    from utils.mailer import send_student_approved
    send_student_approved(reg["email"], reg["name"], username, pwd, login_url)
    flash(f"✅ Student '{reg['name']}' approved. Login credentials have been sent to their email.", "success")
    return redirect(url_for("teacher.dashboard"))

@teacher_bp.route("/student-reg/<int:rid>/reject", methods=["POST"])
@login_required
@role_required("teacher")
def reject_student_reg(rid):
    if not validate_csrf():
        abort(403)
    u  = current_user()
    db = get_db()
    reason = request.form.get("reason","").strip()
    teacher_branch = u.get("department", "")
    if teacher_branch:
        reg = dict_row(db.execute(
            "SELECT * FROM student_registrations WHERE id=? AND college_id=? AND branch_code=?",
            (rid, u["college_id"], teacher_branch)).fetchone())
    else:
        reg = dict_row(db.execute(
            "SELECT * FROM student_registrations WHERE id=? AND college_id=?",
            (rid, u["college_id"])).fetchone())
    if not reg:
        flash("Registration not found or not in your branch.", "danger")
        return redirect(url_for("teacher.dashboard"))
    from datetime import datetime as dt
    db.execute("UPDATE student_registrations SET status='rejected',reject_reason=?,reviewed_at=? WHERE id=?",
               (reason, dt.utcnow().strftime('%Y-%m-%d %H:%M:%S'), rid))
    db.commit()
    from utils.mailer import send_student_rejected
    send_student_rejected(reg["email"], reg["name"], reason)
    flash(f"Registration for '{reg['name']}' rejected.", "warning")
    return redirect(url_for("teacher.dashboard"))

@teacher_bp.route("/reports")
@login_required
@role_required("teacher")
def reports():
    u  = current_user()
    db = get_db()
    if not u.get("college_id"):
        flash("Account not linked to a college.", "danger")
        return redirect(url_for("teacher.dashboard"))
    
    branches = dict_rows(db.execute(
        "SELECT DISTINCT branch_code,branch FROM students WHERE college_id=? ORDER BY branch",
        (u["college_id"],)).fetchall())
    return render_template("teacher/reports.html", branches=branches, u=u)

@teacher_bp.route("/report/class.pdf")
@login_required
@role_required("teacher")
def class_pdf():
    u  = current_user()
    db = get_db()
    if not u.get("college_id"):
        flash("Account not linked to a college.", "danger")
        return redirect(url_for("teacher.dashboard"))
    
    branch_code = request.args.get("branch","")
    semester    = request.args.get("semester","")
    
    # Sanitize
    branch_code = sanitize_alphanumeric(branch_code, 10) if branch_code else None
    sem_val     = int(semester) if semester and semester.isdigit() else None

    # Fetch students
    students_raw = _get_students(db, u["college_id"], branch_code, sem_val)
    
    if not students_raw:
        flash("No students found matching the selected filters.", "warning")
        return redirect(url_for("teacher.reports"))

    # Group students by Branch and Semester for the PDF
    groups = []
    if branch_code and sem_val:
        # Single group
        groups.append({
            "branch": students_raw[0]["branch"],
            "semester": sem_val,
            "students": students_raw
        })
    else:
        # Multiple groups - sort by branch then semester
        # Note: _get_students already sorts by name. We need to regroup.
        from itertools import groupby
        from operator import itemgetter
        
        # To group properly, we must sort by group keys first
        sorted_students = sorted(students_raw, key=lambda x: (x.get("branch",""), x.get("current_semester",1)))
        
        for (b_name, s_no), group_iter in groupby(sorted_students, key=lambda x: (x.get("branch",""), x.get("current_semester",1))):
            groups.append({
                "branch": b_name,
                "semester": s_no,
                "students": list(group_iter)
            })

    col = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())
    pdf = generate_class_report(col["name"] if col else "SPAS College", groups)
    
    fname = f"class_report_{branch_code or 'all'}_sem{semester or 'all'}.pdf"
    return send_file(io.BytesIO(pdf), as_attachment=True,
                     download_name=fname,
                     mimetype="application/pdf")

@teacher_bp.route("/report/student/<int:sid>.pdf")
@login_required
@role_required("teacher")
def student_pdf(sid):
    u  = current_user()
    db = get_db()
    if not u.get("college_id"):
        flash("Account not linked to a college.", "danger")
        return redirect(url_for("teacher.dashboard"))
    student = dict_row(db.execute(
        "SELECT * FROM students WHERE id=? AND college_id=?", (sid, u["college_id"])).fetchone())
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("teacher.dashboard"))
    semesters = _load_semesters(db, sid)
    pdf = generate_student_report(student, semesters)
    return send_file(io.BytesIO(pdf), as_attachment=True,
                     download_name=f"report_{student['enrollment_no']}.pdf",
                     mimetype="application/pdf")

@teacher_bp.route("/student/<int:sid>/reset-password", methods=["POST"])
@login_required
@role_required("teacher")
def reset_student_password(sid):
    if not validate_csrf():
        abort(403)
    u  = current_user()
    db = get_db()
    student = dict_row(db.execute(
        "SELECT s.*,us.id as uid FROM students s "
        "LEFT JOIN users us ON us.student_id=s.id "
        "WHERE s.id=? AND s.college_id=?", (sid, u["college_id"])).fetchone())
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("teacher.dashboard"))
    new_pwd = request.form.get("new_password","").strip()
    if len(new_pwd) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("teacher.student_detail", sid=sid))
    if student.get("uid"):
        db.execute("UPDATE users SET password=? WHERE id=?",
                   (generate_password_hash(new_pwd), student["uid"]))
    else:
        enr = student["enrollment_no"]
        db.execute("INSERT OR IGNORE INTO users(username,password,role,college_id,student_id) VALUES(?,?,?,?,?)",
                   (enr.lower(), generate_password_hash(new_pwd), "student", u["college_id"], sid))
    db.commit()
    flash(f"Password reset for {student['name']}.", "success")
    return redirect(url_for("teacher.student_detail", sid=sid))

@teacher_bp.route("/student/<int:sid>/delete", methods=["POST"])
@login_required
@role_required("teacher")
def delete_student(sid):
    if not validate_csrf():
        abort(403)
    u  = current_user()
    db = get_db()
    student = dict_row(db.execute(
        "SELECT * FROM students WHERE id=? AND college_id=?", (sid, u["college_id"])).fetchone())
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("teacher.dashboard"))
    # Delete associated records
    db.execute("DELETE FROM student_registrations WHERE enrollment_no=?", (student["enrollment_no"],))
    db.execute("DELETE FROM users WHERE student_id=?", (sid,))
    db.execute("DELETE FROM students WHERE id=?", (sid,))
    db.commit()
    flash(f"Student '{student['name']}' deleted.", "warning")
    return redirect(url_for("teacher.dashboard"))

@teacher_bp.route("/students/bulk-delete", methods=["POST"])
@login_required
@role_required("teacher")
def bulk_delete_students():
    if not validate_csrf():
        abort(403)
    u  = current_user()
    db = get_db()
    ids_raw = request.form.get("student_ids","")
    ids     = [i.strip() for i in ids_raw.split(",") if i.strip().isdigit()]
    deleted = 0
    for sid in ids:
        row = db.execute("SELECT id FROM students WHERE id=? AND college_id=?",
                         (sid, u["college_id"])).fetchone()
        if row:
            # We need the enrollment_no to delete from student_registrations
            stu = dict_row(db.execute("SELECT enrollment_no FROM students WHERE id=?", (sid,)).fetchone())
            if stu:
                db.execute("DELETE FROM student_registrations WHERE enrollment_no=?", (stu["enrollment_no"],))
            db.execute("DELETE FROM users WHERE student_id=?", (sid,))
            db.execute("DELETE FROM students WHERE id=?", (sid,))
            deleted += 1
    db.commit()
    flash(f"Deleted {deleted} student(s).", "warning")
    return redirect(url_for("teacher.dashboard"))


# ── Change Password ───────────────────────────────────────────────────
@teacher_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@role_required("teacher")
def change_password():
    from werkzeug.security import check_password_hash
    db   = get_db()
    user = current_user()
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        current_pwd = request.form.get("current_password", "")
        new_pwd     = request.form.get("new_password", "").strip()
        confirm_pwd = request.form.get("confirm_password", "").strip()
        errs = []
        if not check_password_hash(user["password"], current_pwd):
            errs.append("Current password is incorrect.")
        if len(new_pwd) < 8:
            errs.append("New password must be at least 8 characters.")
        if not any(c.isupper() for c in new_pwd):
            errs.append("Must contain at least one uppercase letter.")
        if not any(c.islower() for c in new_pwd):
            errs.append("Must contain at least one lowercase letter.")
        if not any(c.isdigit() for c in new_pwd):
            errs.append("Must contain at least one digit.")
        if new_pwd != confirm_pwd:
            errs.append("Passwords do not match.")
        if errs:
            for e in errs: flash(e, "danger")
        else:
            db.execute("UPDATE users SET password=?, must_change_password=0 WHERE id=?",
                       (generate_password_hash(new_pwd), user["id"]))
            db.commit()
            flash("✅ Password changed successfully.", "success")
            return redirect(url_for("teacher.dashboard"))
    return render_template("teacher/change_password.html", user=user)


# ── Teacher profile edit ──────────────────────────────────────────────
ALLOWED_PHOTO_EXT  = {"jpg","jpeg","png","gif","webp"}
MAX_PHOTO_BYTES    = 512 * 1024

def _allowed_photo_t(fn):
    return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_PHOTO_EXT

@teacher_bp.route("/profile", methods=["GET","POST"])
@login_required
@role_required("teacher")
def edit_profile():
    import base64
    db   = get_db()
    user = current_user()
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        phone     = sanitize_phone(request.form.get("phone") or "")
        bio       = sanitize_text(request.form.get("bio") or "", max_len=500)
        email     = sanitize_email(request.form.get("email") or "")
        full_name = sanitize_name(request.form.get("full_name") or "", 100)
        file      = request.files.get("profile_photo")
        photo_data = None
        if file and file.filename:
            if not _allowed_photo_t(file.filename):
                flash("Only JPG, PNG, GIF, WEBP allowed.", "danger")
                return render_template("teacher/edit_profile.html", user=user)
            raw = file.read()
            if len(raw) > MAX_PHOTO_BYTES:
                flash("Photo must be under 512 KB.", "danger")
                return render_template("teacher/edit_profile.html", user=user)
            ext  = file.filename.rsplit(".",1)[1].lower()
            mime = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png",
                    "gif":"image/gif","webp":"image/webp"}.get(ext,"image/jpeg")
            photo_data = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
        updates = "full_name=?, email=?, phone=?, bio=?"
        params  = [full_name, email, phone, bio]
        if photo_data:
            updates += ", profile_photo=?"
            params.append(photo_data)
        params.append(user["id"])
        db.execute(f"UPDATE users SET {updates} WHERE id=?", params)
        db.commit()
        flash("✅ Profile updated.", "success")
        return redirect(url_for("teacher.edit_profile"))
    return render_template("teacher/edit_profile.html", user=user)

