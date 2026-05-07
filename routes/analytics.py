"""routes/analytics.py — Advanced analytics: subject difficulty, attendance correlation,
   AI natural-language query, multi-college comparison, trend tracking, anomaly detection."""
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from datetime import datetime
from database import get_db, dict_row, dict_rows
from routes.auth import login_required, role_required, current_user
from services.gemini_service import (
    gemini_subject_difficulty, gemini_attendance_correlation,
    gemini_nl_query, gemini_multi_college, gemini_trend_analysis,
    gemini_anomaly_detection, gemini_enhanced_report
)
from security import validate_csrf, sanitize_text, sanitize_name, sanitize_email, sanitize_phone, sanitize_integer, sanitize_alphanumeric, contains_xss, get_client_ip, sanitize_ai_html

analytics_bp = Blueprint("analytics", __name__, url_prefix="/analytics")

def _require_college(u):
    """Return college_id or None after flashing error."""
    cid = u.get("college_id")
    if not cid:
        flash("Your account is not linked to a college. Contact admin.", "danger")
    return cid

# ── Subject Difficulty Ranking ─────────────────────────────────────────
@analytics_bp.route("/subject-difficulty")
@login_required
@role_required("admin", "teacher")
def subject_difficulty():
    u  = current_user()
    db = get_db()
    if not _require_college(u): return redirect(url_for("auth.login"))
    branch_filter = sanitize_alphanumeric(request.args.get("branch", ""), 10)
    sem_filter    = sanitize_text(request.args.get("semester", ""), 2)

    q = """
        SELECT sm.subject_code, sm.subject_name,
               COUNT(*) as total_attempts,
               SUM(CASE WHEN sm.status='FAIL' THEN 1 ELSE 0 END) as fail_count,
               AVG(sm.total_marks) as avg_marks,
               AVG(sm.attendance_pct) as avg_attendance,
               AVG(sm.grade_point) as avg_grade_point
        FROM subject_marks sm
        JOIN semester_records sr ON sm.semester_record_id=sr.id
        JOIN students s ON sr.student_id=s.id
        WHERE s.college_id=? AND sm.subject_name != ''
    """
    params = [u["college_id"]]
    if branch_filter:
        q += " AND s.branch_code=?"; params.append(branch_filter)
    if sem_filter:
        q += " AND sr.semester_no=?"; params.append(int(sem_filter))
    q += " GROUP BY sm.subject_code, sm.subject_name HAVING total_attempts >= 3 ORDER BY fail_count DESC"

    subjects = dict_rows(db.execute(q, params).fetchall())
    for s in subjects:
        total = s["total_attempts"] or 1
        s["fail_rate"] = round((s["fail_count"] or 0) / total * 100, 1)
        s["pass_rate"] = round(100 - s["fail_rate"], 1)
        s["difficulty"] = ("Very Hard" if s["fail_rate"] >= 40 else
                           "Hard"      if s["fail_rate"] >= 25 else
                           "Medium"    if s["fail_rate"] >= 10 else "Easy")
        s["diff_color"] = ("#ef4444" if s["fail_rate"] >= 40 else
                           "#f97316" if s["fail_rate"] >= 25 else
                           "#eab308" if s["fail_rate"] >= 10 else "#22c55e")

    branches = dict_rows(db.execute(
        "SELECT DISTINCT branch_code, branch FROM students WHERE college_id=? ORDER BY branch",
        (u["college_id"],)).fetchall())
    sems = list(range(1, 9))

    ai_ranking = gemini_subject_difficulty(subjects) if subjects else {}
    return render_template("analytics/subject_difficulty.html",
        subjects=subjects, branches=branches, sems=sems,
        branch_filter=branch_filter, sem_filter=sem_filter,
        ai_ranking=ai_ranking, user=u)


# ── Attendance–Performance Correlation ───────────────────────────────
@analytics_bp.route("/attendance-correlation")
@login_required
@role_required("admin", "teacher")
def attendance_correlation():
    u  = current_user()
    db = get_db()
    if not _require_college(u): return redirect(url_for("auth.login"))
    branch_filter = sanitize_alphanumeric(request.args.get("branch", ""), 10)
    sem_filter    = request.args.get("semester", "")
    sem_val = int(sem_filter) if sem_filter.isdigit() else None

    col = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())
    q = """
        SELECT s.enrollment_no, s.name, s.branch,
               sr.attendance, sr.cgpa, sr.sgpa, sr.result,
               sr.backlog_count, sr.semester_no
        FROM students s
        JOIN semester_records sr ON s.id=sr.student_id 
           AND sr.semester_no = (CASE WHEN ? IS NOT NULL THEN ? ELSE s.current_semester END)
        WHERE s.college_id=? AND sr.attendance IS NOT NULL AND sr.cgpa IS NOT NULL
    """
    params = [sem_val, sem_val, u["college_id"]]
    if branch_filter:
        q += " AND s.branch_code=?"; params.append(branch_filter)
    if sem_val:
        q += " AND sr.semester_no=?"; params.append(sem_val)
    q += " ORDER BY sr.attendance"
    students = dict_rows(db.execute(q, params).fetchall())

    # Build correlation buckets
    buckets = {"<60": [], "60-75": [], "75-85": [], "85-100": []}
    for s in students:
        att = s["attendance"] or 0
        if att < 60:        buckets["<60"].append(s["cgpa"] or 0)
        elif att < 75:      buckets["60-75"].append(s["cgpa"] or 0)
        elif att < 85:      buckets["75-85"].append(s["cgpa"] or 0)
        else:               buckets["85-100"].append(s["cgpa"] or 0)

    bucket_stats = {}
    for k, v in buckets.items():
        bucket_stats[k] = {
            "count": len(v),
            "avg_cgpa": round(sum(v)/len(v), 2) if v else 0,
            "max_cgpa": round(max(v), 2) if v else 0,
            "min_cgpa": round(min(v), 2) if v else 0,
        }

    branches = dict_rows(db.execute(
        "SELECT DISTINCT branch_code, branch FROM students WHERE college_id=? ORDER BY branch",
        (u["college_id"],)).fetchall())

    sems = list(range(1, 9))
    ai_insight = gemini_attendance_correlation(students, bucket_stats) if students else ""

    return render_template("analytics/attendance_correlation.html",
        students=students, bucket_stats=bucket_stats, branches=branches, sems=sems,
        branch_filter=branch_filter, sem_filter=sem_filter, ai_insight=ai_insight, col=col, user=u)


# ── AI Natural-Language Query ─────────────────────────────────────────
@analytics_bp.route("/ai-query", methods=["GET", "POST"])
@login_required
@role_required("admin", "teacher")
def ai_query():
    u  = current_user()
    db = get_db()
    if not _require_college(u): return redirect(url_for("auth.login"))
    answer = None
    question = ""
    history = []

    col = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())
    students = dict_rows(db.execute(
        "SELECT s.name, s.enrollment_no, s.branch, s.branch_code, s.current_semester, "
        "  sr.cgpa, sr.sgpa, sr.attendance, sr.result, sr.backlog_count, sr.percentage "
        "FROM students s LEFT JOIN semester_records sr "
        "ON s.id=sr.student_id AND sr.semester_no=s.current_semester "
        "WHERE s.college_id=? ORDER BY sr.cgpa DESC NULLS LAST",
        (u["college_id"],)).fetchall())

    if request.method == "POST":
        if not validate_csrf():
            abort(403)
        question = request.form.get("question", "").strip()
        if question:
            answer = gemini_nl_query(question, students, col)

    return render_template("analytics/ai_query.html",
        question=question, answer=answer, col=col,
        total_students=len(students), user=u)

# ── AI Query API (AJAX) ────────────────────────────────────────────────
@analytics_bp.route("/ai-query/ask", methods=["POST"])
@login_required
@role_required("admin", "teacher")
def ai_query_ask():
    if not validate_csrf():
        abort(403)
    u  = current_user()
    db = get_db()
    if not u.get("college_id"): return jsonify({"answer": "Account not linked to a college."})
    raw_q = str((request.get_json(silent=True) or {}).get("question", "") or request.form.get("question", "")).strip()
    if contains_xss(raw_q):
        return jsonify({"answer": "Invalid input detected."}), 400
    question = raw_q[:500]
    if not question:
        return jsonify({"answer": "Please enter a question."})
    col = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())
    students = dict_rows(db.execute(
        "SELECT s.name, s.enrollment_no, s.branch, s.branch_code, s.current_semester, "
        "  sr.cgpa, sr.sgpa, sr.attendance, sr.result, sr.backlog_count, sr.percentage "
        "FROM students s LEFT JOIN semester_records sr "
        "ON s.id=sr.student_id AND sr.semester_no=s.current_semester "
        "WHERE s.college_id=? ORDER BY sr.cgpa DESC NULLS LAST",
        (u["college_id"],)).fetchall())
    answer = gemini_nl_query(question, students, col)
    return jsonify({"answer": sanitize_ai_html(answer)})


# ── Multi-College Comparison (Super Admin only) ───────────────────────
@analytics_bp.route("/multi-college")
@login_required
@role_required("super_admin")
def multi_college():
    db = get_db()
    colleges = dict_rows(db.execute("SELECT * FROM colleges ORDER BY name").fetchall())
    college_stats = []
    for col in colleges:
        cid = col["id"]
        students = dict_rows(db.execute(
            "SELECT s.*, sr.cgpa, sr.attendance, sr.result, sr.backlog_count "
            "FROM students s LEFT JOIN semester_records sr "
            "ON s.id=sr.student_id AND sr.semester_no=s.current_semester "
            "WHERE s.college_id=?", (cid,)).fetchall())
        total = len(students)
        if total == 0:
            teachers = db.execute("SELECT COUNT(*) FROM users WHERE role='teacher' AND college_id=?", (cid,)).fetchone()[0]
            college_stats.append({**col, "total": 0, "avg_cgpa": 0, "avg_att": 0,
                                   "pass_rate": 0, "at_risk": 0, "at_risk_pct": 0, "teachers": teachers})
            continue
        avg_cgpa = round(sum(s.get("cgpa") or 0 for s in students) / total, 2)
        avg_att  = round(sum(s.get("attendance") or 0 for s in students) / total, 1)
        passing  = sum(1 for s in students if str(s.get("result","")).upper() == "PASS")
        at_risk  = sum(1 for s in students if (s.get("cgpa") or 0) < 5.0 or (s.get("attendance") or 0) < 60)
        teachers = db.execute("SELECT COUNT(*) FROM users WHERE role='teacher' AND college_id=?", (cid,)).fetchone()[0]
        college_stats.append({**col, "total": total, "avg_cgpa": avg_cgpa, "avg_att": avg_att,
                               "pass_rate": round(passing/total*100, 1) if total else 0,
                               "at_risk": at_risk, "at_risk_pct": round(at_risk/total*100,1) if total else 0,
                               "teachers": teachers})

    ai_comparison = gemini_multi_college(college_stats) if college_stats else {}
    return render_template("analytics/multi_college.html",
        college_stats=college_stats, ai_comparison=ai_comparison,
        user=current_user())


# ── Long-Term Trend Tracking ──────────────────────────────────────────
@analytics_bp.route("/trends")
@login_required
@role_required("admin", "teacher")
def trends():
    u  = current_user()
    db = get_db()
    if not _require_college(u): return redirect(url_for("auth.login"))
    branch_filter = sanitize_alphanumeric(request.args.get("branch", ""), 10)
    sem_filter    = request.args.get("semester", "")

    q = """
        SELECT sr.semester_no,
               COUNT(DISTINCT sr.student_id) as students,
               AVG(sr.cgpa) as avg_cgpa,
               AVG(sr.sgpa) as avg_sgpa,
               AVG(sr.attendance) as avg_att,
               SUM(CASE WHEN sr.result='PASS' THEN 1 ELSE 0 END) as passing,
               SUM(CASE WHEN sr.result='FAIL' THEN 1 ELSE 0 END) as failing,
               AVG(sr.backlog_count) as avg_backlogs
        FROM semester_records sr
        JOIN students s ON sr.student_id=s.id
        WHERE s.college_id=?
    """
    params = [u["college_id"]]
    if branch_filter:
        q += " AND s.branch_code=?"; params.append(branch_filter)
    if sem_filter:
        q += " AND sr.semester_no=?"; params.append(int(sem_filter) if sem_filter.isdigit() else sem_filter)
    q += " GROUP BY sr.semester_no ORDER BY sr.semester_no"
    trend_data = dict_rows(db.execute(q, params).fetchall())
    for t in trend_data:
        t["avg_cgpa"]    = round(t["avg_cgpa"] or 0, 2)
        t["avg_sgpa"]    = round(t["avg_sgpa"] or 0, 2)
        t["avg_att"]     = round(t["avg_att"] or 0, 1)
        t["avg_backlogs"]= round(t["avg_backlogs"] or 0, 2)
        total = (t["passing"] or 0) + (t["failing"] or 0)
        t["pass_rate"]   = round((t["passing"] or 0)/total*100, 1) if total else 0

    branches = dict_rows(db.execute(
        "SELECT DISTINCT branch_code, branch FROM students WHERE college_id=? ORDER BY branch",
        (u["college_id"],)).fetchall())

    ai_trend = gemini_trend_analysis(trend_data, branch_filter) if trend_data else {}
    col = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())
    sems = list(range(1, 9))
    return render_template("analytics/trends.html",
        trend_data=trend_data, branches=branches, sems=sems,
        branch_filter=branch_filter, sem_filter=sem_filter, ai_trend=ai_trend, col=col, user=u)


# ── Anomaly Detection ─────────────────────────────────────────────────
@analytics_bp.route("/anomalies")
@login_required
@role_required("admin", "teacher")
def anomalies():
    u  = current_user()
    db = get_db()
    if not _require_college(u): return redirect(url_for("auth.login"))
    branch_filter = sanitize_alphanumeric(request.args.get("branch", ""), 10)
    sem_filter    = request.args.get("semester", "")

    # Get semester-over-semester failure spikes per branch
    q = """
        SELECT s.branch_code, s.branch,
               sr.semester_no,
               COUNT(*) as total,
               SUM(CASE WHEN sr.result='FAIL' THEN 1 ELSE 0 END) as fails,
               AVG(sr.cgpa) as avg_cgpa,
               AVG(sr.attendance) as avg_att
        FROM students s
        JOIN semester_records sr ON s.id=sr.student_id
        WHERE s.college_id=?
    """
    params = [u["college_id"]]
    if branch_filter:
        q += " AND s.branch_code=?"; params.append(branch_filter)
    if sem_filter:
        q += " AND sr.semester_no=?"; params.append(int(sem_filter) if sem_filter.isdigit() else sem_filter)
    q += " GROUP BY s.branch_code, sr.semester_no ORDER BY s.branch_code, sr.semester_no"
    sem_data = dict_rows(db.execute(q, params).fetchall())

    # Detect anomalies: sudden spike (fail rate up >15pp vs previous sem)
    anomaly_list = []
    prev = {}
    for row in sem_data:
        key = row["branch_code"]
        total = row["total"] or 1
        fail_rate = round((row["fails"] or 0) / total * 100, 1)
        row["fail_rate"] = fail_rate
        if key in prev:
            delta = fail_rate - prev[key]["fail_rate"]
            cgpa_delta = (row["avg_cgpa"] or 0) - (prev[key]["avg_cgpa"] or 0)
            if delta >= 15:
                anomaly_list.append({
                    "type": "Failure Spike",
                    "branch": row["branch"],
                    "semester": row["semester_no"],
                    "description": f"Failure rate jumped {delta:+.1f}pp (sem {row['semester_no']-1}→{row['semester_no']})",
                    "severity": "critical" if delta >= 25 else "high",
                    "fail_rate": fail_rate,
                    "delta": delta,
                })
            if cgpa_delta <= -1.0:
                anomaly_list.append({
                    "type": "CGPA Drop",
                    "branch": row["branch"],
                    "semester": row["semester_no"],
                    "description": f"Avg CGPA dropped {cgpa_delta:.2f} (sem {row['semester_no']-1}→{row['semester_no']})",
                    "severity": "high" if cgpa_delta <= -1.5 else "medium",
                    "cgpa_delta": cgpa_delta,
                    "delta": cgpa_delta,
                })
            if (row["avg_att"] or 100) < 60 and (prev[key].get("avg_att") or 100) >= 65:
                anomaly_list.append({
                    "type": "Attendance Crisis",
                    "branch": row["branch"],
                    "semester": row["semester_no"],
                    "description": f"Avg attendance fell below 60% in sem {row['semester_no']}",
                    "severity": "high",
                    "delta": (row["avg_att"] or 0) - (prev[key].get("avg_att") or 0),
                })
        prev[key] = {**row, "fail_rate": fail_rate}

    branches = dict_rows(db.execute(
        "SELECT DISTINCT branch_code, branch FROM students WHERE college_id=? ORDER BY branch",
        (u["college_id"],)).fetchall())

    ai_analysis = gemini_anomaly_detection(anomaly_list, sem_data) if anomaly_list else {
        "summary": "No anomalies detected in the current dataset.",
        "actions": ["Continue monitoring performance trends each semester."],
        "model_version": "rule-based"
    }
    sems = list(range(1, 9))
    col = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())
    return render_template("analytics/anomalies.html",
        anomaly_list=anomaly_list, sem_data=sem_data,
        branches=branches, sems=sems,
        branch_filter=branch_filter, sem_filter=sem_filter,
        ai_analysis=ai_analysis, col=col, user=u)


# ── Enhanced AI Report Generator ─────────────────────────────────────
@analytics_bp.route("/ai-report")
@login_required
@role_required("admin", "teacher")
def ai_report():
    u  = current_user()
    db = get_db()
    if not _require_college(u): return redirect(url_for("auth.login"))
    report_type = sanitize_text(request.args.get("type", "department"), 30)
    branch_filter = sanitize_alphanumeric(request.args.get("branch", ""), 10)
    sem_filter    = request.args.get("semester", "")
    sem_val = int(sem_filter) if sem_filter.isdigit() else None

    col = dict_row(db.execute("SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())
    q = """
        SELECT s.*, sr.cgpa, sr.sgpa, sr.attendance, sr.result,
               sr.backlog_count, sr.percentage, sr.semester_no as sem_no
        FROM students s
        LEFT JOIN semester_records sr ON s.id=sr.student_id 
           AND sr.semester_no = (CASE WHEN ? IS NOT NULL THEN ? ELSE s.current_semester END)
        WHERE s.college_id=?
    """
    params = [sem_val, sem_val, u["college_id"]]
    if branch_filter:
        q += " AND s.branch_code=?"; params.append(branch_filter)
    if sem_val:
        q += " AND sr.semester_no=?"; params.append(sem_val)
    students = dict_rows(db.execute(q, params).fetchall())

    branches = dict_rows(db.execute(
        "SELECT DISTINCT branch_code, branch FROM students WHERE college_id=? ORDER BY branch",
        (u["college_id"],)).fetchall())
    sems = list(range(1, 9))

    report = gemini_enhanced_report(students, col, report_type, branch_filter) if students else None
    return render_template("analytics/ai_report.html",
        report=report, report_type=report_type, col=col,
        students_count=len(students), branches=branches, sems=sems,
        branch_filter=branch_filter, sem_filter=sem_filter, user=u)


# ── Dataset Version Comparison ────────────────────────────────────────
@analytics_bp.route("/comparison")
@login_required
@role_required("admin", "teacher")
def comparison():
    import json as _json
    u  = current_user()
    db = get_db()
    if not _require_college(u):
        return redirect(url_for("auth.login"))

    versions = dict_rows(db.execute(
        "SELECT dv.*, u.full_name as uploaded_by_name "
        "FROM dataset_versions dv "
        "LEFT JOIN users u ON dv.uploaded_by=u.id "
        "WHERE dv.college_id=? AND dv.upload_type='results' AND dv.snapshot_json IS NOT NULL "
        "ORDER BY dv.version_no DESC LIMIT 10",
        (u["college_id"],)).fetchall())

    # Parse snapshot JSON for each version
    parsed_versions = []
    for v in versions:
        try:
            snap = _json.loads(v["snapshot_json"] or "{}")
            parsed_versions.append({
                "version_no":       v["version_no"],
                "id":               v["id"],
                "created_at":       v["created_at"],
                "row_count":        v["row_count"],
                "uploaded_by_name": v["uploaded_by_name"] or "Unknown",
                "stats":            snap.get("stats", {}),
                "students":         snap.get("students", []),
            })
        except Exception:
            continue

    # Build comparison data for charts — all versions sorted oldest→newest
    chart_versions = list(reversed(parsed_versions))

    # Per-student diff between latest two versions
    student_diff = []
    if len(parsed_versions) >= 2:
        new_v   = parsed_versions[0]
        old_v   = parsed_versions[1]
        old_map = {s["enrollment_no"]: s for s in old_v["students"]}
        new_map = {s["enrollment_no"]: s for s in new_v["students"]}
        all_enrs = set(old_map) | set(new_map)
        for enr in all_enrs:
            old_s = old_map.get(enr, {})
            new_s = new_map.get(enr, {})
            old_cgpa = float(old_s.get("cgpa") or 0)
            new_cgpa = float(new_s.get("cgpa") or 0)
            old_att  = float(old_s.get("attendance") or 0)
            new_att  = float(new_s.get("attendance") or 0)
            diff_cgpa = round(new_cgpa - old_cgpa, 2)
            diff_att  = round(new_att  - old_att,  1)
            student_diff.append({
                "enrollment_no": enr,
                "name":          new_s.get("name") or old_s.get("name", enr),
                "branch":        new_s.get("branch") or old_s.get("branch", ""),
                "old_cgpa":      old_cgpa,
                "new_cgpa":      new_cgpa,
                "diff_cgpa":     diff_cgpa,
                "old_att":       old_att,
                "new_att":       new_att,
                "diff_att":      diff_att,
                "old_result":    old_s.get("result", "—"),
                "new_result":    new_s.get("result", "—"),
                "status": ("improved"  if diff_cgpa >= 0.2 else
                           "declined"  if diff_cgpa <= -0.2 else "stable"),
            })
        student_diff.sort(key=lambda x: x["diff_cgpa"])  # worst first

    col = dict_row(db.execute(
        "SELECT * FROM colleges WHERE id=?", (u["college_id"],)).fetchone())

    return render_template("analytics/comparison.html",
        versions=parsed_versions,
        chart_versions=chart_versions,
        student_diff=student_diff,
        col=col, user=u)
