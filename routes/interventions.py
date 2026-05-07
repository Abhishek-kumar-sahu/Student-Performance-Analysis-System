"""routes/interventions.py — SPAS v4+
Teachers log remedial interventions for at-risk students.
Students read-only see their own interventions on their dashboard.
[Security Hardened]: CSRF, sanitization, missing current_user() fixes
"""
from datetime import datetime
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, jsonify, abort)
from database import get_db, dict_row, dict_rows
from routes.auth import login_required, role_required, current_user
from security import (validate_csrf, sanitize_text, sanitize_integer,
                      contains_xss)

intervention_bp = Blueprint("interventions", __name__, url_prefix="/interventions")

INTERVENTION_TYPES = [
    "Academic Counseling",
    "Parent/Guardian Contact",
    "Tutoring / Extra Classes",
    "Attendance Warning",
    "Performance Review Meeting",
    "Scholarship/Aid Referral",
    "Mentorship Assignment",
    "Written Warning",
    "Other",
]

STATUSES = ["Pending", "In Progress", "Completed", "Cancelled"]


# ── Log new intervention ─────────────────────────────────────────────
@intervention_bp.route("/log/<int:student_id>", methods=["POST"])
@login_required
@role_required("teacher", "admin")
def log_intervention(student_id):
    if not validate_csrf():
        abort(403)
    u  = current_user()                    # FIX: was missing
    db = get_db()

    if not u or not u.get("college_id"):
        flash("Your account is not linked to a college.", "danger")
        return redirect(url_for("teacher.dashboard"))

    student = dict_row(db.execute(
        "SELECT * FROM students WHERE id=? AND college_id=?",
        (student_id, u["college_id"])).fetchone())
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("teacher.dashboard"))

    # Sanitize and whitelist-validate inputs
    itype     = sanitize_text(request.form.get("intervention_type", ""), 100)
    notes     = sanitize_text(request.form.get("notes", ""), 500)
    follow_up = sanitize_text(request.form.get("follow_up_date", ""), 20) or None
    status    = sanitize_text(request.form.get("status", "Pending"), 20)

    # Whitelist validation
    if itype not in INTERVENTION_TYPES:
        flash("Invalid intervention type selected.", "danger")
        return redirect(url_for("teacher.student_detail", sid=student_id))
    if status not in STATUSES:
        status = "Pending"

    # Validate date format if provided
    if follow_up:
        try:
            datetime.strptime(follow_up, "%Y-%m-%d")
        except ValueError:
            follow_up = None

    db.execute(
        "INSERT INTO interventions"
        "(student_id,college_id,logged_by,intervention_type,notes,status,follow_up_date)"
        " VALUES(?,?,?,?,?,?,?)",
        (student_id, u["college_id"], u["id"], itype, notes, status, follow_up))

    # Create in-app notification — sanitize content before storing
    stu_user = dict_row(db.execute(
        "SELECT id FROM users WHERE student_id=?", (student_id,)).fetchone())
    if stu_user:
        notif_msg = f"Your teacher has logged a '{itype}' intervention."
        if notes:
            notif_msg += f" Notes: {notes[:80]}"
        db.execute(
            "INSERT INTO notifications(user_id,title,message,type,link)"
            " VALUES(?,?,?,?,?)",
            (stu_user["id"],
             f"New Intervention: {itype}",
             notif_msg,
             "warning",
             url_for("student.dashboard")))

    db.commit()
    flash(f"✅ Intervention '{itype}' logged for {student['name']}.", "success")
    return redirect(url_for("teacher.student_detail", sid=student_id))


# ── Update intervention status ────────────────────────────────────────
@intervention_bp.route("/<int:iid>/update", methods=["POST"])
@login_required
@role_required("teacher", "admin")
def update_status(iid):
    if not validate_csrf():                # FIX: was missing
        abort(403)
    u      = current_user()
    db     = get_db()

    status = sanitize_text(request.form.get("status", "Pending"), 20)
    notes  = sanitize_text(request.form.get("notes", ""), 500)

    if status not in STATUSES:
        status = "Pending"

    iv = dict_row(db.execute(
        "SELECT i.* FROM interventions i "
        "JOIN students s ON i.student_id=s.id "
        "WHERE i.id=? AND s.college_id=?",
        (iid, u["college_id"])).fetchone())
    if not iv:
        flash("Intervention not found.", "danger")
        return redirect(url_for("teacher.dashboard"))

    db.execute(
        "UPDATE interventions SET status=?,notes=?,updated_at=? WHERE id=?",
        (status, notes or iv["notes"], datetime.utcnow().isoformat(), iid))
    db.commit()
    flash("Intervention updated.", "success")
    return redirect(url_for("teacher.student_detail", sid=iv["student_id"]))


# ── Delete intervention ───────────────────────────────────────────────
@intervention_bp.route("/<int:iid>/delete", methods=["POST"])
@login_required
@role_required("teacher", "admin")
def delete_intervention(iid):
    if not validate_csrf():
        abort(403)
    u  = current_user()                    # FIX: was missing
    db = get_db()

    iv = dict_row(db.execute(
        "SELECT i.* FROM interventions i "
        "JOIN students s ON i.student_id=s.id "
        "WHERE i.id=? AND s.college_id=?",
        (iid, u["college_id"])).fetchone())
    if not iv:
        flash("Intervention not found.", "danger")
        return redirect(url_for("teacher.dashboard"))
    db.execute("DELETE FROM interventions WHERE id=?", (iid,))
    db.commit()
    flash("Intervention deleted.", "warning")
    return redirect(url_for("teacher.student_detail", sid=iv["student_id"]))


# ── AJAX: list interventions for a student ────────────────────────────
@intervention_bp.route("/for-student/<int:student_id>")
@login_required
@role_required("teacher", "admin")
def list_for_student(student_id):
    db = get_db()
    u  = current_user()
    items = dict_rows(db.execute(
        "SELECT i.*, u.full_name as teacher_name "
        "FROM interventions i LEFT JOIN users u ON i.logged_by=u.id "
        "WHERE i.student_id=? AND i.college_id=? "
        "ORDER BY i.created_at DESC",
        (student_id, u["college_id"])).fetchall())
    return jsonify(items)
