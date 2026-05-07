"""app.py — SPAS Flask Application Factory  [Security Hardened]"""
import os, time, sqlite3
from dotenv import load_dotenv
load_dotenv()
from datetime import timedelta
from flask import Flask, render_template, g, request, session
from database import init_db, close_db, upgrade_db_v2, DB_PATH
from routes.auth         import auth_bp
from routes.super_admin  import sa_bp
from routes.admin        import admin_bp
from routes.teacher      import teacher_bp
from routes.student      import student_bp
from routes.upload       import upload_bp
from routes.interventions import intervention_bp
from routes.notifications import notif_bp
from routes.analytics    import analytics_bp
from security import apply_security_headers, generate_csrf_token


def create_app() -> Flask:
    app = Flask(__name__)
    app.debug = True
    print("[SPAS] DEBUG MODE ENABLED IN FACTORY")

    # ── Secret key — MUST be set in .env in production ───────────────
    secret = os.environ.get("SECRET_KEY", "")
    if not secret or secret == "your-secret-key-change-this-in-production":
        import warnings as _w, secrets as _s
        _w.warn(
            "[SPAS] SECRET_KEY is not set in .env — using a random ephemeral key. "
            "Every restart will invalidate ALL active user sessions. "
            "Set SECRET_KEY in your .env file to fix this.",
            stacklevel=2,
        )
        secret = _s.token_hex(32)
    app.secret_key = secret

    app.permanent_session_lifetime = timedelta(hours=12)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    # ── Secure cookie settings ────────────────────────────────────────
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("HTTPS", "false").lower() == "true"

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(sa_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(intervention_bp)
    app.register_blueprint(notif_bp)

    app.teardown_appcontext(close_db)

    @app.context_processor
    def inject_csrf():
        return {"csrf_token": generate_csrf_token()}

    @app.before_request
    def _start_timer():
        g.req_start = time.time()

    @app.after_request
    def _after(response):
        try:
            apply_security_headers(response)
        except Exception:
            pass
        if not request.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"]  = "no-cache"
            response.headers["Expires"] = "0"
        try:
            from routes.auth import current_user as _cu
            u = _cu()
            if u and not request.path.startswith("/static"):
                latency = int((time.time() - g.get("req_start", time.time())) * 1000)
                conn = sqlite3.connect(DB_PATH, timeout=5)
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("""INSERT INTO audit_logs
                    (user_id,username,role,action,endpoint,method,status_code,latency_ms,ip_address)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (u.get("id"), u.get("username"), u.get("role"),
                     f"{request.method} {request.path}",
                     request.path, request.method, response.status_code,
                     latency, request.remote_addr))
                conn.commit()
                conn.close()
        except Exception:
            pass
        return response

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400, msg="Bad Request."), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403,
                               msg="Access Denied — You don't have permission."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, msg="Page not found."), 404

    @app.errorhandler(429)
    def too_many(e):
        return render_template("error.html", code=429,
                               msg="Too many requests. Please slow down and try again later."), 429

    @app.errorhandler(500)
    def server_error(e):
        print(f"[SPAS] 500 ERROR DETECTED: {e}")
        import traceback
        traceback.print_exc()
        return render_template("error.html", code=500,
                               msg="An internal error occurred. Please try again."), 500

    @app.context_processor
    def inject_globals():
        try:
            from routes.auth import current_user
            u = current_user()
            notif_count = 0
            if u:
                try:
                    from database import get_db
                    db = get_db()
                    notif_count = db.execute(
                        "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
                        (u["id"],)).fetchone()[0]
                except Exception:
                    pass
            return {"current_user": u, "notif_count": notif_count}
        except Exception:
            return {"current_user": None, "notif_count": 0}

    with app.app_context():
        init_db()
        upgrade_db_v2()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
