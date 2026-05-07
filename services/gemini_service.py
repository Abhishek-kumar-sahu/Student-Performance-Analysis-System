from datetime import datetime
from collections import Counter
import os, json, urllib.request as _urlreq

# ─── AI API endpoint ─────────────────────────────────────────────────────────
_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
    "/models/gemini-2.0-flash:generateContent?key={key}"
)


# ══════════════════════════════════════════════════════════════════════════════
# Low-level: single call to AI API
# ══════════════════════════════════════════════════════════════════════════════
def _call_ai(prompt: str, expect_json: bool = True, temperature: float = 0.3):
    """
    POST to AI API.
    Returns parsed dict (expect_json=True) or raw string, or None on any failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    sys_inst = (
        "You are SPAS — a Student Performance Analysis System AI. "
        "Respond ONLY with valid JSON when asked. "
        "No markdown code fences. No extra text outside the JSON object."
    ) if expect_json else (
        "You are SPAS — a Student Performance Analysis System AI advisor. "
        "Be helpful, encouraging, and specific. "
        "Use HTML <strong> tags to highlight key numbers. "
        "No markdown. Max 4-5 sentences unless listing recommendations."
    )

    payload = json.dumps({
        "system_instruction": {"parts": [{"text": sys_inst}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 1024,
        },
    }).encode()

    req = _urlreq.Request(
        _API_URL.format(key=api_key),
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _urlreq.urlopen(req, timeout=18) as resp:
            raw = json.loads(resp.read())
        text = raw["candidates"][0]["content"]["parts"][0]["text"].strip()
        # BUG-16 FIX: Strip markdown fences robustly.
        # The old rstrip("`") approach fails when the AI appends explanation text
        # after the closing fence (e.g. "```\nNote: this is why..."), causing
        # json.loads to fail and the entire response to be silently discarded.
        import re as _re
        if "```" in text:
            m = _re.search(r'```(?:json)?\s*([\s\S]*?)```', text, _re.DOTALL)
            if m:
                text = m.group(1).strip()
        # Secondary safety net: if the text still has leading/trailing prose,
        # extract the outermost JSON object or array.
        if expect_json and text and text[0] not in ('{', '['):
            m = _re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text, _re.DOTALL)
            if m:
                text = m.group(1).strip()
        return json.loads(text) if expect_json else text
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 1. STUDENT PERFORMANCE PREDICTION
# ══════════════════════════════════════════════════════════════════════════════
def gemini_predict(student_data: dict) -> dict:
    prompt = f"""Analyze this student's academic data and return a JSON prediction.

STUDENT DATA:
- Average Marks      : {student_data.get('avg_marks', 0):.1f}%
- Average Attendance : {student_data.get('avg_attendance', 0):.1f}%
- Current CGPA       : {student_data.get('previous_gpa', 0):.2f}
- Failed Subjects    : {student_data.get('failed_subjects', 0)}
- Current Semester   : {student_data.get('current_semester', student_data.get('semester', 1))}
- GPA Trend (vs prev): {student_data.get('gpa_trend', 0):+.2f}
- Programme / Branch : {student_data.get('programme', 'BE')} / {student_data.get('branch', 'Engineering')}

Return ONLY this JSON (no extra text):
{{
  "risk_level": "low|medium|high|critical",
  "risk_score": <integer 0-100>,
  "predicted_gpa": <float 0.00-10.00>,
  "risk_factors": ["factor1", "factor2", "factor3"],
  "recommendations": ["rec1", "rec2", "rec3", "rec4", "rec5"],
  "performance_summary": "<one sentence overview>"
}}

Risk guide:
  low (score 0-34)     → good marks, good attendance, no backlogs
  medium (score 35-54) → some concerns, minor issues
  high (score 55-74)   → multiple risk factors, needs intervention
  critical (score 75+) → severe risk, immediate action required"""

    result = _call_ai(prompt, expect_json=True)
    if result and isinstance(result, dict):
        result["risk_level"]      = str(result.get("risk_level", "medium")).lower()
        result["risk_score"]      = int(result.get("risk_score", 50))
        result["predicted_gpa"]   = round(float(result.get("predicted_gpa", 0.0)), 2)
        result["recommendations"] = (result.get("recommendations") or [])[:6]
        result["risk_factors"]    = (result.get("risk_factors") or [])[:5]
        result.pop("model_version", None)
        return result

    return _fallback_predict(student_data)


# ══════════════════════════════════════════════════════════════════════════════
# 2. ANALYTICS PAGE — DEEP INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════
def gemini_analytics_insight(
    student: dict,
    semesters: list,
    semester_no: int,
    rank: int,
    total: int,
) -> dict:
    sem_summary = [
        {
            "sem": s.get("semester_no"),
            "sgpa": s.get("sgpa", 0) or 0,
            "cgpa": s.get("cgpa", 0) or 0,
            "attendance": s.get("attendance", 0) or 0,
            "backlogs": s.get("backlog_count", 0) or 0,
            "result": s.get("result", ""),
        }
        for s in semesters
    ]
    cur_sem = next((s for s in semesters if s.get("semester_no") == semester_no), None)
    subjects_brief = [
        {
            "name": sub.get("subject_name", ""),
            "grade": sub.get("grade", ""),
            "marks": sub.get("total_marks", 0),
            "attendance": sub.get("attendance_pct", 0),
            "status": sub.get("status", ""),
        }
        for sub in (cur_sem.get("subjects") or [])
    ] if cur_sem else []

    prompt = f"""Analyze this student's complete academic record and return detailed JSON insights.

Student  : {student.get('name', 'Student')} | {student.get('programme','BE')} {student.get('branch','')}
Semester : {semester_no} | Rank: #{rank} of {total} students in branch

Semester History (all semesters):
{json.dumps(sem_summary, indent=2)}

Semester {semester_no} Subjects:
{json.dumps(subjects_brief, indent=2)}

Return ONLY this JSON:
{{
  "overall_trend": "improving|stable|declining",
  "trend_message": "<2-sentence analysis of academic trajectory>",
  "strengths": ["strength1", "strength2", "strength3"],
  "weaknesses": ["weakness1", "weakness2"],
  "subject_insights": [
    {{"subject": "name", "insight": "brief insight", "action": "recommended action"}}
  ],
  "semester_comparison": "<compare latest vs previous semester in 2 sentences>",
  "rank_analysis": "<1-sentence analysis of class rank and its implication>",
  "predicted_next_cgpa": <float 0-10>,
  "key_alerts": ["alert1", "alert2"],
  "motivation_message": "<1 encouraging sentence personalised to their data>",
  "study_plan": ["daily tip 1", "daily tip 2", "weekly goal"]
}}"""

    result = _call_ai(prompt, expect_json=True)
    if result and isinstance(result, dict):
        result.pop("model_version", None)
        return result
    return _fallback_analytics_insight(rank, total, semesters)


# ══════════════════════════════════════════════════════════════════════════════
# 3. DIGITAL TWIN — WHAT-IF SCENARIO SIMULATION
# ══════════════════════════════════════════════════════════════════════════════
def gemini_digital_twin(base_data: dict, scenario: dict) -> dict:
    """
    Simulate a what-if scenario entirely via AI.
    Returns baseline vs scenario comparison with action plan.
    """
    prompt = f"""Simulate a "digital twin" what-if academic scenario.

BASELINE (current reality):
- Average Marks      : {base_data.get('avg_marks', 0):.1f}%
- Average Attendance : {base_data.get('avg_attendance', 0):.1f}%
- Failed Subjects    : {base_data.get('failed_subjects', 0)}
- Current CGPA       : {base_data.get('previous_gpa', 0):.2f}
- Current Semester   : {base_data.get('current_semester', base_data.get('semester', 1))}

SCENARIO (what-if):
- New Attendance     : {scenario.get('avg_attendance', 75):.1f}%
- New Marks          : {scenario.get('avg_marks', 60):.1f}%
- New Failed Subjects: {scenario.get('failed_subjects', 0)}

Compute the impact. Return ONLY this JSON:
{{
  "baseline_gpa": <float 0-10>,
  "scenario_gpa": <float 0-10>,
  "baseline_risk": "low|medium|high|critical",
  "scenario_risk": "low|medium|high|critical",
  "verdict": "HIGHLY_BENEFICIAL|BENEFICIAL|NEUTRAL|HARMFUL",
  "verdict_reason": "<1 sentence explaining the verdict>",
  "changes": [
    {{"metric": "Attendance",     "baseline": "{base_data.get('avg_attendance',0):.1f}%", "scenario": "{scenario.get('avg_attendance',75):.1f}%", "direction": "up|down|same", "message": "impact in 1 sentence"}},
    {{"metric": "Marks",          "baseline": "{base_data.get('avg_marks',0):.1f}%",      "scenario": "{scenario.get('avg_marks',60):.1f}%",      "direction": "up|down|same", "message": "impact in 1 sentence"}},
    {{"metric": "Failed Subjects","baseline": "{base_data.get('failed_subjects',0)}",       "scenario": "{scenario.get('failed_subjects',0)}",       "direction": "up|down|same", "message": "impact in 1 sentence"}}
  ],
  "action_plan": ["concrete step 1", "concrete step 2", "concrete step 3"],
  "ai_advice": "<1-2 sentences of personalised advice to achieve the scenario>"
}}

Verdict guide:
  HIGHLY_BENEFICIAL : GPA improves >= 0.5 or risk drops significantly
  BENEFICIAL        : GPA improves or risk drops moderately
  NEUTRAL           : Change < 0.2 GPA diff
  HARMFUL           : GPA drops or risk increases"""

    result = _call_ai(prompt, expect_json=True)
    if result and isinstance(result, dict):
        result["baseline_gpa"] = round(float(result.get("baseline_gpa", 0)), 2)
        result["scenario_gpa"] = round(float(result.get("scenario_gpa", 0)), 2)
        result["scenario"]     = scenario
        result["baseline"]     = base_data
        result.pop("model_version", None)
        result.pop("gemini_advice", None)
        return result
    return _fallback_digital_twin(base_data, scenario)


# ══════════════════════════════════════════════════════════════════════════════
# 4. CONVERSATIONAL AI ADVISOR CHAT
# ══════════════════════════════════════════════════════════════════════════════
def gemini_chat(
    student: dict,
    student_data: dict,
    prediction: dict,
    question: str,
) -> str:
    """
    Answer a student's free-text question with full academic context via AI.
    Returns HTML-safe string.
    """
    name = (student.get("name") or "student").split()[0]
    prompt = f"""You are SPAS AI Advisor for a student named {name}.

STUDENT ACADEMIC PROFILE:
  Name         : {student.get('name', name)}
  Programme    : {student.get('programme','BE')} / {student.get('branch','Engineering')}
  Semester     : {student_data.get('current_semester', student.get('current_semester','?'))}
  Avg Marks    : {student_data.get('avg_marks', 0):.1f}%
  Attendance   : {student_data.get('avg_attendance', 0):.1f}%
  Failed Subjs : {student_data.get('failed_subjects', 0)}
  Current CGPA : {student_data.get('previous_gpa', 0):.2f}
  GPA Trend    : {student_data.get('gpa_trend', 0):+.2f}
  Predicted GPA: {prediction.get('predicted_gpa', 0)}
  Risk Level   : {prediction.get('risk_level', 'unknown')} (score {prediction.get('risk_score', 0)}/100)
  Risk Factors : {', '.join(prediction.get('risk_factors', []) or [])}
  Top Recs     : {', '.join((prediction.get('recommendations') or [])[:3])}
  Summary      : {prediction.get('performance_summary', '')}

STUDENT'S QUESTION: "{question}"

INSTRUCTIONS:
- Answer DIRECTLY and SPECIFICALLY about what the student asked.
- Always use the real numbers from their profile above in your answer.
- Use <strong> tags around key numbers and values.
- Be honest but encouraging. 3-5 sentences maximum.
- Do NOT give a generic response — address the exact question asked.
- No markdown, no bullet points unless listing recommendations."""

    result = _call_ai(prompt, expect_json=False, temperature=0.7)
    if result:
        return str(result)
    return _fallback_chat(name, student_data, prediction, question)


# ══════════════════════════════════════════════════════════════════════════════
# 5. CLASS-LEVEL INSIGHT (Teacher Dashboard)
# ══════════════════════════════════════════════════════════════════════════════
def gemini_class_insight(students: list, college_name: str = "", branch: str = "") -> dict:
    """
    AI-powered class-level analytics for the teacher dashboard.
    Accepts a list of student dicts with cgpa, attendance, result fields.
    Returns class insights, at-risk count breakdown, improvement tips.
    """
    if not students:
        return _fallback_class_insight(students, college_name, branch)

    # Build compact summary (avoid huge payloads)
    summary_rows = [
        {
            "name": s.get("name", ""),
            "semester": s.get("current_semester", 0),
            "cgpa": round(float(s.get("cgpa") or 0), 2),
            "attendance": round(float(s.get("attendance") or 0), 1),
            "result": s.get("result", ""),
            "backlogs": int(s.get("backlog_count") or 0),
        }
        for s in students[:60]   # cap at 60 to stay within token budget
    ]
    total = len(students)
    avg_cgpa = round(sum(float(s.get("cgpa") or 0) for s in students) / total, 2) if total else 0
    avg_att  = round(sum(float(s.get("attendance") or 0) for s in students) / total, 1) if total else 0
    passing  = sum(1 for s in students if str(s.get("result","")).upper() == "PASS")

    prompt = f"""You are analyzing an entire class for a teacher at {college_name or 'a college'}.
Branch: {branch or 'Mixed'} | Total Students: {total}
Class Average CGPA: {avg_cgpa} | Avg Attendance: {avg_att}% | Passing: {passing}/{total}

Student summary (up to 60):
{json.dumps(summary_rows, indent=2)}

Return ONLY this JSON:
{{
  "class_health": "excellent|good|average|poor|critical",
  "health_message": "<2-sentence overall class assessment>",
  "top_concerns": ["concern1", "concern2", "concern3"],
  "immediate_actions": ["action1", "action2", "action3"],
  "at_risk_count": <integer>,
  "critical_count": <integer>,
  "top_performers": ["name1", "name2", "name3"],
  "students_needing_intervention": ["name1", "name2", "name3", "name4", "name5"],
  "attendance_concern": true|false,
  "attendance_note": "<1 sentence about attendance situation>",
  "cgpa_trend_note": "<1 sentence about CGPA distribution>",
  "recommended_interventions": ["intervention1", "intervention2"],
  "motivation_for_teacher": "<1 encouraging sentence for the teacher>"
}}"""

    result = _call_ai(prompt, expect_json=True)
    if result and isinstance(result, dict):
        result.pop("model_version", None)
        return result
    return _fallback_class_insight(students, college_name, branch)


# ══════════════════════════════════════════════════════════════════════════════
# 6. BATCH AT-RISK IDENTIFICATION
# ══════════════════════════════════════════════════════════════════════════════
def gemini_at_risk_students(students: list) -> list:
    """
    Identify and rank the top at-risk students from the class list.
    Returns list of dicts: {enrollment_no, name, risk_level, reason}
    """
    if not students:
        return []

    rows = [
        {
            "enrollment_no": s.get("enrollment_no", ""),
            "name": s.get("name", ""),
            "cgpa": round(float(s.get("cgpa") or 0), 2),
            "attendance": round(float(s.get("attendance") or 0), 1),
            "backlogs": int(s.get("backlog_count") or 0),
            "result": s.get("result", ""),
        }
        for s in students[:50]
    ]

    prompt = f"""Identify the top at-risk students from this class list.

Students:
{json.dumps(rows, indent=2)}

Return ONLY a JSON array (no wrapper object) of the top 10 at-risk students, sorted by risk (highest first):
[
  {{
    "enrollment_no": "...",
    "name": "...",
    "risk_level": "critical|high|medium",
    "risk_score": <int 0-100>,
    "reason": "<1 sentence specific reason based on their data>"
  }}
]

Only include students with real risk (cgpa < 6, attendance < 70, or backlogs > 0).
If fewer than 10 qualify, include only those that qualify."""

    result = _call_ai(prompt, expect_json=True)
    if isinstance(result, list):
        return result[:10]

    # Rule-based fallback — never silently return empty
    at_risk = []
    for s in rows:
        cgpa = float(s.get("cgpa") or 0)
        att  = float(s.get("attendance") or 0)
        bl   = int(s.get("backlogs") or 0)
        if cgpa < 6.0 or att < 70 or bl > 0:
            if cgpa < 4.0 or att < 55 or bl >= 3:
                risk_level, risk_score = "critical", 85
            elif cgpa < 5.0 or att < 65 or bl >= 1:
                risk_level, risk_score = "high", 65
            else:
                risk_level, risk_score = "medium", 45
            reasons = []
            if cgpa < 6.0:   reasons.append(f"CGPA {cgpa:.2f}")
            if att < 70:     reasons.append(f"attendance {att:.1f}%")
            if bl > 0:       reasons.append(f"{bl} backlog(s)")
            at_risk.append({
                "enrollment_no": s.get("enrollment_no", ""),
                "name": s.get("name", ""),
                "risk_level": risk_level,
                "risk_score": risk_score,
                "reason": "Student at risk due to: " + ", ".join(reasons) + ".",
            })
    at_risk.sort(key=lambda x: x["risk_score"], reverse=True)
    return at_risk[:10]


# ══════════════════════════════════════════════════════════════════════════════
# DB HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def get_student_prediction_data(db, student_id: int, semester_no: int = None) -> dict:
    """Build prediction input dict from SQLite — no ML, pure data retrieval."""
    student = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if not student:
        return {}

    sem = semester_no or student["current_semester"]
    sr  = db.execute(
        "SELECT * FROM semester_records WHERE student_id=? AND semester_no=?",
        (student_id, sem),
    ).fetchone()

    prog   = student["programme"] if "programme" in student.keys() else "BE"
    branch = student["branch"]    if "branch"    in student.keys() else ""

    if not sr:
        return {
            "avg_marks": 0, "avg_attendance": 0, "previous_gpa": 0,
            "failed_subjects": 0, "current_semester": sem, "semester": sem,
            "gpa_trend": 0, "programme": prog, "branch": branch,
        }

    subjects = db.execute(
        "SELECT * FROM subject_marks WHERE semester_record_id=?", (sr["id"],)
    ).fetchall()
    pcts = [
        s["total_marks"] / s["max_marks"] * 100
        for s in subjects
        if s["max_marks"] and s["max_marks"] > 0
    ]
    avg_marks = round(sum(pcts) / len(pcts), 2) if pcts else (sr["percentage"] or 0)
    avg_att   = sr["attendance"] or 75
    failed    = sr["backlog_count"] or sum(1 for s in subjects if s["status"] == "FAIL")

    prev_sr   = db.execute(
        "SELECT cgpa FROM semester_records WHERE student_id=? AND semester_no=?",
        (student_id, sem - 1),
    ).fetchone()
    prev_gpa  = sr["cgpa"] or 0
    gpa_trend = (sr["cgpa"] or 0) - (prev_sr["cgpa"] if prev_sr else 0)

    return {
        "avg_marks": avg_marks,
        "avg_attendance": avg_att,
        "previous_gpa": prev_gpa,
        "failed_subjects": failed,
        "current_semester": sem,
        "semester": sem,
        "gpa_trend": gpa_trend,
        "programme": prog,
        "branch": branch,
    }


def save_prediction(db, student_id: int, semester_no: int, prediction: dict):
    """Persist prediction to ml_predictions table."""
    db.execute(
        """INSERT OR REPLACE INTO ml_predictions
           (student_id, semester_no, risk_level, risk_score, predicted_gpa,
            current_gpa, risk_factors, recommendations, model_version)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            student_id, semester_no,
            prediction.get("risk_level", "low"),
            prediction.get("risk_score", 0),
            prediction.get("predicted_gpa", 0),
            prediction.get("current_gpa", prediction.get("previous_gpa", 0)),
            json.dumps(prediction.get("risk_factors", [])),
            json.dumps(prediction.get("recommendations", [])),
            "spas-ai",
        ),
    )
    db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# FALLBACKS  (used when API key is not set or call fails)
# ══════════════════════════════════════════════════════════════════════════════
def _fallback_predict(d: dict) -> dict:
    att    = d.get("avg_attendance", 0)
    marks  = d.get("avg_marks", 0)
    pgpa   = d.get("previous_gpa", 0)
    failed = d.get("failed_subjects", 0)
    trend  = d.get("gpa_trend", 0)
    score  = 0; factors = []; recs = []

    if att < 60:     score += 30; factors.append("Critically low attendance (<60%)")
    elif att < 75:   score += 18; factors.append("Attendance below 75% minimum")
    if marks < 40:   score += 30; factors.append("Marks below passing threshold")
    elif marks < 55: score += 16; factors.append("Below-average marks")
    if failed > 2:   score += 24; factors.append(f"{failed} failed subjects (backlogs)")
    elif failed:     score += 10; factors.append(f"{failed} failed subject")
    if trend < -0.5: score += 14; factors.append("Declining GPA trend")

    score = min(100, score)
    risk = ("critical" if score >= 75 else "high" if score >= 55
            else "medium" if score >= 35 else "low")

    if att < 75:   recs.append("Attend every remaining class to cross 75%")
    if marks < 55: recs.append("Schedule dedicated revision for weak subjects daily")
    if failed:     recs.append("Clear backlogs immediately — consult faculty this week")
    if trend < 0:  recs.append("Review your study strategy and use spaced repetition")
    recs += ["Solve previous year question papers", "Join or form a study group"]

    pred_gpa = round(max(0, min(10, (marks / 10) * 0.55 + pgpa * 0.3 + (att / 100) * 2.0)), 2)
    return {
        "risk_level": risk,
        "risk_score": score,
        "predicted_gpa": pred_gpa,
        "current_gpa": pgpa,
        "risk_factors": factors[:5],
        "recommendations": recs[:6],
        "performance_summary": f"Risk level: {risk.upper()}. Predicted GPA: {pred_gpa}.",
    }


def _fallback_analytics_insight(rank, total, semesters):
    trend = "stable"
    if len(semesters) >= 2:
        diff = (semesters[-1].get("sgpa", 0) or 0) - (semesters[-2].get("sgpa", 0) or 0)
        trend = "improving" if diff > 0.1 else "declining" if diff < -0.1 else "stable"
    return {
        "overall_trend": trend,
        "trend_message": "Performance analysis is based on your recorded academic data. Keep working consistently each semester.",
        "strengths": ["Consistent performance"],
        "weaknesses": ["Some areas need review"],
        "subject_insights": [],
        "semester_comparison": "Review your semester-wise results to identify patterns.",
        "rank_analysis": f"You are ranked #{rank} out of {total} students in your branch.",
        "predicted_next_cgpa": round((semesters[-1].get("cgpa", 0) or 0), 2) if semesters else 0,
        "key_alerts": [],
        "motivation_message": "Consistent effort and regular attendance are the keys to success!",
        "study_plan": ["Review lecture notes daily", "Solve 3 practice problems per subject weekly"],
    }


def _fallback_digital_twin(base_data, scenario):
    def _gpa(d):
        return round(max(0.0, min(10.0,
            (d.get("avg_marks", 0) / 10.0) * 0.55
            + d.get("previous_gpa", 0) * 0.3
            + (d.get("avg_attendance", 0) / 100.0) * 2.0
        )), 2)

    bg   = _gpa(base_data)
    sg   = _gpa({**base_data, **scenario})
    diff = round(sg - bg, 2)

    verdict = (
        "HIGHLY_BENEFICIAL" if diff >= 0.5
        else "BENEFICIAL"   if diff >= 0.2
        else "HARMFUL"      if diff <= -0.2
        else "NEUTRAL"
    )
    return {
        "baseline_gpa": bg,
        "scenario_gpa": sg,
        "baseline_risk": "medium",
        "scenario_risk": "medium",
        "verdict": verdict,
        "verdict_reason": f"GPA would change by {diff:+.2f} under the scenario.",
        "changes": [
            {"metric": "Attendance",      "direction": "up" if scenario.get("avg_attendance", 0) > base_data.get("avg_attendance", 0) else "down", "message": "Attendance change impact"},
            {"metric": "Marks",           "direction": "up" if scenario.get("avg_marks", 0) > base_data.get("avg_marks", 0) else "down", "message": "Marks change impact"},
            {"metric": "Failed Subjects", "direction": "down" if scenario.get("failed_subjects", 0) < base_data.get("failed_subjects", 0) else "up", "message": "Backlog change impact"},
        ],
        "action_plan": [
            "Attend all classes to improve attendance",
            "Focus revision on failed/weak subjects",
        ],
        "ai_advice": "Improving attendance and reducing backlogs are the fastest ways to boost your GPA.",
        "scenario": scenario,
        "baseline": base_data,
    }


def _fallback_chat(name, student_data, prediction, question):
    att    = student_data.get("avg_attendance", 0)
    marks  = student_data.get("avg_marks", 0)
    pgpa   = prediction.get("predicted_gpa", 0)
    risk   = prediction.get("risk_level", "unknown")
    recs   = prediction.get("recommendations", [])
    failed = student_data.get("failed_subjects", 0)
    sem    = student_data.get("current_semester", "?")
    cgpa   = student_data.get("previous_gpa", 0)
    trend  = student_data.get("gpa_trend", 0)
    score  = prediction.get("risk_score", 0)
    factors= prediction.get("risk_factors", [])
    q      = question.lower()

    # GPA / grade / CGPA
    if any(w in q for w in ["gpa", "cgpa", "sgpa", "grade point", "predict", "forecast", "expected gpa", "future gpa"]):
        trend_txt = (f"Your GPA is <strong>improving</strong> (+{trend:.2f})" if trend > 0.1
                     else f"Your GPA is <strong>declining</strong> ({trend:.2f})" if trend < -0.1
                     else "Your GPA is <strong>stable</strong>")
        return (f"<strong>{name}</strong>, your current CGPA is <strong>{cgpa:.2f}</strong> and your predicted GPA is <strong>{pgpa}</strong>. "
                f"{trend_txt} compared to last semester. "
                f"{'Great trajectory — keep up the consistency! 🏆' if risk == 'low' else 'Focus on the recommendations to push your GPA higher.'}")

    # Attendance
    if any(w in q for w in ["attend", "absent", "class", "lecture", "present", "bunk", "miss", "shortage"]):
        status = "⚠️ <strong style='color:var(--red);'>BELOW the 75% minimum</strong> — attend every remaining class urgently!" if att < 75 else "✅ <strong style='color:var(--green);'>Above the 75% threshold</strong> — keep it up!"
        return (f"Your current attendance is <strong>{att:.1f}%</strong>. "
                f"{status} "
                f"{'Low attendance is one of your key risk factors.' if att < 75 and factors else ''}")

    # Marks / scores / exam
    if any(w in q for w in ["mark", "score", "exam", "test", "percentage", "percent", "paper", "internal", "external", "subject mark"]):
        return (f"Your average marks score is <strong>{marks:.1f}%</strong> this semester. "
                f"{'⚠️ This is below the 40% passing threshold — seek faculty help immediately.' if marks < 40 else 'Your weakest subjects need focused revision to bring up your overall average.'}")

    # Backlog / failed subjects
    if any(w in q for w in ["backlog", "fail", "arrear", "clear", "ktc", "back paper", "failed subject", "pending"]):
        if failed > 0:
            return (f"You currently have <strong style='color:var(--red);'>{failed} failed subject(s)</strong>. "
                    f"Clearing backlogs should be your <strong>top priority</strong> as they directly impact your CGPA and eligibility. "
                    f"Contact your faculty and register for supplementary exams immediately.")
        return (f"✅ <strong>{name}</strong>, you have <strong>no active backlogs</strong>. Keep it that way by staying on top of your studies!")

    # Risk / danger / chance of failing
    if any(w in q for w in ["risk", "danger", "chance", "probability", "safe", "worry", "concern", "in trouble"]):
        c = {"low": "var(--green)", "medium": "var(--yellow)", "high": "var(--red)", "critical": "var(--red)"}.get(risk, "var(--text)")
        factors_txt = (f" Key risk factors: <em>{', '.join(factors[:3])}</em>." if factors else "")
        return (f"Your risk level is <strong style='color:{c};'>{risk.upper()}</strong> (score: <strong>{score}/100</strong>).{factors_txt} "
                f"{'No major concerns — maintain your good habits.' if risk == 'low' else 'Please review your recommendations on the analytics page and speak to your teacher.'}")

    # Recommendations / advice / tips / study plan
    if any(w in q for w in ["recommend", "improve", "help", "advice", "tip", "suggest", "plan", "strategy", "how to", "what should", "what can", "how can", "study"]):
        if recs:
            return ("Here are your personalised recommendations:<br><ul>"
                    + "".join(f"<li>{r}</li>" for r in recs)
                    + "</ul>")
        return "Keep attending all classes, revise regularly, and consult your teachers about weak areas."

    # Semester info
    if any(w in q for w in ["semester", "current sem", "which sem", "sem number"]):
        return (f"You are currently in <strong>Semester {sem}</strong>. "
                f"Your CGPA stands at <strong>{cgpa:.2f}</strong> and your predicted GPA for this semester is <strong>{pgpa}</strong>.")

    # General greeting / intro
    if any(w in q for w in ["hi", "hello", "hey", "who are you", "what can you do", "help me", "what do you know"]):
        return (f"Hello <strong>{name}</strong>! 👋 I'm your SPAS AI Advisor. "
                f"I have full access to your academic profile — your CGPA is <strong>{cgpa:.2f}</strong>, "
                f"attendance is <strong>{att:.1f}%</strong>, and your risk level is <strong>{risk.upper()}</strong>. "
                f"Ask me anything about your GPA, attendance, backlogs, risk level, or study recommendations!")

    # Default — always give something useful, not just a generic message
    return (f"<strong>{name}</strong>, based on your profile: CGPA <strong>{cgpa:.2f}</strong>, "
            f"attendance <strong>{att:.1f}%</strong>, risk level <strong>{risk.upper()}</strong>, "
            f"predicted GPA <strong>{pgpa}</strong>. "
            f"You can ask me about your GPA, attendance, backlogs, risk score, or study recommendations!")


def _fallback_class_insight(students, college_name, branch):
    total    = len(students)
    avg_cgpa = round(sum(float(s.get("cgpa") or 0) for s in students) / total, 2) if total else 0
    avg_att  = round(sum(float(s.get("attendance") or 0) for s in students) / total, 1) if total else 0
    at_risk  = sum(1 for s in students if (float(s.get("cgpa") or 0) < 5.0 or float(s.get("attendance") or 0) < 60))
    critical = sum(1 for s in students if float(s.get("cgpa") or 0) < 4.0)

    health = (
        "critical" if avg_cgpa < 4.0 else
        "poor"     if avg_cgpa < 5.5 else
        "average"  if avg_cgpa < 7.0 else
        "good"     if avg_cgpa < 8.5 else
        "excellent"
    )
    return {
        "class_health": health,
        "health_message": f"Class average CGPA is {avg_cgpa} with {avg_att}% average attendance.",
        "top_concerns": (
            (["Low class attendance (<60%)"] if avg_att < 60 else []) +
            (["Multiple students below 5.0 CGPA"] if critical > 0 else []) +
            (["High at-risk student count"] if at_risk > total * 0.3 else [])
        ) or ["No immediate critical concerns"],
        "immediate_actions": [
            "Review attendance records weekly",
            "Schedule one-on-one sessions with at-risk students",
        ],
        "at_risk_count": at_risk,
        "critical_count": critical,
        "top_performers": [],
        "students_needing_intervention": [],
        "attendance_concern": avg_att < 75,
        "attendance_note": f"Class average attendance is {avg_att}%.",
        "cgpa_trend_note": f"Class average CGPA is {avg_cgpa}.",
        "recommended_interventions": ["Personal counselling sessions", "Supplementary classes for weak subjects"],
        "motivation_for_teacher": "Your dedication is making a difference — keep tracking and engaging with students!",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 7. SUBJECT DIFFICULTY RANKING
# ══════════════════════════════════════════════════════════════════════════════
def gemini_subject_difficulty(subjects: list) -> dict:
    if not subjects:
        return {"insights": [], "hardest": [], "easiest": [], "summary": "No subject data available."}
    top_hard = subjects[:10]
    prompt = f"""Analyze these subjects by failure rate and provide difficulty insights.

Subjects (sorted by failure rate desc):
{json.dumps([{"name": s["subject_name"], "fail_rate": s["fail_rate"], "avg_marks": round(s.get("avg_marks") or 0, 1), "attempts": s["total_attempts"]} for s in top_hard], indent=2)}

Return ONLY this JSON:
{{
  "summary": "<2-sentence overall assessment of subject difficulty distribution>",
  "hardest_subjects": [
    {{"subject": "name", "reason": "why it's hard", "intervention": "suggested intervention"}}
  ],
  "easiest_subjects": [
    {{"subject": "name", "note": "observation"}}
  ],
  "curriculum_insights": ["insight1", "insight2", "insight3"],
  "recommended_actions": ["action for faculty", "action for students", "action for admin"]
}}"""
    result = _call_ai(prompt, expect_json=True)
    if result and isinstance(result, dict):
        result.pop("model_version", None)
        return result
    hardest = [s["subject_name"] for s in subjects[:3]]
    return {
        "summary": f"Top difficult subjects: {', '.join(hardest)}.",
        "hardest_subjects": [{"subject": s["subject_name"], "reason": f"{s['fail_rate']}% failure rate", "intervention": "Additional tutorials recommended"} for s in subjects[:3]],
        "easiest_subjects": [{"subject": s["subject_name"], "note": "Low failure rate"} for s in subjects[-3:]],
        "curriculum_insights": ["High failure rate subjects need curriculum review"],
        "recommended_actions": ["Schedule extra sessions for high-failure subjects"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 8. ATTENDANCE-PERFORMANCE CORRELATION
# ══════════════════════════════════════════════════════════════════════════════
def gemini_attendance_correlation(students: list, bucket_stats: dict) -> dict:
    if not students:
        return {"correlation": "No data available", "insights": []}
    prompt = f"""Analyze the correlation between attendance and academic performance.

Attendance Buckets with Average CGPA:
{json.dumps(bucket_stats, indent=2)}

Total students: {len(students)}

Return ONLY this JSON:
{{
  "correlation_strength": "strong|moderate|weak",
  "correlation_direction": "positive|negative|none",
  "key_insight": "<1-2 sentence key finding about attendance vs performance>",
  "critical_threshold": "<attendance % where performance drops significantly>",
  "insights": ["insight1", "insight2", "insight3"],
  "recommendations": ["rec1", "rec2", "rec3"],
  "attendance_policy_suggestion": "<1 sentence policy recommendation>"
}}"""
    result = _call_ai(prompt, expect_json=True)
    if result and isinstance(result, dict):
        result.pop("model_version", None)
        return result
    low_att  = bucket_stats.get("<60", {})
    high_att = bucket_stats.get("85-100", {})
    cgpa_diff = round((high_att.get("avg_cgpa", 0) - low_att.get("avg_cgpa", 0)), 2)
    return {
        "correlation_strength": "strong" if cgpa_diff > 1.5 else "moderate",
        "correlation_direction": "positive",
        "key_insight": f"Students with >85% attendance average {high_att.get('avg_cgpa',0):.2f} CGPA vs {low_att.get('avg_cgpa',0):.2f} for <60% attendance.",
        "critical_threshold": "75%",
        "insights": [f"CGPA difference between high/low attendance: {cgpa_diff}", "Attendance strongly predicts academic success"],
        "recommendations": ["Monitor students with attendance below 75% weekly", "Mandatory counselling for <60% attendance"],
        "attendance_policy_suggestion": "Enforce strict 75% minimum attendance policy with early warning system.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 9. NATURAL LANGUAGE QUERY FOR DASHBOARDS
# ══════════════════════════════════════════════════════════════════════════════
def gemini_nl_query(question: str, students: list, college: dict) -> str:
    """
    Answer a free-text analytics question about the college using real student data.
    Builds rich context — branch breakdown, top/bottom performers, backlog stats —
    then falls back to a detailed rule-based engine if the AI call fails.
    """
    if not students:
        return "No student data is available yet. Please upload results data first."

    total    = len(students)
    col_name = (college.get("name") or "the college") if college else "the college"

    # ── Compute all stats upfront ────────────────────────────────────
    avg_cgpa  = round(sum(float(s.get("cgpa") or 0) for s in students) / total, 2)
    avg_att   = round(sum(float(s.get("attendance") or 0) for s in students) / total, 1)
    passing   = sum(1 for s in students if str(s.get("result","")).upper() == "PASS")
    failing   = total - passing
    pass_rate = round(passing / total * 100, 1)
    backlogs  = sum(1 for s in students if int(s.get("backlog_count") or 0) > 0)
    low_att   = sum(1 for s in students if float(s.get("attendance") or 0) < 60)
    att_warn  = sum(1 for s in students if 60 <= float(s.get("attendance") or 0) < 75)
    at_risk   = sum(1 for s in students if float(s.get("cgpa") or 0) < 5.0 or float(s.get("attendance") or 0) < 60)
    cgpa_below5 = sum(1 for s in students if float(s.get("cgpa") or 0) < 5.0)

    # Branch-wise breakdown
    branch_map = {}
    for s in students:
        b = s.get("branch") or "Unknown"
        if b not in branch_map:
            branch_map[b] = {"count": 0, "cgpa_sum": 0, "att_sum": 0, "pass": 0, "at_risk": 0}
        branch_map[b]["count"]    += 1
        branch_map[b]["cgpa_sum"] += float(s.get("cgpa") or 0)
        branch_map[b]["att_sum"]  += float(s.get("attendance") or 0)
        branch_map[b]["pass"]     += 1 if str(s.get("result","")).upper() == "PASS" else 0
        branch_map[b]["at_risk"]  += 1 if (float(s.get("cgpa") or 0) < 5.0 or float(s.get("attendance") or 0) < 60) else 0

    branch_stats = {}
    for b, v in branch_map.items():
        c = v["count"]
        branch_stats[b] = {
            "count":      c,
            "avg_cgpa":   round(v["cgpa_sum"] / c, 2) if c else 0,
            "avg_att":    round(v["att_sum"]  / c, 1) if c else 0,
            "pass_rate":  round(v["pass"] / c * 100, 1) if c else 0,
            "at_risk":    v["at_risk"],
        }

    # Top & bottom performers
    sorted_by_cgpa = sorted(students, key=lambda s: float(s.get("cgpa") or 0), reverse=True)
    top5    = [{"name": s.get("name",""), "cgpa": s.get("cgpa",0), "branch": s.get("branch","")} for s in sorted_by_cgpa[:5]]
    bottom5 = [{"name": s.get("name",""), "cgpa": s.get("cgpa",0), "branch": s.get("branch","")} for s in sorted_by_cgpa[-5:]]
    sorted_by_att = sorted(students, key=lambda s: float(s.get("attendance") or 0))
    lowest_att5 = [{"name": s.get("name",""), "attendance": s.get("attendance",0), "branch": s.get("branch","")} for s in sorted_by_att[:5]]

    prompt = f"""You are SPAS AI — an academic analytics assistant for {col_name}.
Answer the question DIRECTLY and SPECIFICALLY using the real data provided below.
Use <strong> tags around key numbers. Be concise but complete. No markdown, no bullet symbols.

=== COLLEGE ANALYTICS DATA ===
Total Students  : {total}
Average CGPA    : {avg_cgpa}
Average Attend. : {avg_att}%
Pass Rate       : {pass_rate}% ({passing} passing, {failing} failing)
At Risk (CGPA<5 or Att<60%) : {at_risk} students
CGPA Below 5.0  : {cgpa_below5} students
With Backlogs   : {backlogs} students
Attendance <60% : {low_att} students
Attendance 60–75%: {att_warn} students

Branch-wise Stats:
{json.dumps(branch_stats, indent=2)}

Top 5 Performers (by CGPA):
{json.dumps(top5, indent=2)}

Bottom 5 Performers (by CGPA):
{json.dumps(bottom5, indent=2)}

Lowest Attendance Students:
{json.dumps(lowest_att5, indent=2)}

Full student list (name, branch, CGPA, attendance, result, backlogs):
{json.dumps([{"name": s.get("name",""), "branch": s.get("branch",""), "cgpa": round(float(s.get("cgpa") or 0),2), "attendance": round(float(s.get("attendance") or 0),1), "result": s.get("result",""), "backlogs": int(s.get("backlog_count") or 0)} for s in students], indent=2)}

=== QUESTION ===
"{question}"

INSTRUCTIONS:
- Answer the EXACT question asked. Do not give a generic college summary.
- If the question asks "who" — name specific students.
- If the question asks "how many" — give the exact count.
- If the question asks "which branch" — compare branches and name the best/worst.
- If the question is unclear or not about academics, say so politely.
- Use <strong> for all key numbers and names. Plain HTML only."""

    result = _call_ai(prompt, expect_json=False, temperature=0.3)
    if result:
        return str(result)

    # ── Rich rule-based fallback ─────────────────────────────────────
    return _fallback_nl_query(question, total, avg_cgpa, avg_att, pass_rate,
                               passing, failing, at_risk, cgpa_below5, backlogs,
                               low_att, att_warn, branch_stats, top5, bottom5,
                               lowest_att5, students)


def _fallback_nl_query(question, total, avg_cgpa, avg_att, pass_rate,
                        passing, failing, at_risk, cgpa_below5, backlogs,
                        low_att, att_warn, branch_stats, top5, bottom5,
                        lowest_att5, students):
    """Rich keyword-based fallback when AI API is unavailable."""
    q = question.lower().strip()

    # Top performers
    if any(w in q for w in ["top", "best", "highest cgpa", "topper", "rank 1", "first rank"]):
        rows = "".join(f"<li><strong>{s['name']}</strong> — CGPA <strong>{s['cgpa']}</strong> ({s['branch']})</li>" for s in top5)
        return f"Top 5 performing students by CGPA:<ul>{rows}</ul>"

    # Bottom performers
    if any(w in q for w in ["bottom", "worst", "lowest cgpa", "poor perform", "weakest"]):
        rows = "".join(f"<li><strong>{s['name']}</strong> — CGPA <strong>{s['cgpa']}</strong> ({s['branch']})</li>" for s in bottom5)
        return f"Bottom 5 students by CGPA:<ul>{rows}</ul>"

    # Lowest attendance
    if any(w in q for w in ["lowest attendance", "least attendance", "absent", "who has lowest"]):
        rows = "".join(f"<li><strong>{s['name']}</strong> — <strong>{s['attendance']}%</strong> ({s['branch']})</li>" for s in lowest_att5)
        return f"Students with lowest attendance:<ul>{rows}</ul>"

    # CGPA below a threshold
    if any(w in q for w in ["below 5", "cgpa below", "cgpa less", "low cgpa", "cgpa under"]):
        return (f"<strong>{cgpa_below5}</strong> out of <strong>{total}</strong> students "
                f"have a CGPA below <strong>5.0</strong> "
                f"({round(cgpa_below5/total*100,1) if total else 0}% of the class).")

    # CGPA / GPA general
    if any(w in q for w in ["cgpa", "gpa", "average grade", "grade point"]):
        best_b  = max(branch_stats, key=lambda b: branch_stats[b]["avg_cgpa"]) if branch_stats else "—"
        worst_b = min(branch_stats, key=lambda b: branch_stats[b]["avg_cgpa"]) if branch_stats else "—"
        return (f"The college average CGPA is <strong>{avg_cgpa}</strong> across <strong>{total}</strong> students. "
                f"Highest branch: <strong>{best_b}</strong> ({branch_stats[best_b]['avg_cgpa'] if best_b != '—' else '—'}), "
                f"Lowest branch: <strong>{worst_b}</strong> ({branch_stats[worst_b]['avg_cgpa'] if worst_b != '—' else '—'}).")

    # Attendance — branch comparison
    if "which branch" in q and any(w in q for w in ["attend", "present"]):
        best_b  = max(branch_stats, key=lambda b: branch_stats[b]["avg_att"]) if branch_stats else "—"
        worst_b = min(branch_stats, key=lambda b: branch_stats[b]["avg_att"]) if branch_stats else "—"
        return (f"<strong>{best_b}</strong> has the highest average attendance at "
                f"<strong>{branch_stats[best_b]['avg_att'] if best_b != '—' else '—'}%</strong>. "
                f"<strong>{worst_b}</strong> has the lowest at "
                f"<strong>{branch_stats[worst_b]['avg_att'] if worst_b != '—' else '—'}%</strong>.")

    # Attendance general
    if any(w in q for w in ["attend", "present", "absent"]):
        return (f"Average attendance is <strong>{avg_att}%</strong> across <strong>{total}</strong> students. "
                f"<strong>{low_att}</strong> students have attendance below 60%, "
                f"and <strong>{att_warn}</strong> are in the warning zone (60–75%).")

    # At-risk / which branch most at-risk
    if "which branch" in q and any(w in q for w in ["risk", "fail", "most"]):
        most_risk_b = max(branch_stats, key=lambda b: branch_stats[b]["at_risk"]) if branch_stats else "—"
        return (f"<strong>{most_risk_b}</strong> has the most at-risk students: "
                f"<strong>{branch_stats[most_risk_b]['at_risk'] if most_risk_b != '—' else '—'}</strong> "
                f"out of {branch_stats[most_risk_b]['count'] if most_risk_b != '—' else '—'} in that branch.")

    # At-risk count
    if any(w in q for w in ["at risk", "at-risk", "danger", "risk of fail"]):
        return (f"<strong>{at_risk}</strong> students ({round(at_risk/total*100,1) if total else 0}%) "
                f"are at risk — having CGPA below 5.0 or attendance below 60%.")

    # Backlogs
    if any(w in q for w in ["backlog", "arrear", "back paper", "fail subject", "pending"]):
        bl_students = [s for s in students if int(s.get("backlog_count") or 0) > 0]
        names = ", ".join(f"<strong>{s.get('name','')}</strong>" for s in bl_students[:5])
        more = f" and {len(bl_students)-5} more" if len(bl_students) > 5 else ""
        return (f"<strong>{backlogs}</strong> students have active backlogs. "
                + (f"They include: {names}{more}." if bl_students else ""))

    # Pass rate
    if any(w in q for w in ["pass rate", "pass%", "how many pass", "passing", "overall pass"]):
        return (f"The overall pass rate is <strong>{pass_rate}%</strong> — "
                f"<strong>{passing}</strong> students passed and <strong>{failing}</strong> failed "
                f"out of <strong>{total}</strong> total students.")

    # Branch comparison / which branch best
    if any(w in q for w in ["which branch", "branch perform", "branch cgpa", "branch comparison", "compare branch"]):
        rows = "".join(
            f"<li><strong>{b}</strong> — Avg CGPA: <strong>{v['avg_cgpa']}</strong>, "
            f"Attendance: {v['avg_att']}%, Pass: {v['pass_rate']}%, At-risk: {v['at_risk']}</li>"
            for b, v in sorted(branch_stats.items(), key=lambda x: x[1]["avg_cgpa"], reverse=True)
        )
        return f"Branch-wise performance (sorted by CGPA):<ul>{rows}</ul>"

    # Count / how many students
    if any(w in q for w in ["how many student", "total student", "student count", "number of student"]):
        return (f"There are <strong>{total}</strong> students in total. "
                f"<strong>{passing}</strong> are passing and <strong>{failing}</strong> are failing. "
                f"<strong>{at_risk}</strong> students are flagged as at-risk.")

    # Semester / performance summary
    if any(w in q for w in ["summary", "overview", "report", "performance"]):
        return (f"College summary: <strong>{total}</strong> students, avg CGPA <strong>{avg_cgpa}</strong>, "
                f"avg attendance <strong>{avg_att}%</strong>, pass rate <strong>{pass_rate}%</strong>, "
                f"<strong>{at_risk}</strong> at-risk, <strong>{backlogs}</strong> with backlogs.")

    # Default — still give useful data
    return (f"Based on the data: <strong>{total}</strong> students, average CGPA <strong>{avg_cgpa}</strong>, "
            f"average attendance <strong>{avg_att}%</strong>, pass rate <strong>{pass_rate}%</strong>, "
            f"<strong>{at_risk}</strong> at-risk students. "
            f"Try asking about top performers, branch comparisons, attendance, backlogs, or pass rates.")


# ══════════════════════════════════════════════════════════════════════════════
# 10. MULTI-COLLEGE COMPARISON
# ══════════════════════════════════════════════════════════════════════════════
def gemini_multi_college(college_stats: list) -> dict:
    if not college_stats:
        return {"insights": [], "best_college": "", "needs_support": ""}
    prompt = f"""Compare these colleges academically and provide insights.

College Performance Data:
{json.dumps([{"name": c["name"], "total_students": c["total"], "avg_cgpa": c["avg_cgpa"], "avg_attendance": c["avg_att"], "pass_rate": c["pass_rate"], "at_risk_pct": c.get("at_risk_pct",0), "teachers": c["teachers"]} for c in college_stats], indent=2)}

Return ONLY this JSON:
{{
  "best_performing": "<college name>",
  "needs_support": "<college name>",
  "network_avg_cgpa": <float>,
  "insights": ["insight1", "insight2", "insight3"],
  "standout_observations": ["obs1", "obs2"],
  "recommendations": [
    {{"college": "name", "action": "specific recommendation"}}
  ],
  "benchmark_targets": {{"cgpa": <float>, "attendance": <float>, "pass_rate": <float>}},
  "summary": "<2-sentence network-wide assessment>"
}}"""
    result = _call_ai(prompt, expect_json=True)
    if result and isinstance(result, dict):
        result.pop("model_version", None)
        return result
    best  = max(college_stats, key=lambda c: c["avg_cgpa"]) if college_stats else {}
    worst = min(college_stats, key=lambda c: c["avg_cgpa"]) if college_stats else {}
    return {
        "best_performing": best.get("name", ""),
        "needs_support": worst.get("name", ""),
        "network_avg_cgpa": round(sum(c["avg_cgpa"] for c in college_stats) / len(college_stats), 2),
        "insights": [],
        "standout_observations": [],
        "recommendations": [],
        "benchmark_targets": {"cgpa": 7.0, "attendance": 75.0, "pass_rate": 85.0},
        "summary": f"Network has {len(college_stats)} colleges. Best performer: {best.get('name','')}.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 11. LONG-TERM TREND ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
def gemini_trend_analysis(trend_data: list, branch: str = "") -> dict:
    if not trend_data:
        return {"trend": "No data", "insights": []}
    prompt = f"""Analyze long-term academic trends across semesters{' for branch: '+branch if branch else ''}.

Semester-wise Data:
{json.dumps(trend_data, indent=2)}

Return ONLY this JSON:
{{
  "overall_trajectory": "improving|stable|declining|mixed",
  "trajectory_message": "<2-sentence description of the overall academic trend>",
  "best_semester": <semester number>,
  "worst_semester": <semester number>,
  "cgpa_trend": "up|down|flat",
  "attendance_trend": "up|down|flat",
  "key_observations": ["obs1", "obs2", "obs3"],
  "turning_points": ["any notable inflection points"],
  "forecast": "<what to expect in upcoming semesters based on trends>",
  "recommendations": ["action1", "action2"]
}}"""
    result = _call_ai(prompt, expect_json=True)
    if result and isinstance(result, dict):
        result.pop("model_version", None)
        return result
    if len(trend_data) >= 2:
        first_cgpa = trend_data[0].get("avg_cgpa", 0)
        last_cgpa  = trend_data[-1].get("avg_cgpa", 0)
        traj = "improving" if last_cgpa > first_cgpa + 0.1 else "declining" if last_cgpa < first_cgpa - 0.1 else "stable"
    else:
        traj = "stable"
    best  = max(trend_data, key=lambda t: t.get("avg_cgpa", 0))
    worst = min(trend_data, key=lambda t: t.get("avg_cgpa", 0))
    return {
        "overall_trajectory": traj,
        "trajectory_message": f"Academic performance is {traj} over {len(trend_data)} semesters.",
        "best_semester": best.get("semester_no", 0),
        "worst_semester": worst.get("semester_no", 0),
        "cgpa_trend": "up" if traj == "improving" else "down" if traj == "declining" else "flat",
        "attendance_trend": "flat",
        "key_observations": [f"Best CGPA in Sem {best.get('semester_no')}: {best.get('avg_cgpa',0):.2f}"],
        "turning_points": [],
        "forecast": "Continue monitoring semester-wise progress.",
        "recommendations": ["Focus on struggling semesters", "Identify pattern changes early"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 12. ANOMALY DETECTION
# ══════════════════════════════════════════════════════════════════════════════
def gemini_anomaly_detection(anomalies: list, sem_data: list) -> dict:
    if not anomalies:
        return {"summary": "No anomalies detected.", "actions": []}
    prompt = f"""Analyze these academic anomalies and provide expert recommendations.

Detected Anomalies:
{json.dumps(anomalies, indent=2)}

Context (semester data):
{json.dumps(sem_data[:20], indent=2)}

Return ONLY this JSON:
{{
  "summary": "<2-sentence summary of what these anomalies indicate>",
  "root_cause_hypotheses": ["hypothesis1", "hypothesis2"],
  "severity_assessment": "critical|high|medium|low",
  "immediate_actions": ["urgent action 1", "urgent action 2"],
  "long_term_actions": ["long term 1", "long term 2"],
  "investigation_checklist": ["check1", "check2", "check3"]
}}"""
    result = _call_ai(prompt, expect_json=True)
    if result and isinstance(result, dict):
        result.pop("model_version", None)
        return result
    return {
        "summary": f"{len(anomalies)} anomaly/anomalies detected. Immediate review recommended.",
        "root_cause_hypotheses": ["Curriculum changes", "Faculty changes", "External factors"],
        "severity_assessment": "high" if any(a.get("severity") == "critical" for a in anomalies) else "medium",
        "immediate_actions": ["Review affected semesters' data", "Schedule faculty meeting"],
        "long_term_actions": ["Implement early warning systems", "Regular monitoring protocols"],
        "investigation_checklist": ["Compare with previous year data", "Check faculty assignments", "Review syllabus changes"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 13. ENHANCED AI REPORT GENERATOR
# ══════════════════════════════════════════════════════════════════════════════
def gemini_enhanced_report(students: list, college: dict, report_type: str = "department", branch: str = "") -> dict:
    total = len(students)
    if not total:
        return {"error": "No student data available."}

    col_name   = college.get("name", "College") if college else "College"
    avg_cgpa   = round(sum(float(s.get("cgpa") or 0) for s in students) / total, 2)
    avg_att    = round(sum(float(s.get("attendance") or 0) for s in students) / total, 1)
    passing    = sum(1 for s in students if str(s.get("result","")).upper() == "PASS")
    failing    = total - passing
    pass_rate  = round(passing / total * 100, 1)
    at_risk    = sum(1 for s in students if (float(s.get("cgpa") or 0) < 5.0 or float(s.get("attendance") or 0) < 60))
    backlogs   = sum(1 for s in students if int(s.get("backlog_count") or 0) > 0)
    low_att    = sum(1 for s in students if float(s.get("attendance") or 0) < 75)

    # Branch breakdown
    branch_map: dict = {}
    for s in students:
        b = s.get("branch") or "Unknown"
        if b not in branch_map:
            branch_map[b] = {"count": 0, "cgpa_sum": 0.0, "att_sum": 0.0, "pass": 0, "at_risk": 0, "backlogs": 0}
        branch_map[b]["count"]    += 1
        branch_map[b]["cgpa_sum"] += float(s.get("cgpa") or 0)
        branch_map[b]["att_sum"]  += float(s.get("attendance") or 0)
        branch_map[b]["pass"]     += 1 if str(s.get("result","")).upper() == "PASS" else 0
        branch_map[b]["at_risk"]  += 1 if (float(s.get("cgpa") or 0) < 5.0 or float(s.get("attendance") or 0) < 60) else 0
        branch_map[b]["backlogs"] += 1 if int(s.get("backlog_count") or 0) > 0 else 0

    branch_stats = {b: {
        "count":     v["count"],
        "avg_cgpa":  round(v["cgpa_sum"] / v["count"], 2) if v["count"] else 0,
        "avg_att":   round(v["att_sum"]  / v["count"], 1) if v["count"] else 0,
        "pass_rate": round(v["pass"] / v["count"] * 100, 1) if v["count"] else 0,
        "at_risk":   v["at_risk"],
        "backlogs":  v["backlogs"],
    } for b, v in branch_map.items()}

    sorted_by_cgpa = sorted(students, key=lambda s: float(s.get("cgpa") or 0), reverse=True)
    top10    = [{"name": s.get("name",""), "cgpa": round(float(s.get("cgpa") or 0),2),
                 "attendance": round(float(s.get("attendance") or 0),1),
                 "result": s.get("result",""), "branch": s.get("branch","")} for s in sorted_by_cgpa[:10]]
    bottom10 = [{"name": s.get("name",""), "cgpa": round(float(s.get("cgpa") or 0),2),
                 "attendance": round(float(s.get("attendance") or 0),1),
                 "result": s.get("result",""), "branch": s.get("branch","")} for s in sorted_by_cgpa[-10:]]
    at_risk_list = [{"name": s.get("name",""), "cgpa": round(float(s.get("cgpa") or 0),2),
                     "attendance": round(float(s.get("attendance") or 0),1),
                     "backlogs": int(s.get("backlog_count") or 0)} 
                    for s in students if float(s.get("cgpa") or 0) < 5.0 or float(s.get("attendance") or 0) < 60]

    type_instructions = {
        "department": "Focus on subject-level insights, faculty recommendations, curriculum improvements.",
        "college":    "Focus on overall institution health, cross-branch comparison, strategic recommendations.",
        "at_risk":    "Focus ENTIRELY on at-risk students. List them, explain their specific risks, and give targeted intervention plans.",
        "toppers":    "Focus on top performers. Analyse what makes them succeed, and how to replicate their habits across the class.",
    }
    focus = type_instructions.get(report_type, type_instructions["department"])

    prompt = f"""Generate a comprehensive {report_type.replace('_',' ')} academic report for {col_name}{' — '+branch if branch else ''}.

FULL DATA:
- Total Students     : {total}
- Average CGPA       : {avg_cgpa}
- Average Attendance : {avg_att}%
- Pass Rate          : {pass_rate}% ({passing} pass, {failing} fail)
- At-Risk Students   : {at_risk} ({round(at_risk/total*100,1)}%)
- With Backlogs      : {backlogs}
- Attendance <75%    : {low_att}

Branch-wise Breakdown:
{json.dumps(branch_stats, indent=2)}

Top 10 Performers:
{json.dumps(top10, indent=2)}

Bottom 10 Students (need support):
{json.dumps(bottom10, indent=2)}

At-Risk Students:
{json.dumps(at_risk_list[:15], indent=2)}

REPORT FOCUS: {focus}

Return ONLY this JSON:
{{
  "title": "<Specific report title including college/branch name and report type>",
  "executive_summary": "<4-5 sentence executive summary with specific numbers>",
  "performance_rating": "Excellent|Good|Average|Poor|Critical",
  "key_metrics": {{
    "avg_cgpa": {avg_cgpa},
    "avg_attendance": {avg_att},
    "pass_rate": {pass_rate},
    "at_risk_pct": {round(at_risk/total*100,1) if total else 0}
  }},
  "highlights": ["specific achievement 1 with numbers", "specific achievement 2", "specific achievement 3"],
  "concerns": ["specific concern 1 with numbers", "specific concern 2"],
  "departmental_insights": ["specific data-driven insight 1", "insight 2", "insight 3"],
  "faculty_recommendations": ["actionable rec 1", "actionable rec 2", "actionable rec 3"],
  "student_support_actions": ["specific action 1", "specific action 2", "specific action 3"],
  "at_risk_names": ["name1", "name2", "name3", "name4", "name5"],
  "top_performers": ["name1 (CGPA x.xx)", "name2 (CGPA x.xx)", "name3 (CGPA x.xx)"],
  "branch_insights": ["branch comparison insight 1", "insight 2"],
  "benchmark_comparison": "<specific comparison to 7.0 CGPA / 75% attendance benchmarks>",
  "next_semester_targets": ["specific measurable target 1", "specific target 2"],
  "generated_at": "{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
}}"""

    result = _call_ai(prompt, expect_json=True, temperature=0.4)
    if result and isinstance(result, dict):
        result.pop("model_version", None)
        # Inject computed data that the template needs for charts
        result["_chart_data"] = {
            "branch_stats": branch_stats,
            "top10": top10,
            "bottom10": bottom10,
            "total": total,
            "passing": passing,
            "failing": failing,
            "at_risk": at_risk,
            "backlogs": backlogs,
            "low_att": low_att,
        }
        return result

    # Rich fallback
    best_branch  = max(branch_stats, key=lambda b: branch_stats[b]["avg_cgpa"]) if branch_stats else "—"
    worst_branch = min(branch_stats, key=lambda b: branch_stats[b]["avg_cgpa"]) if branch_stats else "—"
    rating = "Excellent" if avg_cgpa >= 8.0 else "Good" if avg_cgpa >= 7.0 else "Average" if avg_cgpa >= 5.5 else "Poor" if avg_cgpa >= 4.0 else "Critical"

    return {
        "title": f"{report_type.replace('_',' ').title()} Report — {col_name}{' (' + branch + ')' if branch else ''}",
        "executive_summary": (
            f"{col_name} has {total} students with an average CGPA of {avg_cgpa} and {avg_att}% average attendance. "
            f"The overall pass rate is {pass_rate}% with {passing} students passing and {failing} failing. "
            f"{at_risk} students ({round(at_risk/total*100,1)}%) are at risk due to low CGPA or attendance. "
            f"Best performing branch: {best_branch} (CGPA {branch_stats[best_branch]['avg_cgpa'] if best_branch != '—' else '—'}). "
            f"{backlogs} students have active backlogs requiring immediate attention."
        ),
        "performance_rating": rating,
        "key_metrics": {
            "avg_cgpa": avg_cgpa, "avg_attendance": avg_att,
            "pass_rate": pass_rate, "at_risk_pct": round(at_risk/total*100,1) if total else 0,
        },
        "highlights": [
            f"Pass rate of {pass_rate}% with {passing} students successfully cleared their semester",
            f"Average CGPA of {avg_cgpa} across {total} students",
            f"{best_branch} branch leads with avg CGPA {branch_stats[best_branch]['avg_cgpa'] if best_branch != '—' else '—'}",
        ],
        "concerns": [
            f"{at_risk} students ({round(at_risk/total*100,1)}%) are flagged as at-risk",
            f"{low_att} students have attendance below 75% threshold",
        ] + ([f"{backlogs} students have active backlogs"] if backlogs else []),
        "departmental_insights": [
            f"Branch performance ranges from {branch_stats[worst_branch]['avg_cgpa'] if worst_branch != '—' else '—'} to {branch_stats[best_branch]['avg_cgpa'] if best_branch != '—' else '—'} CGPA",
            f"Attendance concern: {low_att} students below the 75% minimum",
            f"Failing students: {failing} need immediate faculty intervention",
        ],
        "faculty_recommendations": [
            f"Conduct weekly check-ins for the {at_risk} at-risk students",
            f"Arrange supplementary sessions for {worst_branch} branch to improve CGPA",
            "Track attendance weekly and notify students below 75%",
        ],
        "student_support_actions": [
            "Schedule one-on-one counselling for students with CGPA below 5.0",
            "Set up peer study groups pairing top performers with struggling students",
            "Backlog clearance drive for the {backlogs} students with pending subjects",
        ],
        "at_risk_names": [s["name"] for s in at_risk_list[:5]],
        "top_performers": [f"{s['name']} (CGPA {s['cgpa']})" for s in top10[:5]],
        "branch_insights": [
            f"{best_branch} performs best; {worst_branch} needs most support",
            f"Cross-branch CGPA gap: {round(branch_stats[best_branch]['avg_cgpa'] - branch_stats[worst_branch]['avg_cgpa'], 2) if best_branch != '—' and worst_branch != '—' else '—'} points",
        ],
        "benchmark_comparison": (
            f"College avg CGPA {avg_cgpa} is {'above' if avg_cgpa >= 7.0 else 'below'} the 7.0 benchmark. "
            f"Attendance avg {avg_att}% is {'above' if avg_att >= 75 else 'below'} the 75% minimum."
        ),
        "next_semester_targets": [
            f"Raise avg CGPA from {avg_cgpa} to {round(min(avg_cgpa + 0.3, 10.0), 2)}",
            f"Reduce at-risk count from {at_risk} to below {max(1, at_risk - int(at_risk*0.3))}",
        ],
        "generated_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M') + " UTC",
        "_chart_data": {
            "branch_stats": branch_stats, "top10": top10, "bottom10": bottom10,
            "total": total, "passing": passing, "failing": failing,
            "at_risk": at_risk, "backlogs": backlogs, "low_att": low_att,
        },
    }

