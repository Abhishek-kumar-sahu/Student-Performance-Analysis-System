"""routes/auth.py — SPAS: Login, logout, forgot/reset, college reg, student self-reg
[Security Hardened]: CSRF, brute-force lockout, input sanitization, rate limiting]
"""
import secrets
from functools import wraps
from datetime import datetime, timedelta
from flask import Blueprint, session, request, redirect, url_for, render_template, flash, abort
from werkzeug.security import check_password_hash, generate_password_hash
from database import get_db, dict_row, generate_strong_password, make_admin_username
from utils.mailer import (send_password_reset_link, send_new_registration_to_superadmin,
                          send_registration_approved, send_registration_rejected,
                          send_student_registration_received)
from security import (validate_csrf, sanitize_text, sanitize_name, sanitize_email,
                      sanitize_phone, sanitize_alphanumeric, sanitize_integer,
                      contains_xss, check_login_lockout, record_login_failure,
                      record_login_success, get_client_ip, rate_limit)

auth_bp = Blueprint("auth", __name__)
RESET_EXPIRE_MINUTES = 30

from .branches import BRANCH_CHOICES, BRANCH_DICT, PROGRAMME_BRANCHES, PROGRAMME_CHOICES

# ── Auth decorators ───────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "user_id" not in session:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("auth.login"))
        # Verify user still exists in DB (catches stale sessions after DB reset)
        if current_user() is None:
            session.clear()
            flash("Your session has expired. Please sign in again.", "warning")
            return redirect(url_for("auth.login"))
        return f(*a, **kw)
    return w

def role_required(*roles):
    def dec(f):
        @wraps(f)
        def w(*a, **kw):
            if "user_id" not in session:
                return redirect(url_for("auth.login"))
            u = current_user()
            if u is None:
                session.clear()
                flash("Your session has expired. Please sign in again.", "warning")
                return redirect(url_for("auth.login"))
            if session.get("role") not in roles:
                abort(403)
            return f(*a, **kw)
        return w
    return dec

def current_user():
    """Return the current logged-in user dict, cached per request in flask.g."""
    try:
        from flask import g
        if not hasattr(g, '_current_user'):
            uid = session.get("user_id")
            g._current_user = (
                dict_row(get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
                if uid else None
            )
        return g._current_user
    except RuntimeError:
        # Outside request context (tests, seeding)
        uid = session.get("user_id") if session else None
        return dict_row(get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()) if uid else None

def _redirect_role(role):
    return redirect(url_for({
        "super_admin":"super_admin.dashboard","admin":"admin.dashboard",
        "teacher":"teacher.dashboard","student":"student.dashboard"
    }.get(role,"auth.login")))

# ── Login — with CSRF + brute-force lockout ──────────────────────────

@auth_bp.route("/login", methods=["GET","POST"])
@rate_limit(max_calls=20, window=60)      # max 20 requests/min per IP
def login():
    if session.get("user_id"):
        return _redirect_role(session.get("role",""))

    if request.method == "POST":
        # ── CSRF check ────────────────────────────────────────────────
        if not validate_csrf():
            abort(403)

        ip = get_client_ip()

        # ── Brute-force lockout ───────────────────────────────────────
        locked, remaining = check_login_lockout(ip)
        if locked:
            flash(f"Too many failed attempts. Try again in {remaining // 60} min {remaining % 60} sec.", "danger")
            return render_template("login.html")

        # ── Sanitize inputs ──────────────────────────────────────────
        username = sanitize_text(request.form.get("username",""), max_len=80).lower()
        password = request.form.get("password","")      # raw; hashed, never echoed

        if contains_xss(username):
            flash("Invalid input.", "danger")
            return render_template("login.html")

        db   = get_db()
        user = dict_row(db.execute("SELECT * FROM users WHERE username=? OR email=?", (username, username)).fetchone())

        if user and check_password_hash(user["password"], password):
            if user.get("is_active", 1) == 0:
                flash("Your account has been deactivated. Contact admin.", "danger")
                return render_template("login.html")

            record_login_success(ip)    # clear failure counter

            # Set session variables
            for k in ["id","role","username","full_name","college_id","student_id","department"]:
                session[k if k!="id" else "user_id"] = user.get(k)
            session["full_name"] = user.get("full_name") or user["username"]

            if user.get("must_change_password") == 1:
                flash("Please change your password before continuing.", "info")
                target = {
                    "student": "student.change_password",
                    "teacher": "teacher.change_password",
                    "admin": "admin.change_password",
                    "super_admin": "super_admin.change_password"
                }.get(user["role"])
                if target:
                    return redirect(url_for(target))
            
            flash(f"Welcome back, {session['full_name']}!", "success")
            return _redirect_role(user["role"])

        # ── Failed login ─────────────────────────────────────────────
        record_login_failure(ip)
        locked, remaining = check_login_lockout(ip)
        if locked:
            flash(f"Account temporarily locked for {remaining // 60} min {remaining % 60} sec due to too many failures.", "danger")
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout", methods=["GET","POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


# ── Forgot / Reset password — with CSRF + rate limit ────────────────

@auth_bp.route("/forgot-password", methods=["GET","POST"])
@rate_limit(max_calls=5, window=300)     # max 5 reset requests per 5 min per IP
def forgot_password():
    if request.method == "POST":
        if not validate_csrf():
            abort(403)

        email = sanitize_email(request.form.get("email",""))
        db    = get_db()
        user  = dict_row(db.execute(
            "SELECT * FROM users WHERE LOWER(email)=?", (email,)).fetchone())
        flash("If that email is registered, a reset link has been sent.", "info")
        if user and email:
            token   = secrets.token_urlsafe(48)
            expires = (datetime.utcnow() + timedelta(minutes=RESET_EXPIRE_MINUTES)).isoformat()
            db.execute("DELETE FROM password_reset_tokens WHERE user_id=?", (user["id"],))
            db.execute("INSERT INTO password_reset_tokens(user_id,token,expires_at) VALUES(?,?,?)",
                       (user["id"], token, expires))
            db.commit()
            send_password_reset_link(
                user["email"], user.get("full_name") or user["username"],
                url_for("auth.reset_password", token=token, _external=True),
                RESET_EXPIRE_MINUTES)
        return redirect(url_for("auth.forgot_password"))
    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET","POST"])
def reset_password(token):
    # Validate token is hex-safe before querying DB
    if not re_safe_token(token):
        flash("Invalid reset link.", "danger")
        return redirect(url_for("auth.forgot_password"))

    db  = get_db()
    rec = dict_row(db.execute(
        "SELECT prt.*,u.email,u.full_name,u.username FROM password_reset_tokens prt "
        "JOIN users u ON prt.user_id=u.id WHERE prt.token=? AND prt.used=0", (token,)).fetchone())
    if not rec:
        flash("This reset link is invalid or already used.", "danger")
        return redirect(url_for("auth.forgot_password"))
    if datetime.utcnow().isoformat() > rec["expires_at"]:
        db.execute("DELETE FROM password_reset_tokens WHERE token=?", (token,)); db.commit()
        flash("This reset link has expired. Please request a new one.", "danger")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        pwd  = request.form.get("password","")
        pwd2 = request.form.get("confirm_password","")
        errs = _validate_password(pwd, pwd2)
        if errs:
            for e in errs: flash(e, "danger")
            return render_template("reset_password.html", token=token, rec=rec)
        db.execute("UPDATE users SET password=?,must_change_password=0 WHERE id=?",
                   (generate_password_hash(pwd), rec["user_id"]))
        db.execute("UPDATE password_reset_tokens SET used=1 WHERE token=?", (token,))
        db.commit()
        flash("Password updated! Please sign in with your new password.", "success")
        return redirect(url_for("auth.login"))
    return render_template("reset_password.html", token=token, rec=rec)


def re_safe_token(token: str) -> bool:
    """Accept only URL-safe base64 characters (64 chars min)."""
    import re
    return bool(re.match(r'^[A-Za-z0-9_\-]{32,128}$', token or ""))


def _validate_password(pwd: str, pwd2: str) -> list:
    errs = []
    if len(pwd) < 8:                         errs.append("At least 8 characters required.")
    if pwd != pwd2:                           errs.append("Passwords do not match.")
    if not any(c.isupper() for c in pwd):    errs.append("At least one uppercase letter.")
    if not any(c.islower() for c in pwd):    errs.append("At least one lowercase letter.")
    if not any(c.isdigit() for c in pwd):    errs.append("At least one digit.")
    return errs


# ── College Registration — with CSRF + sanitization ─────────────────

@auth_bp.route("/register-college", methods=["GET","POST"])
@rate_limit(max_calls=10, window=300)
def register_college():
    if request.method == "POST":
        if not validate_csrf():
            abort(403)

        f = request.form
        college_name  = sanitize_text(f.get("college_name",""), 200)
        college_code  = sanitize_alphanumeric(f.get("college_code",""), 20)
        university    = sanitize_text(f.get("university","RGPV Bhopal"), 200)
        city          = sanitize_text(f.get("city",""), 100)
        state         = sanitize_text(f.get("state","Madhya Pradesh"), 100)
        contact_name  = sanitize_name(f.get("contact_name",""), 100)
        contact_email = sanitize_email(f.get("contact_email",""))
        contact_phone = sanitize_phone(f.get("contact_phone",""))
        website       = sanitize_text(f.get("website",""), 200)
        message       = sanitize_text(f.get("message",""), 1000)

        errors = []
        if not college_name:  errors.append("College name is required.")
        if not college_code:  errors.append("College code is required.")
        if not contact_name:  errors.append("Contact person name is required.")
        if not contact_email: errors.append("Valid email is required.")
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("register_college.html", form=f)

        db = get_db()
        if db.execute("SELECT id FROM colleges WHERE college_code=?", (college_code,)).fetchone():
            flash("College code is already registered.", "danger")
            return render_template("register_college.html", form=f)
        existing = db.execute("SELECT id, status FROM college_registrations WHERE college_code=?", (college_code,)).fetchone()
        if existing:
            if existing["status"] == "pending":
                flash("A pending registration for this college code already exists. Please wait for approval.", "warning")
                return render_template("register_college.html", form=f)
            elif existing["status"] == "approved":
                flash("This college code is already approved and registered.", "danger")
                return render_template("register_college.html", form=f)
            else: # status is rejected
                # Delete the rejected one so we can insert a fresh pending request
                db.execute("DELETE FROM college_registrations WHERE id=?", (existing["id"],))

        db.execute("""INSERT INTO college_registrations
            (college_name,college_code,university,city,state,admin_full_name,admin_email,contact_phone,website,message)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (college_name,college_code,university,city,state,contact_name,contact_email,contact_phone,website,message))
        db.commit()

        sa = dict_row(db.execute("SELECT email FROM users WHERE role='super_admin' LIMIT 1").fetchone())
        if sa and sa.get("email"):
            send_new_registration_to_superadmin(sa["email"],
                {"college_name":college_name,"college_code":college_code,
                 "contact_name":contact_name,"contact_email":contact_email,
                 "city":city,"state":state},
                url_for("super_admin.registrations", _external=True))

        flash("Registration submitted! You'll receive credentials via email once approved.", "success")
        return redirect(url_for("auth.login"))
    return render_template("register_college.html", form={})


# ── Student Self-Registration — with CSRF + sanitization ─────────────

@auth_bp.route("/register-student", methods=["GET","POST"])
@rate_limit(max_calls=10, window=300)
def register_student():
    db = get_db()
    colleges = [dict(r) for r in db.execute("SELECT id,name,college_code FROM colleges ORDER BY name").fetchall()]

    if request.method == "POST":
        if not validate_csrf():
            abort(403)

        f = request.form
        enrollment_no = sanitize_alphanumeric(f.get("enrollment_no",""), 30)
        full_name     = sanitize_name(f.get("full_name",""), 100)
        email         = sanitize_email(f.get("email",""))
        email         = email if email else None
        phone         = sanitize_phone(f.get("phone",""))
        branch_code   = sanitize_alphanumeric(f.get("branch_code",""), 10)
        semester      = sanitize_integer(f.get("semester","1"), default=1, min_val=1, max_val=8)
        college_id    = sanitize_integer(f.get("college_id",""), default=0, min_val=1)
        gender        = sanitize_text(f.get("gender",""), 10)
        programme     = sanitize_text(f.get("programme","BE"), 10)

        errs = []
        if not enrollment_no: errs.append("Enrollment number is required.")
        if not full_name:     errs.append("Full name is required.")
        if not email:         errs.append("Valid email address is required.")
        if not branch_code:   errs.append("Please select a branch.")
        if not college_id:    errs.append("Please select your college.")

        if enrollment_no and (
            db.execute("SELECT id FROM students WHERE enrollment_no=?", (enrollment_no,)).fetchone() or
            db.execute("SELECT id FROM student_registrations WHERE enrollment_no=?", (enrollment_no,)).fetchone()
        ):
            errs.append("This enrollment number is already registered or has a pending request.")

        if email and (
            db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone() or
            db.execute("SELECT id FROM student_registrations WHERE email=?", (email,)).fetchone()
        ):
            errs.append("This email address is already registered or has a pending request.")

        if errs:
            for e in errs: flash(e, "danger")
            return render_template("register_student.html", form=f, colleges=colleges, branches=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES)

        branch_map  = dict(BRANCH_CHOICES)
        branch_name = branch_map.get(branch_code, branch_code)  # trusted catalogue value — do NOT sanitize_text (would HTML-escape &)
        col = dict_row(db.execute("SELECT college_code FROM colleges WHERE id=?", (college_id,)).fetchone())
        college_code_str = col["college_code"] if col else ""

        db.execute("""INSERT INTO student_registrations
            (enrollment_no,name,email,phone,branch,branch_code,programme,semester,
             college_id,college_code,gender,registered_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,'self')""",
            (enrollment_no,full_name,email,phone,branch_name,branch_code,programme,
             semester,college_id,college_code_str,gender))
        db.commit()

        send_student_registration_received(email, full_name, enrollment_no)
        flash("Registration submitted! Your college admin will review it and send your login credentials.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register_student.html", form={}, colleges=colleges, branches=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES)


@auth_bp.route("/")
@auth_bp.route("/splash")
def splash():
    if session.get("user_id"): return _redirect_role(session.get("role",""))
    return render_template("splash.html")

@auth_bp.route("/register")
def register_page():
    db = get_db()
    colleges = [dict(r) for r in db.execute("SELECT id,name,college_code FROM colleges ORDER BY name").fetchall()]
    return render_template("register.html", colleges=colleges, branches=BRANCH_CHOICES, programme_branches=PROGRAMME_BRANCHES, form={})
