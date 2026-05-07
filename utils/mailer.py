"""utils/mailer.py — SPAS v4 Email System (SMTP + console fallback)"""
import os, smtplib, logging, html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

log = logging.getLogger("mailer")

FROM_NAME = "SPAS — Student Performance Analysis System"
APP_NAME  = "SPAS"

# BUG-22 FIX: Read SMTP credentials at call time, not at import time.
# Reading at import time means: (a) credential rotation requires a full restart,
# (b) if SMTP_USER is empty at startup but set later, _is_configured() always
# returns False for the entire lifetime of that worker process.
def _smtp_cfg() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASS", ""),
    }

def _is_configured() -> bool:
    cfg = _smtp_cfg()
    return bool(cfg["user"] and cfg["password"])

def _base_template(title, content, footer_note=""):
    yr = datetime.now().year
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#f0f4f8;font-family:'Segoe UI',Helvetica,Arial,sans-serif;color:#1a202c}}
.wrapper{{max-width:600px;margin:32px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.1)}}
.header{{background:linear-gradient(135deg,#1e3a5f 0%,#2d6a9f 100%);padding:32px 40px;text-align:center}}
.logo-box{{display:inline-flex;align-items:center;gap:12px;margin-bottom:8px}}
.logo-icon{{width:48px;height:48px;background:rgba(255,255,255,.15);border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:1.5rem;border:1px solid rgba(255,255,255,.25)}}
.logo-text{{font-size:1.8rem;font-weight:800;color:#fff;letter-spacing:3px}}
.header-sub{{font-size:.75rem;color:rgba(255,255,255,.65);letter-spacing:1px}}
.header h1{{font-size:1.3rem;color:#fff;font-weight:700;margin-top:20px}}
.body{{padding:36px 40px}}
.body p{{font-size:.92rem;color:#4a5568;line-height:1.8;margin-bottom:14px}}
.info-box{{background:#f7fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px 20px;margin:16px 0}}
.info-row{{display:flex;gap:12px;padding:6px 0;border-bottom:1px solid #e2e8f0;font-size:.85rem}}
.info-row:last-child{{border-bottom:none}}
.info-label{{color:#718096;font-weight:600;min-width:140px}}
.info-value{{color:#1a202c;font-weight:700;word-break:break-all}}
.cta-btn{{display:inline-block;margin:20px 0 8px;padding:14px 32px;background:linear-gradient(135deg,#2563eb,#1d4ed8);border-radius:10px;color:#fff;text-decoration:none;font-weight:700;font-size:.95rem;letter-spacing:.3px}}
.warn-box{{background:#fff8f0;border:1px solid #fed7aa;border-radius:10px;padding:14px 18px;margin:16px 0;font-size:.82rem;color:#92400e}}
.footer{{background:#f7fafc;padding:20px 40px;text-align:center;font-size:.72rem;color:#a0aec0;border-top:1px solid #e2e8f0}}
</style></head><body>
<div class="wrapper">
  <div class="header">
    <div class="logo-box">
      <div class="logo-icon"><i class="fa-solid fa-bolt" style="color:#00ffc8;"></i></div>
      <div class="logo-text">{APP_NAME}</div>
    </div>
    <div class="header-sub">Student Performance Analysis System</div>
    <h1>{title}</h1>
  </div>
  <div class="body">{content}</div>
  <div class="footer">
    © {yr} SPAS — Student Performance Analysis System<br>
    {footer_note or "This is an automated message. Please do not reply."}
  </div>
</div></body></html>"""

def send_email(to, subject, html_content, text=""):
    if not to:
        log.error("[Mailer] No recipient email provided.")
        return False
    if not _is_configured():
        log.warning(f"[Mailer] SMTP not configured — printing to console")
        print(f"\n{'─'*65}")
        print(f"📧  TO      : {to}")
        print(f"📋  SUBJECT : {subject}")
        print(f"📝  TEXT    : {(text or 'HTML email')[:500]}")
        print(f"{'─'*65}\n")
        return True
    cfg = _smtp_cfg()
    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = f"{FROM_NAME} <{cfg['user']}>"
        msg["To"]      = to
        msg["Subject"] = subject
        if text: msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html_content, "html"))
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
            s.ehlo(); s.starttls(); s.login(cfg["user"], cfg["password"])
            s.sendmail(cfg["user"], [to], msg.as_string())
        log.info(f"[Mailer] Sent to {to}")
        return True
    except Exception as e:
        log.error(f"[Mailer] Failed: {e}")
        return False

# ── Specific email functions ──────────────────────────────────────────

def send_password_reset_link(to_email, name, reset_url, expires_min=30):
    content = f"""
    <p>Hi <strong>{name}</strong>,</p>
    <p>We received a request to reset your SPAS account password. Click below to create a new password:</p>
    <div style="text-align:center"><a href="{reset_url}" class="cta-btn">Reset My Password →</a></div>
    <div class="info-box">
      <div class="info-row"><span class="info-label">⏱ Link expires in</span><span class="info-value">{expires_min} minutes</span></div>
    </div>
    <div class="warn-box"><i class="fa-solid fa-triangle-exclamation"></i> If you did not request this, ignore this email. Your password will remain unchanged. Never share this link.</div>
    """
    return send_email(to_email, "[SPAS] Password Reset Request",
                      _base_template("Reset Your Password", content),
                      f"Reset your SPAS password (expires {expires_min} min): {reset_url}")

def send_new_registration_to_superadmin(sa_email, reg, dashboard_url):
    content = f"""
    <p>A new college has submitted a registration request and is awaiting your approval.</p>
    <div class="info-box">
      <div class="info-row"><span class="info-label">College Name</span><span class="info-value">{reg['college_name']}</span></div>
      <div class="info-row"><span class="info-label">College Code</span><span class="info-value">{reg['college_code']}</span></div>
      <div class="info-row"><span class="info-label">Contact Person</span><span class="info-value">{reg['contact_name']}</span></div>
      <div class="info-row"><span class="info-label">Contact Email</span><span class="info-value">{reg['contact_email']}</span></div>
      <div class="info-row"><span class="info-label">Location</span><span class="info-value">{reg.get('city','')} / {reg.get('state','')}</span></div>
    </div>
    <div style="text-align:center"><a href="{dashboard_url}" class="cta-btn">Review in Dashboard →</a></div>
    <div class="warn-box"><i class="fa-solid fa-circle-exclamation"></i> Do not approve institutions you do not recognize. Verify the college code before approving.</div>
    """
    return send_email(sa_email, f"[SPAS] New College Registration — {reg['college_name']}",
                      _base_template("New College Registration Request", content))

def send_registration_approved(admin_email, college_name, username, password, login_url):
    # Escape for HTML template
    e_user = html.escape(str(username))
    e_pass = html.escape(str(password))
    content = f"""
    <p>Great news! Your college registration on SPAS has been <strong style="color:#16a34a">approved</strong> by the Super Administrator.</p>
    <p>Your admin account credentials are below. Please sign in and change your password immediately.</p>
    <div class="info-box">
      <div class="info-row"><span class="info-label">College</span><span class="info-value">{college_name}</span></div>
      <div class="info-row"><span class="info-label">Username</span><span class="info-value">{e_user}</span></div>
      <div class="info-row"><span class="info-label">Temp Password</span><span class="info-value">{e_pass}</span></div>
    </div>
    <div style="text-align:center"><a href="{login_url}" class="cta-btn">Sign In to SPAS →</a></div>
    <div class="warn-box"><i class="fa-solid fa-circle-info"></i> This is a temporary password. Use the <strong>Forgot Password</strong> link on login to set a permanent one. Never share these credentials.</div>
    """
    return send_email(admin_email, f"[SPAS] Registration Approved — Welcome to SPAS!",
                      _base_template("Registration Approved", content),
                      f"SPAS account approved. Username: {username} | Password: {password} | Login: {login_url}")

def send_registration_rejected(admin_email, college_name, reason):
    content = f"""
    <p>We regret to inform you that your registration request for <strong>{college_name}</strong> has been <strong style="color:#dc2626">rejected</strong>.</p>
    <div class="info-box">
      <div class="info-row"><span class="info-label">Reason</span><span class="info-value">{reason or 'No reason provided.'}</span></div>
    </div>
    <p>If you believe this is an error, please contact the SPAS administrator or re-submit with corrected information.</p>
    """
    return send_email(admin_email, f"[SPAS] Registration Request Rejected — {college_name}",
                      _base_template("Registration Rejected", content))

def send_student_registration_received(student_email, student_name, enrollment_no):
    content = f"""
    <p>Hi <strong>{student_name}</strong>,</p>
    <p>Your student registration request has been received and is pending review by your college admin.</p>
    <div class="info-box">
      <div class="info-row"><span class="info-label">Enrollment No.</span><span class="info-value">{enrollment_no}</span></div>
      <div class="info-row"><span class="info-label">Status</span><span class="info-value">Pending Review</span></div>
    </div>
    <p>You will receive another email once your registration is approved with your login credentials.</p>
    <div class="warn-box"><i class="fa-solid fa-circle-info"></i> If you did not submit this registration, please contact your college admin immediately.</div>
    """
    return send_email(student_email, "[SPAS] Student Registration Received",
                      _base_template("Registration Received", content))

def send_student_approved(student_email, student_name, username, password, login_url):
    # Escape for HTML
    e_user = html.escape(str(username))
    e_pass = html.escape(str(password))
    content = f"""
    <p>Hi <strong>{student_name}</strong>,</p>
    <p>Your student account on SPAS has been <strong style="color:#16a34a">approved</strong>! You can now log in and view your academic records.</p>
    <div class="info-box">
      <div class="info-row"><span class="info-label">Username</span><span class="info-value">{e_user}</span></div>
      <div class="info-row"><span class="info-label">Password</span><span class="info-value">{e_pass}</span></div>
    </div>
    <div style="text-align:center"><a href="{login_url}" class="cta-btn">Access My Academic Portal →</a></div>
    <div class="warn-box"><i class="fa-solid fa-key"></i> Please change your password after first login. Keep your credentials confidential.</div>
    """
    return send_email(student_email, "[SPAS] Your Student Account is Ready!",
                      _base_template("Account Approved", content),
                      f"SPAS student account approved. Username: {username} | Password: {password} | Login: {login_url}")

def send_student_rejected(student_email, student_name, reason):
    content = f"""
    <p>Hi <strong>{student_name}</strong>,</p>
    <p>Your student registration request has been <strong style="color:#dc2626">rejected</strong>.</p>
    <div class="info-box">
      <div class="info-row"><span class="info-label">Reason</span><span class="info-value">{reason or 'Please contact your admin for details.'}</span></div>
    </div>
    <p>Please contact your college admin for assistance.</p>
    """
    return send_email(student_email, "[SPAS] Student Registration Status Update",
                      _base_template("Registration Not Approved", content))

def send_teacher_created_student(student_email, student_name, enrollment_no, teacher_name, username, password, login_url):
    # Escape for HTML
    e_user = html.escape(str(username))
    e_pass = html.escape(str(password))
    content = f"""
    <p>Hi <strong>{student_name}</strong>,</p>
    <p>Your teacher <strong>{teacher_name}</strong> has registered you on SPAS. Your account is ready to use.</p>
    <div class="info-box">
      <div class="info-row"><span class="info-label">Enrollment No.</span><span class="info-value">{enrollment_no}</span></div>
      <div class="info-row"><span class="info-label">Username</span><span class="info-value">{e_user}</span></div>
      <div class="info-row"><span class="info-label">Password</span><span class="info-value">{e_pass}</span></div>
    </div>
    <div style="text-align:center"><a href="{login_url}" class="cta-btn">Access My Academic Portal →</a></div>
    <div class="warn-box"><i class="fa-solid fa-key"></i> Please change your password after first login.</div>
    """
    return send_email(student_email, "[SPAS] Your Academic Portal Account is Ready",
                      _base_template("Account Created by Teacher", content),
                      f"SPAS account created. Username: {username} | Password: {password} | Login: {login_url}")

def send_teacher_account_created(teacher_email, teacher_name, username, password, login_url):
    # Escape for HTML
    e_user = html.escape(str(username))
    e_pass = html.escape(str(password))
    content = f"""
    <p>Hi <strong>{teacher_name}</strong>,</p>
    <p>Your college administrator has created a teacher account for you on SPAS. You can now log in to manage your classes and students.</p>
    <div class="info-box">
      <div class="info-row"><span class="info-label">Username</span><span class="info-value">{e_user}</span></div>
      <div class="info-row"><span class="info-label">Temporary Password</span><span class="info-value">{e_pass}</span></div>
    </div>
    <div class="warn-box"><i class="fa-solid fa-key"></i> Please change your password after first login.</div>
    """
    return send_email(teacher_email, "[SPAS] Your Teacher Account is Ready",
                      _base_template("Teacher Account Created", content),
                      f"SPAS account created. Username: {username} | Password: {password} | Login: {login_url}")

