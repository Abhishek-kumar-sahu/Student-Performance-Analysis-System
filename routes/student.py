"""routes/student.py — Student routes — all analysis powered by SPAS AI"""
import io
from flask import Blueprint, render_template, redirect, url_for, flash, send_file, request, jsonify, abort
from werkzeug.security import generate_password_hash
from database import get_db, dict_row, dict_rows
from routes.auth import login_required, role_required, current_user
from utils.pdf_generator import generate_student_report
from services.gemini_service import (
    gemini_predict, gemini_analytics_insight, gemini_digital_twin,
    gemini_chat, get_student_prediction_data, save_prediction
)
from security import validate_csrf, sanitize_text, sanitize_name, sanitize_email, sanitize_phone, sanitize_integer, sanitize_alphanumeric, contains_xss, get_client_ip, sanitize_ai_html

student_bp = Blueprint("student", __name__, url_prefix="/student")


def _load_semesters(db, student_id):
    recs = dict_rows(db.execute(
        "SELECT * FROM semester_records WHERE student_id=? ORDER BY semester_no",
        (student_id,)).fetchall())
    for r in recs:
        r["subjects"] = dict_rows(db.execute(
            "SELECT * FROM subject_marks WHERE semester_record_id=? ORDER BY subject_name",
            (r["id"],)).fetchall())
    return recs


@student_bp.route("/dashboard")
@login_required
@role_required("student")
def dashboard():
    u  = current_user()
    db = get_db()
    if not u.get("student_id"):
        flash("No student profile linked to your account.", "warning")
        return redirect(url_for("auth.login"))
    student = dict_row(db.execute("SELECT * FROM students WHERE id=?", (u["student_id"],)).fetchone())
    if not student:
        flash("Student record not found. Contact admin.", "warning")
        return redirect(url_for("auth.login"))
    col       = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (student["college_id"],)).fetchone())
    semesters = _load_semesters(db, student["id"])

    pred_data = get_student_prediction_data(db, student["id"])
    # BUG-27 FIX: Previously the live AI API was called on EVERY dashboard load,
    # each with an 18-second timeout. The ml_predictions table was written but
    # never read back. Now: use the cached prediction if it exists and is
    # less than 24 hours old; only call the AI if the cache is stale/missing.
    import json as _json
    _cached_pred = db.execute(
        "SELECT * FROM ml_predictions WHERE student_id=? AND semester_no=? "
        "AND created_at > datetime('now','-1 day') ORDER BY created_at DESC LIMIT 1",
        (student["id"], pred_data.get("current_semester", 0))
    ).fetchone()
    if _cached_pred:
        ml_pred = {
            "risk_level":      _cached_pred["risk_level"] or "medium",
            "risk_score":      _cached_pred["risk_score"] or 50,
            "predicted_gpa":   _cached_pred["predicted_gpa"] or 0,
            "current_gpa":     _cached_pred["current_gpa"] or 0,
            "risk_factors":    _json.loads(_cached_pred["risk_factors"] or "[]"),
            "recommendations": _json.loads(_cached_pred["recommendations"] or "[]"),
            "performance_summary": f"Risk: {(_cached_pred['risk_level'] or 'medium').title()}",
        }
    else:
        ml_pred = gemini_predict(pred_data)
        try:
            save_prediction(db, student["id"], pred_data.get("current_semester", 0), ml_pred)
        except Exception:
            pass
    ml_pred["icon"]       = {"low":"🏆","medium":"📈","high":"⚠️","critical":"🚨"}.get(ml_pred.get("risk_level","medium"),"📊")
    ml_pred["label"]      = ml_pred.get("performance_summary", ml_pred.get("risk_level","").title())
    ml_pred["confidence"] = ml_pred.get("risk_score", 50)
    ml_pred["color"]      = {"low":"#00ff99","medium":"#ffcc00","high":"#ff8800","critical":"#ff4444"}.get(ml_pred.get("risk_level","medium"),"#8b949e")
    ml_pred["cgpa"]       = pred_data.get("previous_gpa", 0)
    ml_pred["attendance"] = pred_data.get("avg_attendance", 0)

    current_sem      = semesters[-1] if semesters else None
    current_subjects = current_sem["subjects"] if current_sem else []
    sgpa_trend = [{"sem":s["semester_no"],"sgpa":s.get("sgpa",0) or 0,
                   "cgpa":s.get("cgpa",0) or 0,"att":s.get("attendance",0) or 0}
                  for s in semesters]
    best = worst = None
    if current_subjects:
        best  = max(current_subjects, key=lambda x: x.get("grade_point",0) or 0)
        worst = min(current_subjects, key=lambda x: x.get("grade_point",0) or 0)
    return render_template("student/dashboard.html",
        student=student, col=col, semesters=semesters,
        current_sem=current_sem, current_subjects=current_subjects,
        sgpa_trend=sgpa_trend, ml_pred=ml_pred,
        best_subject=best, worst_subject=worst, user=u)


@student_bp.route("/report.pdf")
@login_required
@role_required("student")
def download_report():
    u  = current_user()
    db = get_db()
    if not u.get("student_id"):
        flash("No student record.", "danger")
        return redirect(url_for("student.dashboard"))
    student   = dict_row(db.execute("SELECT * FROM students WHERE id=?", (u["student_id"],)).fetchone())
    semesters = _load_semesters(db, student["id"])
    pdf   = generate_student_report(student, semesters)
    fname = f"academic_report_{student['enrollment_no']}.pdf"
    return send_file(io.BytesIO(pdf), as_attachment=True, download_name=fname, mimetype="application/pdf")


ALLOWED_EXTENSIONS = {"jpg","jpeg","png","gif","webp"}
MAX_PHOTO_KB = 20

def _allowed_photo(fn):
    return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS


@student_bp.route("/profile")
@login_required
@role_required("student")
def profile():
    u  = current_user()
    db = get_db()
    if not u.get("student_id"):
        flash("No student profile linked.", "warning")
        return redirect(url_for("student.dashboard"))
    student       = dict_row(db.execute("SELECT * FROM students WHERE id=?", (u["student_id"],)).fetchone())
    col           = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (student["college_id"],)).fetchone()) if student else None
    semesters     = _load_semesters(db, student["id"])
    interventions = dict_rows(db.execute(
        "SELECT i.*, u2.full_name as logged_by_name FROM interventions i "
        "LEFT JOIN users u2 ON i.logged_by=u2.id "
        "WHERE i.student_id=? ORDER BY i.created_at DESC",
        (student["id"],)).fetchall())
    return render_template("student/profile.html",
        student=student, col=col, semesters=semesters,
        interventions=interventions, user=u)


@student_bp.route("/profile/edit", methods=["GET","POST"])
@login_required
@role_required("student")
def edit_profile():
    u  = current_user()
    db = get_db()
    if not u.get("student_id"):
        flash("No student profile linked.", "warning")
        return redirect(url_for("student.dashboard"))
    student = dict_row(db.execute("SELECT * FROM students WHERE id=?", (u["student_id"],)).fetchone())
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        phone = sanitize_phone(request.form.get("phone") or "")
        parent_phone = sanitize_phone(request.form.get("parent_phone") or "")
        address = sanitize_text(request.form.get("address") or "", max_len=200)
        semester = sanitize_integer(request.form.get("current_semester") or "1", default=1, min_val=1, max_val=8)
        bio   = sanitize_text(request.form.get("bio") or "", max_len=500)
        file  = request.files.get("profile_photo")
        photo_data = None
        if file and file.filename:
            if not _allowed_photo(file.filename):
                flash("Only JPG, PNG, GIF, WEBP allowed.", "danger")
                return render_template("student/edit_profile.html", student=student, user=u)
            raw = file.read()
            if len(raw) > MAX_PHOTO_KB * 1024:
                flash(f"Photo must be under {MAX_PHOTO_KB} KB.", "danger")
                return render_template("student/edit_profile.html", student=student, user=u)
            import base64
            ext  = file.filename.rsplit(".",1)[1].lower()
            mime = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","gif":"image/gif","webp":"image/webp"}.get(ext,"image/jpeg")
            photo_data = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
        
        q = "UPDATE students SET phone=?, parent_phone=?, address=?, current_semester=?, bio=?"
        params = [phone, parent_phone, address, semester, bio]
        if photo_data:
            q += ", profile_photo=?"
            params.append(photo_data)
            # Sync photo to users table
            db.execute("UPDATE users SET profile_photo=? WHERE id=?", (photo_data, u["id"]))
        
        q += " WHERE id=?"
        params.append(student["id"])
        db.execute(q, params)
        db.commit()
        flash("✅ Profile updated.", "success")
        return redirect(url_for("student.profile"))
    return render_template("student/edit_profile.html", student=student, user=u)


@student_bp.route("/notifications")
@login_required
@role_required("student")
def notifications_page():
    u  = current_user()
    db = get_db()
    notifs = dict_rows(db.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (u["id"],)).fetchall())
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (u["id"],))
    db.commit()
    return render_template("student/notifications.html", notifs=notifs, user=u)


@student_bp.route("/my-interventions")
@login_required
@role_required("student")
def my_interventions():
    u  = current_user()
    db = get_db()
    if not u.get("student_id"):
        return jsonify([])
    items = dict_rows(db.execute(
        "SELECT i.*, u.full_name as teacher_name "
        "FROM interventions i LEFT JOIN users u ON i.logged_by=u.id "
        "WHERE i.student_id=? ORDER BY i.created_at DESC",
        (u["student_id"],)).fetchall())
    return jsonify(items)


# ── Analytics — AI-powered ────────────────────────────────────────────────

@student_bp.route("/analytics")
@login_required
@role_required("student")
def analytics():
    u  = current_user()
    db = get_db()
    if not u.get("student_id"):
        return redirect(url_for("student.dashboard"))
    student   = dict_row(db.execute("SELECT * FROM students WHERE id=?", (u["student_id"],)).fetchone())
    semesters = _load_semesters(db, student["id"])
    semester  = request.args.get("sem", student["current_semester"], type=int)

    semester_avg = {}
    for s in semesters:
        subs = s.get("subjects", [])
        if subs:
            pcts = [sub["total_marks"]/sub["max_marks"]*100
                    for sub in subs if sub.get("max_marks") and sub["max_marks"] > 0]
            semester_avg[s["semester_no"]] = round(sum(pcts)/len(pcts), 2) if pcts else 0
        else:
            semester_avg[s["semester_no"]] = s.get("percentage", 0) or 0

    # Single query to get all peers' percentages — avoids N+1 queries
    peer_rows = db.execute(
        "SELECT sr.percentage FROM students s "
        "LEFT JOIN semester_records sr ON s.id=sr.student_id AND sr.semester_no=? "
        "WHERE s.college_id=? AND s.branch_code=? AND s.id != ?",
        (semester, student["college_id"], student["branch_code"], student["id"])
    ).fetchall()
    all_students_count = len(peer_rows) + 1  # include self
    my_avg = semester_avg.get(semester, 0)
    rank = 1 + sum(1 for r in peer_rows if (r["percentage"] or 0) > my_avg)
    all_students = [{"id": None}] * all_students_count  # keep len() usage working

    pred_data  = get_student_prediction_data(db, student["id"], semester)
    # FIX: Use cached prediction (≤24 hrs old) to avoid live AI call on every page load.
    # This mirrors the same caching logic used in the main dashboard route.
    import json as _json
    _cached_pred = db.execute(
        "SELECT * FROM ml_predictions WHERE student_id=? AND semester_no=? "
        "AND created_at > datetime('now','-1 day') ORDER BY created_at DESC LIMIT 1",
        (student["id"], semester)
    ).fetchone()
    if _cached_pred:
        prediction = {
            "risk_level":      _cached_pred["risk_level"] or "medium",
            "risk_score":      _cached_pred["risk_score"] or 50,
            "predicted_gpa":   _cached_pred["predicted_gpa"] or 0,
            "current_gpa":     _cached_pred["current_gpa"] or 0,
            "risk_factors":    _json.loads(_cached_pred["risk_factors"] or "[]"),
            "recommendations": _json.loads(_cached_pred["recommendations"] or "[]"),
            "performance_summary": f"Risk: {(_cached_pred['risk_level'] or 'medium').title()}",
        }
    else:
        prediction = gemini_predict(pred_data)
        if prediction:
            try:
                save_prediction(db, student["id"], semester, prediction)
            except Exception:
                pass

    insight = gemini_analytics_insight(student, semesters, semester, rank, max(len(all_students), 1))

    return render_template("student/analytics.html",
        student=student, semesters=semesters, semester=semester,
        semester_avg=semester_avg, rank=rank,
        total_in_dept=all_students_count,
        prediction=prediction, insight=insight, user=u)


# ── AI Advisor — chat ──────────────────────────────────────────────────

@student_bp.route("/ai-advisor")
@login_required
@role_required("student")
def ai_advisor():
    u  = current_user()
    db = get_db()
    if not u.get("student_id"):
        return redirect(url_for("student.dashboard"))
    student    = dict_row(db.execute("SELECT * FROM students WHERE id=?", (u["student_id"],)).fetchone())
    semesters  = _load_semesters(db, student["id"])
    pred_data  = get_student_prediction_data(db, student["id"])
    # FIX: use cached prediction to avoid live API call on every page load
    import json as _json
    _cached = db.execute(
        "SELECT * FROM ml_predictions WHERE student_id=? AND semester_no=? "
        "AND created_at > datetime('now','-1 day') ORDER BY created_at DESC LIMIT 1",
        (student["id"], pred_data.get("current_semester", 0))
    ).fetchone()
    if _cached:
        prediction = {
            "risk_level":      _cached["risk_level"] or "medium",
            "risk_score":      _cached["risk_score"] or 50,
            "predicted_gpa":   _cached["predicted_gpa"] or 0,
            "current_gpa":     _cached["current_gpa"] or 0,
            "risk_factors":    _json.loads(_cached["risk_factors"] or "[]"),
            "recommendations": _json.loads(_cached["recommendations"] or "[]"),
            "performance_summary": f"Risk: {(_cached['risk_level'] or 'medium').title()}",
        }
    else:
        prediction = gemini_predict(pred_data)
        if prediction:
            try:
                save_prediction(db, student["id"], pred_data.get("current_semester", 0), prediction)
            except Exception:
                pass
    return render_template("student/ai_advisor.html",
        student=student, semesters=semesters,
        prediction=prediction, data=pred_data, user=u)


@student_bp.route("/ai-advisor/ask", methods=["POST"])
@login_required
@role_required("student")
def ai_advisor_ask():
    if not validate_csrf():
        abort(403)
    u  = current_user()
    db = get_db()
    raw_question = str((request.json or {}).get("question", "")).strip()[:500]
    if contains_xss(raw_question):
        return jsonify({"answer": "Invalid input detected."}), 400
    question = raw_question
    if not u.get("student_id"):
        return jsonify({"answer": "No student profile found."})
    student    = dict_row(db.execute("SELECT * FROM students WHERE id=?", (u["student_id"],)).fetchone())
    pred_data  = get_student_prediction_data(db, student["id"])
    # FIX: reuse cached prediction instead of calling AI API on every chat message
    import json as _json
    _cached = db.execute(
        "SELECT * FROM ml_predictions WHERE student_id=? AND semester_no=? "
        "AND created_at > datetime('now','-1 day') ORDER BY created_at DESC LIMIT 1",
        (student["id"], pred_data.get("current_semester", 0))
    ).fetchone()
    if _cached:
        prediction = {
            "risk_level":      _cached["risk_level"] or "medium",
            "risk_score":      _cached["risk_score"] or 50,
            "predicted_gpa":   _cached["predicted_gpa"] or 0,
            "risk_factors":    _json.loads(_cached["risk_factors"] or "[]"),
            "recommendations": _json.loads(_cached["recommendations"] or "[]"),
        }
    else:
        prediction = gemini_predict(pred_data)
    answer     = gemini_chat(student, pred_data, prediction, question)
    return jsonify({"answer": sanitize_ai_html(answer)})

# ── Digital Twin — AI simulation ─────────────────────────────────────────

@student_bp.route("/digital-twin", methods=["GET","POST"])
@login_required
@role_required("student")
def digital_twin():
    u  = current_user()
    db = get_db()
    if not u.get("student_id"):
        return redirect(url_for("student.dashboard"))
    student    = dict_row(db.execute("SELECT * FROM students WHERE id=?", (u["student_id"],)).fetchone())
    semesters  = _load_semesters(db, student["id"])
    pred_data  = get_student_prediction_data(db, student["id"])
    twin_result = None
    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        scenario = {
            "avg_attendance":  sanitize_integer(request.form.get("scenario_attendance", pred_data.get("avg_attendance", 75)), default=75, min_val=0, max_val=100),
            "avg_marks":       sanitize_integer(request.form.get("scenario_marks", pred_data.get("avg_marks", 60)), default=60, min_val=0, max_val=100),
            "failed_subjects": sanitize_integer(request.form.get("scenario_failed", pred_data.get("failed_subjects", 0)), default=0, min_val=0, max_val=50),
        }
        twin_result = gemini_digital_twin(pred_data, scenario)
    return render_template("student/digital_twin.html",
        student=student, semesters=semesters,
        base_data=pred_data, twin_result=twin_result, user=u)


@student_bp.route("/digital-twin/api", methods=["POST"])
@login_required
@role_required("student")
def digital_twin_api():
    if not validate_csrf():
        abort(403)
    u  = current_user()
    db = get_db()
    if not u.get("student_id"):
        return jsonify({"error": "No profile"}), 400
    student   = dict_row(db.execute("SELECT * FROM students WHERE id=?", (u["student_id"],)).fetchone())
    pred_data = get_student_prediction_data(db, student["id"])
    data      = request.json or {}
    scenario  = {
        "avg_attendance":  sanitize_integer(data.get("attendance", pred_data.get("avg_attendance", 75)), default=75, min_val=0, max_val=100),
        "avg_marks":       sanitize_integer(data.get("marks", pred_data.get("avg_marks", 60)), default=60, min_val=0, max_val=100),
        "failed_subjects": sanitize_integer(data.get("failed", pred_data.get("failed_subjects", 0)), default=0, min_val=0, max_val=50),
    }
    result = gemini_digital_twin(pred_data, scenario)
    return jsonify(result)


# ── Change Password ───────────────────────────────────────────────────
@student_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@role_required("student")
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
            return redirect(url_for("student.dashboard"))
    return render_template("student/change_password.html", user=user)
