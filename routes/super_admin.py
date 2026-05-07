"""routes/super_admin.py — Super Admin: dashboard, colleges, admins, registrations"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from werkzeug.security import generate_password_hash
from datetime import datetime
from database import get_db, dict_row, dict_rows, generate_strong_password, make_admin_username
from routes.auth import login_required, role_required, current_user
from utils.mailer import send_registration_approved, send_registration_rejected
from security import validate_csrf, sanitize_text, sanitize_name, sanitize_email, sanitize_phone, sanitize_integer, sanitize_alphanumeric, contains_xss, get_client_ip

sa_bp = Blueprint("super_admin", __name__, url_prefix="/superadmin")


@sa_bp.route("/dashboard")
@login_required
@role_required("super_admin")
def dashboard():
    db = get_db()
    colleges = dict_rows(db.execute("SELECT * FROM colleges ORDER BY created_at DESC").fetchall())
    admins   = dict_rows(db.execute(
        "SELECT u.*,c.name as college_name,c.college_code FROM users u "
        "LEFT JOIN colleges c ON u.college_id=c.id "
        "WHERE u.role='admin' ORDER BY u.created_at DESC").fetchall())
    pending  = dict_rows(db.execute(
        "SELECT * FROM college_registrations WHERE status='pending' "
        "ORDER BY submitted_at DESC").fetchall())
    total_students = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    total_teachers = db.execute("SELECT COUNT(*) FROM users WHERE role='teacher'").fetchone()[0]
    return render_template("super_admin/dashboard.html",
        colleges=colleges, admins=admins, pending_registrations=pending,
        total_students=total_students, total_teachers=total_teachers, user=current_user())


# ── College Registrations ─────────────────────────────────────────────
@sa_bp.route("/registrations")
@login_required
@role_required("super_admin")
def registrations():
    db = get_db()
    all_regs = dict_rows(db.execute(
        "SELECT * FROM college_registrations ORDER BY submitted_at DESC").fetchall())
    return render_template("super_admin/registrations.html",
                           registrations=all_regs, user=current_user())


@sa_bp.route("/registrations/<int:rid>/approve", methods=["POST"])
@login_required
@role_required("super_admin")
def approve_registration(rid):
    if not validate_csrf():
        abort(403)
    db  = get_db()
    reg = dict_row(db.execute(
        "SELECT * FROM college_registrations WHERE id=?", (rid,)).fetchone())
    if not reg or reg["status"] != "pending":
        flash("Registration not found or already processed.", "danger")
        return redirect(url_for("super_admin.registrations"))

    # Create college (ignore if code already exists)
    try:
        cur = db.execute("""INSERT INTO colleges(name,college_code,university,city,state,contact_email)
            VALUES(?,?,?,?,?,?)""",
            (reg["college_name"], reg["college_code"],
             reg.get("university", "RGPV Bhopal"),
             reg.get("city", ""), reg.get("state", ""), reg["contact_email"]))
        col_id = cur.lastrowid
    except Exception:
        existing = dict_row(db.execute(
            "SELECT id FROM colleges WHERE college_code=?",
            (reg["college_code"],)).fetchone())
        col_id = existing["id"] if existing else None

    # Generate unique admin username
    username = make_admin_username(reg["college_name"], reg["college_code"])
    base = username; idx = 1
    while db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        username = f"{base}{idx}"; idx += 1

    # Check for duplicate email
    email = reg.get("admin_email") or reg.get("contact_email")
    email = email if email else None
    
    if email and db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        flash(f"Error: Email {email} is already registered to another user.", "danger")
        return redirect(url_for("super_admin.registrations"))

    raw_password = generate_strong_password(14)
    db.execute("""INSERT INTO users(username,email,password,role,full_name,college_id,must_change_password)
        VALUES(?,?,?,?,?,?,1)""",
        (username, email, generate_password_hash(raw_password),
         "admin", reg.get("admin_full_name") or reg.get("contact_name"), col_id))
    db.execute("UPDATE college_registrations SET status='approved',reviewed_at=? WHERE id=?",
               (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), rid))
    db.commit()

    login_url = url_for("auth.login", _external=True)
    contact_email = reg.get("admin_email") or reg.get("contact_email")
    send_registration_approved(contact_email, reg["college_name"],
                               username, raw_password, login_url)
    flash(f"✅ '{reg['college_name']}' approved. Credentials sent to {contact_email}.",
          "success")
    return redirect(url_for("super_admin.registrations"))


@sa_bp.route("/registrations/<int:rid>/reject", methods=["POST"])
@login_required
@role_required("super_admin")
def reject_registration(rid):
    if not validate_csrf():
        abort(403)
    db     = get_db()
    reason = request.form.get("reason", "").strip()
    reg    = dict_row(db.execute(
        "SELECT * FROM college_registrations WHERE id=?", (rid,)).fetchone())
    if not reg:
        flash("Registration not found.", "danger")
        return redirect(url_for("super_admin.registrations"))
    db.execute(
        "UPDATE college_registrations SET status='rejected',reject_reason=?,reviewed_at=? WHERE id=?",
        (reason, datetime.utcnow().isoformat(), rid))
    db.commit()
    send_registration_rejected(reg.get("admin_email") or reg.get("contact_email"), reg["college_name"], reason)
    flash(f"Registration for '{reg['college_name']}' rejected.", "warning")
    return redirect(url_for("super_admin.registrations"))


# ── College CRUD ──────────────────────────────────────────────────────
@sa_bp.route("/colleges/create", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
def create_college():
    db = get_db()
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        f    = request.form
        name = sanitize_text(f.get("name", ""), 200)
        code = sanitize_alphanumeric(f.get("college_code", ""), 20)
        if not name or not code:
            flash("Name and college code are required.", "danger")
        else:
            try:
                db.execute(
                    "INSERT INTO colleges(name,college_code,city,state,contact_email) VALUES(?,?,?,?,?)",
                    (name, code,
                     sanitize_text(f.get("city",""), 100),
                     sanitize_text(f.get("state",""), 100),
                     sanitize_email(f.get("contact_email","")))
                )
                db.commit()
                flash(f"College '{name}' created.", "success")
                return redirect(url_for("super_admin.dashboard"))
            except Exception as e:
                flash(f"Error: {e}", "danger")
    return render_template("super_admin/college_form.html", college=None, user=current_user())


@sa_bp.route("/colleges/edit/<int:cid>", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
def edit_college(cid):
    db      = get_db()
    college = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (cid,)).fetchone())
    if not college:
        flash("College not found.", "danger")
        return redirect(url_for("super_admin.dashboard"))
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        f = request.form
        db.execute("UPDATE colleges SET name=?,city=?,state=?,contact_email=? WHERE id=?",
                   (sanitize_text(f.get("name",""),200),
                    sanitize_text(f.get("city",""),100),
                    sanitize_text(f.get("state",""),100),
                    sanitize_email(f.get("contact_email","")), cid))
        db.commit()
        flash("College updated.", "success")
        return redirect(url_for("super_admin.dashboard"))
    return render_template("super_admin/college_form.html", college=college, user=current_user())


@sa_bp.route("/colleges/delete/<int:cid>", methods=["POST"])
@login_required
@role_required("super_admin")
def delete_college(cid):
    if not validate_csrf():
        abort(403)
    db = get_db()
    college = dict_row(db.execute("SELECT name FROM colleges WHERE id=?", (cid,)).fetchone())
    if not college:
        flash("College not found.", "danger")
        return redirect(url_for("super_admin.dashboard"))

    # BUG-19 FIX: The original code ran O(students × semesters) individual DELETE
    # queries. With PRAGMA foreign_keys=ON and ON DELETE CASCADE set on all child
    # tables, a single DELETE on the parent cascades automatically. We only need
    # to explicitly delete tables that are NOT covered by CASCADE (i.e. those that
    # reference colleges.id via a non-CASCADE foreign key or have no FK at all).

    # 1. Students cascade → semester_records → subject_marks (all via CASCADE)
    db.execute("DELETE FROM students WHERE college_id=?", (cid,))

    # 2. Users cascade → password_reset_tokens, notifications (all via CASCADE)
    db.execute("DELETE FROM users WHERE college_id=?", (cid,))

    # 3. College-scoped tables with no cascade chain
    for tbl in ("fetch_logs", "student_registrations", "upload_logs",
                "dataset_versions", "anomaly_logs", "interventions"):
        db.execute(f"DELETE FROM {tbl} WHERE college_id=?", (cid,))

    # 4. Finally the college itself
    db.execute("DELETE FROM colleges WHERE id=?", (cid,))
    db.commit()

    flash(f"College '{college['name']}' and all associated data deleted.", "warning")
    return redirect(url_for("super_admin.dashboard"))


# ── Admin CRUD ────────────────────────────────────────────────────────
@sa_bp.route("/admins/create", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
def create_admin():
    db       = get_db()
    colleges = dict_rows(db.execute("SELECT * FROM colleges ORDER BY name").fetchall())
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        f      = request.form
        uname  = sanitize_alphanumeric(f.get("username",""), 50).lower()
        pwd    = f.get("password","").strip()
        email  = sanitize_email(f.get("email",""))
        email  = email if email else None
        col_id = sanitize_integer(f.get("college_id",""), default=0, min_val=1)
        if not uname or not pwd or not col_id:
            flash("All fields are required.", "danger")
        elif db.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone():
            flash("Username already taken.", "danger")
        else:
            db.execute(
                "INSERT INTO users(username,email,password,role,full_name,college_id,must_change_password) "
                "VALUES(?,?,?,?,?,?,1)",
                (uname, email, generate_password_hash(pwd),
                 "admin", sanitize_name(f.get("full_name",""), 100), col_id))
            db.commit()
            
            if email:
                col_name = "Your College"
                if col_id:
                    col_row = dict_row(db.execute("SELECT name FROM colleges WHERE id=?", (col_id,)).fetchone())
                    if col_row:
                        col_name = col_row["name"]
                login_url = url_for("auth.login", _external=True)
                from utils.mailer import send_registration_approved
                send_registration_approved(email, col_name, uname, pwd, login_url)
                flash(f"Admin '{uname}' created. Credentials sent to {email}.", "success")
            else:
                flash(f"Admin '{uname}' created.", "success")
                
            return redirect(url_for("super_admin.dashboard"))
    return render_template("super_admin/admin_form.html", admin=None,
                           colleges=colleges, user=current_user())


@sa_bp.route("/admins/edit/<int:uid>", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
def edit_admin(uid):
    db       = get_db()
    admin    = dict_row(db.execute(
        "SELECT * FROM users WHERE id=? AND role='admin'", (uid,)).fetchone())
    colleges = dict_rows(db.execute("SELECT * FROM colleges ORDER BY name").fetchall())
    if not admin:
        flash("Admin not found.", "danger")
        return redirect(url_for("super_admin.dashboard"))
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        f      = request.form
        uname  = sanitize_alphanumeric(f.get("username",""), 50).lower()
        email  = sanitize_email(f.get("email",""))
        email  = email if email else None
        col_id = sanitize_integer(f.get("college_id",""), default=0, min_val=1)
        if db.execute("SELECT id FROM users WHERE username=? AND id!=?",
                      (uname, uid)).fetchone():
            flash("Username already taken.", "danger")
        else:
            db.execute("UPDATE users SET username=?,full_name=?,email=?,college_id=? WHERE id=?",
                       (uname, sanitize_name(f.get("full_name",""),100), sanitize_email(f.get("email","")),
                        int(col_id) if col_id else None, uid))
            if f.get("password", "").strip():
                db.execute("UPDATE users SET password=? WHERE id=?",
                           (generate_password_hash(f["password"]), uid))
            db.commit()
            flash("Admin updated.", "success")
            return redirect(url_for("super_admin.dashboard"))
    return render_template("super_admin/admin_form.html", admin=admin,
                           colleges=colleges, user=current_user())


@sa_bp.route("/admins/delete/<int:uid>", methods=["POST"])
@login_required
@role_required("super_admin")
def delete_admin(uid):
    if not validate_csrf():
        abort(403)
    db = get_db()
    db.execute("DELETE FROM users WHERE id=? AND role='admin'", (uid,))
    db.commit()
    flash("Admin deleted.", "warning")
    return redirect(url_for("super_admin.dashboard"))


@sa_bp.route("/admins/send-reset/<int:uid>", methods=["POST"])
@login_required
@role_required("super_admin")
def send_reset_to_admin(uid):
    if not validate_csrf():
        abort(403)
    import secrets as _sec
    from datetime import timedelta
    db    = get_db()
    admin = dict_row(db.execute(
        "SELECT * FROM users WHERE id=? AND role='admin'", (uid,)).fetchone())
    if not admin:
        flash("Admin not found.", "danger")
        return redirect(url_for("super_admin.dashboard"))
    if not admin.get("email"):
        flash("This admin has no email address configured.", "danger")
        return redirect(url_for("super_admin.dashboard"))
    token   = _sec.token_urlsafe(48)
    expires = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
    db.execute("DELETE FROM password_reset_tokens WHERE user_id=?", (admin["id"],))
    db.execute("INSERT INTO password_reset_tokens(user_id,token,expires_at) VALUES(?,?,?)",
               (admin["id"], token, expires))
    db.commit()
    from utils.mailer import send_password_reset_link
    send_password_reset_link(
        admin["email"], admin.get("full_name") or admin["username"],
        url_for("auth.reset_password", token=token, _external=True))
    flash(f"✅ Password reset link sent to {admin['email']}.", "success")
    return redirect(url_for("super_admin.dashboard"))


@sa_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
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
            errs.append("New password must contain at least one uppercase letter.")
        if not any(c.islower() for c in new_pwd):
            errs.append("New password must contain at least one lowercase letter.")
        if not any(c.isdigit() for c in new_pwd):
            errs.append("New password must contain at least one digit.")
        if new_pwd != confirm_pwd:
            errs.append("Passwords do not match.")
        if errs:
            for e in errs:
                flash(e, "danger")
        else:
            db.execute("UPDATE users SET password=?, must_change_password=0 WHERE id=?",
                       (generate_password_hash(new_pwd), user["id"]))
            db.commit()
            flash("✅ Password changed successfully.", "success")
            return redirect(url_for("super_admin.dashboard"))
    return render_template("super_admin/change_password.html", user=user)

# ── Audit Log Viewer (moved from Admin) ────────────────────────────────
@sa_bp.route("/audit-log")
@login_required
@role_required("super_admin")
def audit_log():
    u   = current_user()
    db  = get_db()
    page     = sanitize_integer(request.args.get("page", 1), default=1, min_val=1, max_val=99999)
    per_page = 50
    role_f   = sanitize_text(request.args.get("role", ""), 20)
    action_f = sanitize_text(request.args.get("action", ""), 50)
    user_f   = sanitize_alphanumeric(request.args.get("username", ""), 50)
    offset   = (page - 1) * per_page

    q      = "SELECT * FROM audit_logs WHERE 1=1"
    params = []
    if role_f:
        q += " AND role=?"; params.append(role_f)
    if action_f:
        q += " AND action LIKE ?"; params.append(f"%{action_f}%")
    if user_f:
        q += " AND username LIKE ?"; params.append(f"%{user_f}%")
    count_q = "SELECT COUNT(*) FROM audit_logs WHERE 1=1"
    count_params = []
    if role_f:   count_q += " AND role=?";           count_params.append(role_f)
    if action_f: count_q += " AND action LIKE ?";    count_params.append(f"%{action_f}%")
    if user_f:   count_q += " AND username LIKE ?";  count_params.append(f"%{user_f}%")
    total = db.execute(count_q, count_params).fetchone()[0]
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [per_page, offset]
    logs  = dict_rows(db.execute(q, params).fetchall())
    pages = (total + per_page - 1) // per_page

    stats = dict_row(db.execute("""
        SELECT
          COUNT(*) as total_today,
          SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as errors_today,
          AVG(latency_ms) as avg_latency
        FROM audit_logs
        WHERE created_at >= datetime('now','-1 day')
    """).fetchone()) or {}

    return render_template("super_admin/audit_log.html",
        logs=logs, total=total, page=page, pages=pages,
        per_page=per_page, stats=stats,
        role_f=role_f, action_f=action_f, user_f=user_f, user=u)
