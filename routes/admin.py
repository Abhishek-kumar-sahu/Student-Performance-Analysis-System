"""routes/admin.py — Admin: Teacher CRUD, student registration review, analytics"""
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from werkzeug.security import generate_password_hash
from datetime import datetime
from database import (get_db, dict_row, dict_rows, persist_fetch,
                      generate_strong_password, DB_PATH)
from routes.auth import login_required, role_required, current_user
from security import validate_csrf, sanitize_text, sanitize_name, sanitize_email, sanitize_phone, sanitize_integer, sanitize_alphanumeric, contains_xss, get_client_ip

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

from .branches import BRANCH_CHOICES, BRANCH_DICT, PROGRAMME_BRANCHES, PROGRAMME_CHOICES




# ── Dashboard ─────────────────────────────────────────────────────────
@admin_bp.route("/dashboard")
@login_required
@role_required("admin")
def dashboard():
    db  = get_db()
    u   = current_user()
    if not u.get("college_id"):
        flash("Your account is not linked to a college. Contact the super admin.", "danger")
        return redirect(url_for("auth.login"))

    # Filters
    sel_branch = request.args.get("branch", "")
    sel_sem    = request.args.get("semester", "")

    col = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())
    
    sem_val = int(sel_sem) if sel_sem.isdigit() else None
    
    q = """
        SELECT s.*, sr.sgpa, sr.cgpa, sr.attendance, sr.result, sr.backlog_count, sr.percentage
        FROM students s LEFT JOIN semester_records sr 
        ON s.id=sr.student_id AND sr.semester_no = (CASE WHEN ? IS NOT NULL THEN ? ELSE s.current_semester END)
        WHERE s.college_id=?
    """
    params = [sem_val, sem_val, u["college_id"]]

    if sel_branch:
        q += " AND s.branch_code=?"; params.append(sel_branch)
    if sel_sem:
        q += " AND sr.semester_no=?"; params.append(sem_val)

    q += " ORDER BY s.name"
    students = dict_rows(db.execute(q, params).fetchall())

    teachers   = dict_rows(db.execute(
        "SELECT * FROM users WHERE role='teacher' AND college_id=?",
        (u["college_id"],)).fetchall())
    
    # Stats based on the filtered list (or should it be total? I'll stick to filtered for consistency)
    total    = len(students)
    passing  = sum(1 for s in students if str(s.get("result","")).upper() == "PASS")
    avg_cgpa = round(sum(s.get("cgpa",0) or 0 for s in students) / total, 2) if total else 0
    avg_att  = round(sum(s.get("attendance",0) or 0 for s in students) / total, 1) if total else 0
    at_risk  = sum(1 for s in students
                   if (s.get("cgpa",0) or 0) < 5.0 or (s.get("attendance",0) or 0) < 60)
    
    from collections import Counter
    branch_dist = dict(Counter(s["branch"] for s in students))

    return render_template("admin/dashboard.html",
        u=u, col=col, students=students, teachers=teachers,
        total=total, passing=passing,
        failing=total-passing, avg_cgpa=avg_cgpa, avg_att=avg_att, at_risk=at_risk,
        branch_dist=branch_dist, branches=BRANCH_CHOICES, 
        programme_branches=PROGRAMME_BRANCHES, user=u,
        sel_branch=sel_branch, sel_sem=sel_sem)


# ── Teachers CRUD ─────────────────────────────────────────────────────
@admin_bp.route("/teachers")
@login_required
@role_required("admin")
def teachers():
    db = get_db()
    u  = current_user()
    if not u.get("college_id"):
        flash("Your account is not linked to a college.", "danger")
        return redirect(url_for("auth.login"))
    ts = dict_rows(db.execute(
        "SELECT u.*,c.name as college_name FROM users u "
        "LEFT JOIN colleges c ON u.college_id=c.id "
        "WHERE u.role='teacher' AND u.college_id=? ORDER BY u.created_at DESC",
        (u["college_id"],)).fetchall())
    return render_template("admin/teachers.html", teachers=ts, user=u, branches=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES)


@admin_bp.route("/teachers/create", methods=["GET", "POST"])
@login_required
@role_required("admin")
def create_teacher():
    u  = current_user()
    db = get_db()
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        f         = request.form
        uname     = sanitize_alphanumeric(f.get("username", ""), 50).lower()
        pwd       = f.get("password", "").strip()
        full_name = sanitize_name(f.get("full_name", ""), 100)
        dept      = sanitize_alphanumeric(f.get("department", ""), 10)
        email     = sanitize_email(f.get("email", ""))
        email     = email if email else None
        phone     = sanitize_phone(f.get("phone", ""))
        errs = []
        if not uname:  errs.append("Username is required.")
        if not pwd:    errs.append("Password is required.")
        if len(pwd) < 8: errs.append("Password must be at least 8 characters.")
        if contains_xss(full_name): errs.append("Invalid characters in name.")
        if db.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone():
            errs.append("Username already taken.")
        if email and db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            errs.append("Email address already registered.")
        if errs:
            for e in errs: flash(e, "danger")
        else:
            db.execute(
                "INSERT INTO users(username,password,role,full_name,department,email,phone,college_id,must_change_password) "
                "VALUES(?,?,?,?,?,?,?,?,1)",
                (uname, generate_password_hash(pwd), "teacher",
                 full_name, dept, email, phone, u["college_id"]))
            db.commit()
            
            if email:
                login_url = url_for("auth.login", _external=True)
                from utils.mailer import send_teacher_account_created
                send_teacher_account_created(email, full_name or uname, uname, pwd, login_url)
                flash(f"Teacher '{uname}' created. Credentials sent to {email}.", "success")
            else:
                flash(f"Teacher '{uname}' created.", "success")
                
            return redirect(url_for("admin.teachers"))
    return render_template("admin/teacher_form.html", teacher=None, user=u, branches=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES)


@admin_bp.route("/teachers/edit/<int:tid>", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_teacher(tid):
    db      = get_db()
    u       = current_user()
    teacher = dict_row(db.execute(
        "SELECT * FROM users WHERE id=? AND role='teacher' AND college_id=?",
        (tid, u["college_id"])).fetchone())
    if not teacher:
        flash("Teacher not found.", "danger")
        return redirect(url_for("admin.teachers"))
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        f         = request.form
        uname     = sanitize_alphanumeric(f.get("username", ""), 50).lower()
        pwd       = f.get("password", "").strip()
        full_name = sanitize_name(f.get("full_name", ""), 100)
        dept      = sanitize_alphanumeric(f.get("department", ""), 10)
        email     = sanitize_email(f.get("email", ""))
        email     = email if email else None
        phone     = sanitize_phone(f.get("phone", ""))
        if contains_xss(full_name):
            flash("Invalid characters in name.", "danger")
        elif db.execute("SELECT id FROM users WHERE username=? AND id!=?",
                      (uname, tid)).fetchone():
            flash("Username already taken.", "danger")
        elif email and db.execute("SELECT id FROM users WHERE email=? AND id!=?",
                                (email, tid)).fetchone():
            flash("Email address already registered to another user.", "danger")
        else:
            db.execute("UPDATE users SET username=?,full_name=?,department=?,email=?,phone=? WHERE id=?",
                       (uname, full_name, dept, email, phone, tid))
            if pwd:
                if len(pwd) < 8:
                    flash("Password too short (min 8 chars).", "danger")
                    return render_template("admin/teacher_form.html", teacher=teacher,
                                           user=u, branches=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES)
                db.execute("UPDATE users SET password=? WHERE id=?",
                           (generate_password_hash(pwd), tid))
            db.commit()
            flash("Teacher updated.", "success")
            return redirect(url_for("admin.teachers"))
    return render_template("admin/teacher_form.html", teacher=teacher, user=u, branches=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES)


@admin_bp.route("/teachers/delete/<int:tid>", methods=["POST"])
@login_required
@role_required("admin")
def delete_teacher(tid):
    if not validate_csrf():
        abort(403)
    db = get_db()
    u  = current_user()
    db.execute("DELETE FROM users WHERE id=? AND role='teacher' AND college_id=?",
               (tid, u["college_id"]))
    db.commit()
    flash("Teacher deleted.", "warning")
    return redirect(url_for("admin.teachers"))


@admin_bp.route("/teachers/reset-password/<int:tid>", methods=["POST"])
@login_required
@role_required("admin")
def reset_teacher_password(tid):
    if not validate_csrf():
        abort(403)
    db      = get_db()
    u       = current_user()
    new_pwd = request.form.get("new_password", "").strip()
    if len(new_pwd) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("admin.teachers"))
    teacher = dict_row(db.execute(
        "SELECT * FROM users WHERE id=? AND role='teacher' AND college_id=?",
        (tid, u["college_id"])).fetchone())
    if not teacher:
        flash("Teacher not found.", "danger")
        return redirect(url_for("admin.teachers"))
    db.execute("UPDATE users SET password=? WHERE id=?",
               (generate_password_hash(new_pwd), tid))
    db.commit()
    flash(f"Password reset for '{teacher['username']}'.", "success")
    return redirect(url_for("admin.teachers"))


# ── Student Registrations ─────────────────────────────────────────────
@admin_bp.route("/student-registrations")
@login_required
@role_required("admin")
def student_registrations():
    db   = get_db()
    u    = current_user()
    if not u.get("college_id"):
        flash("Your account is not linked to a college.", "danger")
        return redirect(url_for("auth.login"))
    regs = dict_rows(db.execute(
        "SELECT sr.*,u.username as teacher_name FROM student_registrations sr "
        "LEFT JOIN users u ON sr.teacher_id=u.id "
        "WHERE sr.college_id=? ORDER BY sr.submitted_at DESC",
        (u["college_id"],)).fetchall())
    return render_template("admin/student_registrations.html", regs=regs, user=u)


@admin_bp.route("/student-registrations/<int:rid>/approve", methods=["POST"])
@login_required
@role_required("admin")
def approve_student_reg(rid):
    if not validate_csrf():
        abort(403)
    db  = get_db()
    u   = current_user()
    reg = dict_row(db.execute(
        "SELECT * FROM student_registrations WHERE id=? AND college_id=? AND status='pending'",
        (rid, u["college_id"])).fetchone())
    if not reg:
        flash("Registration not found.", "danger")
        return redirect(url_for("admin.student_registrations"))

    now = datetime.utcnow().isoformat()
    db.execute("""INSERT OR IGNORE INTO students
        (enrollment_no,name,branch,branch_code,programme,current_semester,
         college_id,college_code,gender,phone,last_synced)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (reg["enrollment_no"], reg["full_name"], reg["branch"], reg["branch_code"],
         reg["programme"], reg["semester"], reg["college_id"], reg["college_code"],
         reg.get("gender",""), reg.get("phone",""), now))
    stu = dict_row(db.execute("SELECT id FROM students WHERE enrollment_no=?",
                              (reg["enrollment_no"],)).fetchone())

    raw_pwd  = generate_strong_password(10)
    username = reg["enrollment_no"].lower()
    base = username; idx = 1
    while db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        username = f"{base}{idx}"; idx += 1

    db.execute("INSERT OR IGNORE INTO users(username,email,password,role,full_name,college_id,student_id,must_change_password) "
               "VALUES(?,?,?,?,?,?,?,1)",
               (username, reg["email"], generate_password_hash(raw_pwd),
                "student", reg["full_name"], u["college_id"], stu["id"]))
    db.execute("UPDATE student_registrations SET status='approved',reviewed_at=? WHERE id=?",
               (now, rid))
    db.commit()

    from utils.mailer import send_student_approved
    send_student_approved(reg["email"], reg["full_name"], username, raw_pwd,
                          url_for("auth.login", _external=True))
    flash(f"Student '{reg['full_name']}' approved. Login credentials have been sent to their email.", "success")
    return redirect(url_for("admin.student_registrations"))


@admin_bp.route("/student-registrations/<int:rid>/reject", methods=["POST"])
@login_required
@role_required("admin")
def reject_student_reg(rid):
    if not validate_csrf():
        abort(403)
    db     = get_db()
    u      = current_user()
    reason = request.form.get("reason", "").strip()
    reg    = dict_row(db.execute(
        "SELECT * FROM student_registrations WHERE id=? AND college_id=?",
        (rid, u["college_id"])).fetchone())
    if not reg:
        flash("Registration not found.", "danger")
        return redirect(url_for("admin.student_registrations"))
    db.execute("UPDATE student_registrations SET status='rejected',reject_reason=?,reviewed_at=? WHERE id=?",
               (reason, datetime.utcnow().isoformat(), rid))
    db.commit()
    from utils.mailer import send_student_rejected
    send_student_rejected(reg["email"], reg["full_name"], reason)
    flash(f"Registration for '{reg['full_name']}' rejected.", "warning")
    return redirect(url_for("admin.student_registrations"))


# [Audit Log moved to Super Admin]

# ── Change Password ───────────────────────────────────────────────────
@admin_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@role_required("admin")
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
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/change_password.html", user=user)


# ── Edit Profile (Admin) ──────────────────────────────────────────────
ALLOWED_PHOTO_EXT = {"jpg", "jpeg", "png", "gif", "webp"}
MAX_PHOTO_BYTES   = 512 * 1024   # 512 KB for admin/teacher (larger than student)

def _allowed_photo(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXT

@admin_bp.route("/profile", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_profile():
    import base64
    db   = get_db()
    user = current_user()
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        phone      = sanitize_phone(request.form.get("phone") or "")
        bio        = sanitize_text(request.form.get("bio") or "", max_len=500)
        email      = sanitize_email(request.form.get("email") or "")
        full_name  = sanitize_name(request.form.get("full_name") or "", 100)
        file       = request.files.get("profile_photo")
        photo_data = None
        if file and file.filename:
            if not _allowed_photo(file.filename):
                flash("Only JPG, PNG, GIF, WEBP allowed.", "danger")
                return render_template("admin/edit_profile.html", user=user)
            raw = file.read()
            if len(raw) > MAX_PHOTO_BYTES:
                flash(f"Photo must be under 512 KB.", "danger")
                return render_template("admin/edit_profile.html", user=user)
            ext  = file.filename.rsplit(".", 1)[1].lower()
            mime = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png",
                    "gif":"image/gif","webp":"image/webp"}.get(ext, "image/jpeg")
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
        return redirect(url_for("admin.edit_profile"))
    return render_template("admin/edit_profile.html", user=user)


@admin_bp.route("/view-student/<int:sid>")
@login_required
@role_required("admin")
def view_student(sid):
    db = get_db()
    u  = current_user()
    if not u.get("college_id"):
        flash("Account not linked to a college.", "danger")
        return redirect(url_for("auth.login"))
    student = dict_row(db.execute(
        "SELECT * FROM students WHERE id=? AND college_id=?",
        (sid, u["college_id"])).fetchone())
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("admin.dashboard"))
    from database import dict_rows
    semesters = dict_rows(db.execute(
        "SELECT * FROM semester_records WHERE student_id=? ORDER BY semester_no",
        (sid,)).fetchall())
    for s in semesters:
        s["subjects"] = dict_rows(db.execute(
            "SELECT * FROM subject_marks WHERE semester_record_id=? ORDER BY subject_name",
            (s["id"],)).fetchall())
    return render_template("admin/view_student.html", student=student,
                           semesters=semesters, user=u)
