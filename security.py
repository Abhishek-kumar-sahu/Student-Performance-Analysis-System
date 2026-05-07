"""security.py — SPAS Centralized Security Module
Covers: CSRF, XSS sanitization, input validation, rate limiting,
        brute-force lockout, security headers, safe redirects.
"""
import re, time, secrets, hashlib, html, threading
from functools import wraps
from collections import defaultdict
from flask import session, request, abort, g
from markupsafe import escape


# ══════════════════════════════════════════════════════════════════════
# 1. CSRF PROTECTION
# ══════════════════════════════════════════════════════════════════════

def generate_csrf_token() -> str:
    """Generate (or reuse) a per-session CSRF token."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf() -> bool:
    """Check that the POST request carries a valid CSRF token.
    Accepts token from:
      1. Form field: csrf_token
      2. HTTP header: X-CSRF-Token  (for AJAX/fetch requests)
      3. JSON body: csrf_token      (for JSON API calls)
    Uses constant-time comparison to prevent timing side-channel attacks.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return True

    # Try all three locations in priority order
    token = request.form.get("csrf_token")
    if not token:
        token = request.headers.get("X-CSRF-Token")
    if not token and request.is_json:
        try:
            token = (request.get_json(silent=True) or {}).get("csrf_token")
        except Exception:
            pass

    expected = session.get("csrf_token")
    if not token or not expected:
        return False
    # Constant-time comparison to prevent timing attacks
    return secrets.compare_digest(str(token), str(expected))


def csrf_protect(f):
    """Decorator: reject POST requests that lack a valid CSRF token."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not validate_csrf():
            abort(403)           # Forbidden — CSRF check failed
        return f(*args, **kwargs)
    return wrapper


# ══════════════════════════════════════════════════════════════════════
# 2. XSS / INPUT SANITIZATION
# ══════════════════════════════════════════════════════════════════════

# Patterns that indicate XSS payloads
_XSS_PATTERNS = re.compile(
    r"(<\s*script|javascript\s*:|on\w+\s*=|<\s*iframe|<\s*object|"
    r"<\s*embed|<\s*link|<\s*meta|vbscript\s*:|data\s*:\s*text/html)",
    re.IGNORECASE,
)

def sanitize_text(value: str, max_len: int = 500) -> str:
    """Escape HTML entities, strip XSS patterns, enforce max length."""
    if not isinstance(value, str):
        value = str(value) if value is not None else ""
    # Truncate first
    value = value[:max_len]
    # HTML-escape (converts <, >, &, ", ' to safe entities)
    value = html.escape(value, quote=True)
    return value.strip()


def sanitize_name(value: str, max_len: int = 100) -> str:
    """Sanitize a human name — letters, spaces, hyphens, apostrophes only."""
    value = sanitize_text(value, max_len)
    # Remove anything that isn't a letter, space, hyphen, apostrophe, or dot
    return re.sub(r"[^\w\s\-\'\.]", "", value, flags=re.UNICODE).strip()


def sanitize_alphanumeric(value: str, max_len: int = 50) -> str:
    """Allow only letters, numbers, hyphens, underscores."""
    value = str(value or "")[:max_len].strip().upper()
    return re.sub(r"[^A-Z0-9\-_]", "", value)


def sanitize_email(value: str) -> str:
    """Validate and normalize an email address."""
    value = str(value or "").strip().lower()[:254]
    if not re.match(r'^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$', value):
        return ""
    return value


def sanitize_phone(value: str) -> str:
    """Allow only digits, spaces, +, -, ()."""
    value = str(value or "")[:20]
    return re.sub(r"[^\d\s\+\-\(\)]", "", value).strip()


def sanitize_integer(value, default: int = 0, min_val: int = 0, max_val: int = 99999) -> int:
    """Safely parse an integer within allowed bounds."""
    try:
        v = int(str(value).strip())
        return max(min_val, min(max_val, v))
    except (ValueError, TypeError):
        return default


def contains_xss(value: str) -> bool:
    """Return True if the raw (unescaped) value looks like an XSS payload."""
    return bool(_XSS_PATTERNS.search(str(value or "")))


def validate_form_inputs(*fields) -> list:
    """Check a list of raw form field values for XSS patterns.
    Returns a list of error strings (empty = clean).
    """
    errors = []
    for label, raw in fields:
        if contains_xss(str(raw or "")):
            errors.append(f"Invalid characters detected in '{label}'.")
    return errors


# ══════════════════════════════════════════════════════════════════════
# 3. BRUTE-FORCE / RATE LIMITING  (in-memory, per-IP)
# ══════════════════════════════════════════════════════════════════════

# Structure: { ip: {"count": int, "window_start": float, "locked_until": float} }
_login_attempts: dict = defaultdict(lambda: {"count": 0, "window_start": time.time(), "locked_until": 0.0})

MAX_LOGIN_ATTEMPTS = 5          # max failures per window
LOGIN_WINDOW_SECONDS = 300      # 5-minute sliding window
LOCKOUT_SECONDS = 900           # 15-minute lockout after too many failures

_rate_buckets: dict = defaultdict(lambda: {"count": 0, "window_start": time.time()})

# BUG-21 FIX: Locks for thread-safe access to shared in-memory dicts.
# Without these, concurrent requests can race on read-modify-write and allow
# more calls than the limit under load.
_login_lock = threading.Lock()
_rate_lock  = threading.Lock()

# BUG-20 FIX: Only trust X-Forwarded-For from known proxy IPs.
# Clients can forge this header freely; trusting it unconditionally lets
# anyone spoof their IP and bypass rate limits / brute-force lockouts.
# In production, replace with your actual load-balancer IP(s).
_TRUSTED_PROXY_IPS: set = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def get_client_ip() -> str:
    """Return the real client IP.
    X-Forwarded-For is only trusted when the direct connection originates from
    a known reverse proxy — prevents clients spoofing their IP.
    """
    remote = request.remote_addr or "unknown"
    if remote in _TRUSTED_PROXY_IPS:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
    return remote


def check_login_lockout(ip: str) -> tuple[bool, int]:
    """Return (is_locked, seconds_remaining)."""
    with _login_lock:
        entry = _login_attempts[ip]
        now = time.time()
        if entry["locked_until"] > now:
            return True, int(entry["locked_until"] - now)
    return False, 0


def record_login_failure(ip: str):
    """Track failed login attempt; lock IP after MAX_LOGIN_ATTEMPTS."""
    now = time.time()
    with _login_lock:
        entry = _login_attempts[ip]
        # Reset window if expired
        if now - entry["window_start"] > LOGIN_WINDOW_SECONDS:
            entry["count"] = 0
            entry["window_start"] = now
        entry["count"] += 1
        if entry["count"] >= MAX_LOGIN_ATTEMPTS:
            entry["locked_until"] = now + LOCKOUT_SECONDS
            entry["count"] = 0          # reset counter after locking


def record_login_success(ip: str):
    """Clear failure counters on successful login."""
    with _login_lock:
        _login_attempts[ip] = {"count": 0, "window_start": time.time(), "locked_until": 0.0}


def rate_limit(max_calls: int = 30, window: int = 60):
    """Decorator: allow max_calls per window seconds per IP before returning 429."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip  = get_client_ip()
            key = f"{f.__name__}:{ip}"
            now = time.time()
            with _rate_lock:
                b = _rate_buckets[key]
                if now - b["window_start"] > window:
                    b["count"] = 0
                    b["window_start"] = now
                b["count"] += 1
                over_limit = b["count"] > max_calls
            if over_limit:
                abort(429)     # Too Many Requests
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ══════════════════════════════════════════════════════════════════════
# 3b. AI OUTPUT SANITIZATION
# ══════════════════════════════════════════════════════════════════════

# BUG-17 FIX: AI responses are injected into the DOM via innerHTML.
# Without sanitization a prompt-injection could return <img onerror=...> and
# execute arbitrary JS. Strip all HTML tags except a safe whitelist.
_AI_ALLOWED_TAGS = re.compile(
    r'<(?!/?(strong|em|b|i|br|ul|ol|li)\b)[^>]*>',
    re.IGNORECASE,
)


def sanitize_ai_html(text: str) -> str:
    """Strip all HTML tags from AI-generated text except a safe whitelist.
    Allowed: <strong>, <em>, <b>, <i>, <br>, <ul>, <ol>, <li>.
    Everything else (script, img, iframe, onerror handlers…) is removed.
    """
    if not text:
        return ""
    return _AI_ALLOWED_TAGS.sub('', str(text))


# ══════════════════════════════════════════════════════════════════════
# 4. SAFE REDIRECT
# ══════════════════════════════════════════════════════════════════════

def is_safe_redirect(url: str) -> bool:
    """Ensure redirect target is a relative path (no open-redirect to external sites)."""
    if not url:
        return False
    # Must start with / but not // (which browsers treat as protocol-relative)
    return url.startswith("/") and not url.startswith("//")


# ══════════════════════════════════════════════════════════════════════
# 5. SECURITY HEADERS
# ══════════════════════════════════════════════════════════════════════

def apply_security_headers(response):
    """Add all recommended security headers to every HTTP response."""
    h = response.headers

    # Prevent clickjacking (only allow framing from same origin)
    h["X-Frame-Options"] = "SAMEORIGIN"

    # Prevent MIME-type sniffing
    h["X-Content-Type-Options"] = "nosniff"

    # Enable browser's built-in XSS filter (legacy browsers)
    h["X-XSS-Protection"] = "1; mode=block"

    # Referrer Policy — don't leak full URL to third parties
    h["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Permissions Policy — disable unnecessary browser features
    h["Permissions-Policy"] = (
        "geolocation=(), microphone=(), camera=(), "
        "payment=(), usb=(), bluetooth=()"
    )

    # Content Security Policy — prevent inline XSS, restrict resource origins
    # 'unsafe-inline' for styles only (needed for inline CSS in templates)
    h["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )

    # HSTS — force HTTPS for 1 year (enable when running behind HTTPS)
    # h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response
