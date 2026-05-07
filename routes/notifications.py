"""routes/notifications.py — SPAS v4+
In-app notification bell for teachers, admins, and students.
Blueprint section: Phase 10 — Performance Monitoring / alerts.
"""
from flask import Blueprint, jsonify, redirect, url_for
from database import get_db, dict_rows
from routes.auth import login_required, current_user
from security import validate_csrf

notif_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@notif_bp.route("/unread-count")
@login_required
def unread_count():
    u  = current_user()
    db = get_db()
    cnt = db.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
        (u["id"],)).fetchone()[0]
    return jsonify({"count": cnt})


@notif_bp.route("/list")
@login_required
def list_notifications():
    u    = current_user()
    db   = get_db()
    rows = dict_rows(db.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
        (u["id"],)).fetchall())
    return jsonify(rows)


@notif_bp.route("/mark-read/<int:nid>", methods=["POST"])
@login_required
def mark_read(nid):
    if not validate_csrf():
        return jsonify({"error": "CSRF"}), 403
    u  = current_user()
    db = get_db()
    db.execute(
        "UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
        (nid, u["id"]))
    db.commit()
    return jsonify({"ok": True})


@notif_bp.route("/mark-all-read", methods=["POST"])
@login_required
def mark_all_read():
    if not validate_csrf():
        return jsonify({"error": "CSRF"}), 403
    u  = current_user()
    db = get_db()
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (u["id"],))
    db.commit()
    return jsonify({"ok": True})


@notif_bp.route("/clear", methods=["POST"])
@login_required
def clear_all():
    if not validate_csrf():
        return jsonify({"error": "CSRF"}), 403
    u  = current_user()
    db = get_db()
    db.execute("DELETE FROM notifications WHERE user_id=? AND is_read=1", (u["id"],))
    db.commit()
    return jsonify({"ok": True})


def push_notification(db, user_id, title, message, notif_type="info", link=None):
    """Helper: push a notification to a user. Call from anywhere."""
    db.execute(
        "INSERT INTO notifications(user_id,title,message,type,link) VALUES(?,?,?,?,?)",
        (user_id, title, message, notif_type, link))
