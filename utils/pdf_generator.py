"""
utils/pdf_generator.py  —  PDF generation using ReportLab
Generates student academic report and teacher class report.
"""
import io
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# ── Color palette matching dark UI ──────────────────────────────────
C_DARK   = colors.HexColor("#0a1520")
C_CYAN   = colors.HexColor("#00cccc")
C_GREEN  = colors.HexColor("#00bb77")
C_YELLOW = colors.HexColor("#ccaa00")
C_RED    = colors.HexColor("#cc3333")
C_BLUE   = colors.HexColor("#3377cc")
C_GRAY   = colors.HexColor("#555555")
C_LGRAY  = colors.HexColor("#eeeeee")
C_WHITE  = colors.white
C_BG     = colors.HexColor("#f8fafc")

W, H = A4


def _grade_color(grade: str):
    g = (grade or "").upper()
    if g in ("O","A+"): return C_GREEN
    if g in ("A","B+"): return C_BLUE
    if g in ("B","C"):  return C_YELLOW
    if g == "F":        return C_RED
    return C_GRAY


def _result_color(result: str):
    return C_GREEN if str(result).upper() == "PASS" else C_RED


def _sgpa_color(sgpa: float):
    if sgpa >= 8.5: return C_GREEN
    if sgpa >= 7.0: return C_BLUE
    if sgpa >= 5.5: return C_YELLOW
    return C_RED


def generate_student_report(student: dict, semesters: list) -> bytes:
    """
    Generate a full academic report PDF for a single student.
    student: dict with name, enrollment_no, branch, college_code, etc.
    semesters: list of dicts {semester_no, sgpa, cgpa, percentage, attendance,
               result, backlog_count, subjects:[{...},...]}
    Returns: bytes (PDF content)
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=16*mm, bottomMargin=16*mm,
    )

    styles = getSampleStyleSheet()
    story  = []

    # ── Header banner ──────────────────────────────────────────────
    header_data = [[
        Paragraph(
            f"<font color='white' size='18'><b>SPAS — Student Academic Report</b></font><br/>"
            f"<font color='#aadddd' size='9'>Student Performance Analysis System</font>",
            ParagraphStyle("hdr", alignment=TA_LEFT, leading=20)
        ),
        Paragraph(
            f"<font color='#aadddd' size='8'>Generated on<br/>{datetime.now().strftime('%d %b %Y, %H:%M')}</font>",
            ParagraphStyle("hdr2", alignment=TA_RIGHT, leading=13)
        )
    ]]
    ht = Table(header_data, colWidths=[W - 100*mm, 65*mm])
    ht.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), C_DARK),
        ("TEXTCOLOR",    (0,0),(-1,-1), C_WHITE),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ("LEFTPADDING",  (0,0),(-1,-1), 12),
        ("RIGHTPADDING", (0,0),(-1,-1), 12),
        ("TOPPADDING",   (0,0),(-1,-1), 10),
        ("BOTTOMPADDING",(0,0),(-1,-1), 10),
    ]))
    story.append(ht)
    story.append(Spacer(1, 6*mm))

    # ── Student info card ──────────────────────────────────────────
    latest = semesters[-1] if semesters else {}
    cgpa   = latest.get("cgpa", 0) or 0
    sgpa   = latest.get("sgpa", 0) or 0
    att    = latest.get("attendance", 0) or 0

    info = [
        ["STUDENT NAME",    student.get("name","—"),    "CGPA",       f"{cgpa:.2f}"],
        ["ENROLLMENT NO",   student.get("enrollment_no","—"), "SGPA (Latest)", f"{sgpa:.2f}"],
        ["PROGRAMME",       f"{student.get('programme','BE')} — {student.get('branch','—')}",
         "ATTENDANCE", f"{att:.1f}%"],
        ["COLLEGE CODE",    student.get("college_code","—"),
         "SEMESTERS",  str(len(semesters))],
    ]
    info_t = Table(info, colWidths=[45*mm, 70*mm, 40*mm, 30*mm])
    info_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(0,-1), C_DARK),
        ("BACKGROUND",    (2,0),(2,-1), C_DARK),
        ("TEXTCOLOR",     (0,0),(0,-1), C_CYAN),
        ("TEXTCOLOR",     (2,0),(2,-1), C_CYAN),
        ("TEXTCOLOR",     (1,0),(1,-1), colors.black),
        ("TEXTCOLOR",     (3,0),(3,-1), colors.black),
        ("FONTNAME",      (0,0),(0,-1), "Helvetica-Bold"),
        ("FONTNAME",      (2,0),(2,-1), "Helvetica-Bold"),
        ("FONTNAME",      (1,0),(1,-1), "Helvetica"),
        ("FONTSIZE",      (0,0),(-1,-1), 8),
        ("FONTSIZE",      (3,0),(3,-1), 9),
        ("BACKGROUND",    (1,0),(1,-1), C_BG),
        ("BACKGROUND",    (3,0),(3,-1), C_BG),
        ("BOX",           (0,0),(-1,-1), 1, C_CYAN),
        ("INNERGRID",     (0,0),(-1,-1), 0.5, colors.HexColor("#cccccc")),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
    ]))
    story.append(info_t)
    story.append(Spacer(1, 5*mm))

    # ── SGPA trend table (summary) ─────────────────────────────────
    if semesters:
        story.append(Paragraph(
            "<font color='#0a1520' size='9'><b>SEMESTER PERFORMANCE SUMMARY</b></font>",
            ParagraphStyle("sec", leftIndent=0, spaceAfter=4)
        ))
        sum_hdr = [["SEM","SGPA","CGPA","PERCENTAGE","ATTENDANCE","BACKLOGS","RESULT"]]
        sum_rows = []
        for s in semesters:
            sum_rows.append([
                str(s.get("semester_no","—")),
                f"{(s.get('sgpa') or 0):.2f}",
                f"{(s.get('cgpa') or 0):.2f}",
                f"{(s.get('percentage') or 0):.1f}%",
                f"{(s.get('attendance') or 0):.1f}%",
                str(s.get("backlog_count",0)),
                str(s.get("result","—")),
            ])
        sum_t = Table(sum_hdr + sum_rows, colWidths=[12*mm,20*mm,20*mm,30*mm,30*mm,22*mm,22*mm])
        sum_style = [
            ("BACKGROUND",   (0,0),(-1,0), C_DARK),
            ("TEXTCOLOR",    (0,0),(-1,0), C_CYAN),
            ("FONTNAME",     (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",     (0,0),(-1,-1), 8),
            ("ALIGN",        (0,0),(-1,-1), "CENTER"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
            ("BOX",          (0,0),(-1,-1), 1, C_CYAN),
            ("INNERGRID",    (0,0),(-1,-1), 0.3, colors.lightgrey),
            ("TOPPADDING",   (0,0),(-1,-1), 4),
            ("BOTTOMPADDING",(0,0),(-1,-1), 4),
        ]
        for i,s in enumerate(semesters,1):
            res = str(s.get("result","")).upper()
            col = C_GREEN if res=="PASS" else C_RED
            sum_style.append(("TEXTCOLOR",(6,i),(6,i), col))
            sum_style.append(("FONTNAME",(6,i),(6,i), "Helvetica-Bold"))
            sgpa_col = _sgpa_color(s.get("sgpa",0) or 0)
            sum_style.append(("TEXTCOLOR",(1,i),(1,i), sgpa_col))
            sum_style.append(("FONTNAME",(1,i),(1,i), "Helvetica-Bold"))
        sum_t.setStyle(TableStyle(sum_style))
        story.append(sum_t)
        story.append(Spacer(1, 5*mm))

    # ── Per-semester subject breakdown ─────────────────────────────
    for s in semesters:
        sem_no  = s.get("semester_no","?")
        subjects = s.get("subjects",[])
        if not subjects: continue

        sem_title = Table([[
            Paragraph(f"<font color='white' size='9'><b>Semester {sem_no}</b></font>", ParagraphStyle("st")),
            Paragraph(
                f"<font color='#aadddd' size='8'>SGPA: {s.get('sgpa') or 0:.2f}  |  "
                f"CGPA: {s.get('cgpa') or 0:.2f}  |  Attendance: {s.get('attendance') or 0:.1f}%  |  "
                f"Result: {s.get('result','—') or '—'}</font>",
                ParagraphStyle("st2", alignment=TA_RIGHT)
            )
        ]], colWidths=[60*mm, W-120*mm])
        sem_title.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),(-1,-1), C_DARK),
            ("LEFTPADDING",  (0,0),(-1,-1), 8),
            ("RIGHTPADDING", (0,0),(-1,-1), 8),
            ("TOPPADDING",   (0,0),(-1,-1), 5),
            ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ]))

        subj_hdr = [["SUBJECT","TYPE","INTERNAL\n(30%)","EXTERNAL\n(70%)","TOTAL\n/100","GRADE","GP","CREDITS","ATT %","STATUS"]]
        subj_rows = []
        for sm in subjects:
            subj_rows.append([
                sm.get("subject_name","—")[:28],
                (sm.get("subject_type","—") or "—")[:5],
                f"{sm.get('internal_marks') or 0:.1f}",
                f"{sm.get('external_marks') or 0:.1f}",
                f"{sm.get('total_marks') or 0:.1f}",
                sm.get("grade","—") or "—",
                f"{sm.get('grade_point') or 0:.1f}",
                f"{sm.get('credits') or 0:.0f}",
                f"{sm.get('attendance_pct') or 0:.0f}%",
                sm.get("status","—") or "—",
            ])

        cw = [48*mm,14*mm,16*mm,16*mm,15*mm,12*mm,10*mm,14*mm,12*mm,14*mm]
        subj_t = Table(subj_hdr+subj_rows, colWidths=cw, repeatRows=1)
        subj_style = [
            ("BACKGROUND",    (0,0),(-1,0), colors.HexColor("#1a2f45")),
            ("TEXTCOLOR",     (0,0),(-1,0), C_CYAN),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 7.5),
            ("ALIGN",         (0,0),(-1,-1), "CENTER"),
            ("ALIGN",         (0,0),(0,-1), "LEFT"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
            ("BOX",           (0,0),(-1,-1), 0.8, C_CYAN),
            ("INNERGRID",     (0,0),(-1,-1), 0.25, colors.lightgrey),
            ("TOPPADDING",    (0,0),(-1,-1), 3),
            ("BOTTOMPADDING", (0,0),(-1,-1), 3),
            ("LEFTPADDING",   (0,1),(0,-1), 4),
        ]
        for i,sm in enumerate(subjects,1):
            grade_col = _grade_color(sm.get("grade",""))
            res_col   = _result_color(sm.get("status",""))
            subj_style.append(("TEXTCOLOR",(5,i),(5,i), grade_col))
            subj_style.append(("FONTNAME",(5,i),(5,i), "Helvetica-Bold"))
            subj_style.append(("TEXTCOLOR",(9,i),(9,i), res_col))
            subj_style.append(("FONTNAME",(9,i),(9,i), "Helvetica-Bold"))
        subj_t.setStyle(TableStyle(subj_style))

        story.append(KeepTogether([sem_title, Spacer(1,1*mm), subj_t, Spacer(1,5*mm)]))

    # ── Footer ─────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_CYAN))
    story.append(Spacer(1,2*mm))
    story.append(Paragraph(
        "<font size='7' color='gray'>This report was generated by SPAS — Student Performance Analysis System. "
        "For official academic records contact the university registrar.</font>",
        ParagraphStyle("footer", alignment=TA_CENTER)
    ))

    doc.build(story)
    return buf.getvalue()


def generate_class_report(college_name: str, groups: list) -> bytes:
    """
    Generate a class-wide academic report for teacher download.
    groups: list of dicts {branch, semester, students:[...]}
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=14*mm, bottomMargin=14*mm,
    )
    styles = getSampleStyleSheet()
    story  = []

    # Calculate total stats across all groups
    total_students = 0
    total_passing = 0
    total_cgpa = 0
    total_att = 0
    
    for g in groups:
        students = g["students"]
        total_students += len(students)
        total_passing += sum(1 for s in students if str(s.get("result","")).upper()=="PASS")
        total_cgpa += sum(s.get("cgpa",0) or 0 for s in students)
        total_att += sum(s.get("attendance",0) or 0 for s in students)

    avg_cgpa = (total_cgpa / total_students) if total_students else 0
    avg_att  = (total_att / total_students) if total_students else 0

    # Header
    hdr_data = [[
        Paragraph(
            f"<font color='white' size='14'><b>Academic Performance Report</b></font><br/>"
            f"<font color='#aadddd' size='8'>{college_name} · Performance Analytics Centre</font>",
            ParagraphStyle("h",leading=18)
        ),
        Paragraph(
            f"<font color='#aadddd' size='7'>SPAS — Student Performance Analysis System<br/>"
            f"{datetime.now().strftime('%d %b %Y, %H:%M')}</font>",
            ParagraphStyle("h2",alignment=TA_RIGHT,leading=12)
        )
    ]]
    ht = Table(hdr_data, colWidths=[W-90*mm, 55*mm])
    ht.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), C_DARK),
        ("LEFTPADDING",  (0,0),(-1,-1), 10),
        ("RIGHTPADDING", (0,0),(-1,-1), 10),
        ("TOPPADDING",   (0,0),(-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(ht)
    story.append(Spacer(1,4*mm))

    # Summary stats
    stats_data = [
        [f"Total Students: {total_students}", f"Passing: {total_passing}",
         f"Failing: {total_students-total_passing}", f"Avg CGPA: {avg_cgpa:.2f}",
         f"Avg Attendance: {avg_att:.1f}%"]
    ]
    st = Table(stats_data, colWidths=[(W-32*mm)/5]*5)
    st.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), colors.HexColor("#1a2f45")),
        ("TEXTCOLOR",    (0,0),(-1,-1), C_CYAN),
        ("FONTNAME",     (0,0),(-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0),(-1,-1), 8),
        ("ALIGN",        (0,0),(-1,-1), "CENTER"),
        ("TOPPADDING",   (0,0),(-1,-1), 6),
        ("BOTTOMPADDING",(0,0),(-1,-1), 6),
        ("BOX",          (0,0),(-1,-1), 1, C_CYAN),
        ("INNERGRID",    (0,0),(-1,-1), 0.3, C_CYAN),
    ]))
    story.append(st)
    story.append(Spacer(1,6*mm))

    # Sections for each group
    for g in groups:
        branch = g.get("branch", "All Branches")
        semester = g.get("semester", 0)
        students = g.get("students", [])
        
        if not students: continue

        # Group header
        gh_data = [[
            Paragraph(f"<b>{branch}</b> — Semester {semester}", 
                      ParagraphStyle("gh", fontName="Helvetica-Bold", fontSize=10, textColor=C_DARK)),
            Paragraph(f"Students: {len(students)}", 
                      ParagraphStyle("gh2", alignment=TA_RIGHT, fontSize=9, textColor=C_GRAY))
        ]]
        ght = Table(gh_data, colWidths=[W-60*mm, 30*mm])
        ght.setStyle(TableStyle([
            ("BOTTOMPADDING", (0,0),(-1,-1), 2),
            ("VALIGN", (0,0),(-1,-1), "BOTTOM"),
        ]))
        story.append(ght)
        story.append(HRFlowable(width="100%", thickness=1, color=C_DARK, spaceAfter=2))

        # Table for this group
        hdr = [["#","ENROLLMENT","NAME","CGPA","SGPA","ATT%","RESULT","BACKLOGS"]]
        rows = []
        for i,s in enumerate(students,1):
            rows.append([
                str(i),
                s.get("enrollment_no","—"),
                s.get("name","—")[:22],
                f"{(s.get('cgpa') or 0):.2f}",
                f"{(s.get('sgpa') or 0):.2f}",
                f"{(s.get('attendance') or 0):.0f}%",
                s.get("result","—"),
                str(s.get("backlog_count",0)),
            ])

        cw = [8*mm,32*mm,50*mm,18*mm,18*mm,14*mm,16*mm,18*mm]
        mt = Table(hdr+rows, colWidths=cw, repeatRows=1)
        ms = [
            ("BACKGROUND",    (0,0),(-1,0), C_DARK),
            ("TEXTCOLOR",     (0,0),(-1,0), C_CYAN),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 7.5),
            ("ALIGN",         (0,0),(-1,-1), "CENTER"),
            ("ALIGN",         (2,0),(2,-1), "LEFT"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE,C_LGRAY]),
            ("BOX",           (0,0),(-1,-1), 0.8, C_CYAN),
            ("INNERGRID",     (0,0),(-1,-1), 0.25, colors.lightgrey),
            ("TOPPADDING",    (0,0),(-1,-1), 3),
            ("BOTTOMPADDING", (0,0),(-1,-1), 3),
        ]
        for i,s in enumerate(students,1):
            res_col = _result_color(s.get("result",""))
            ms.append(("TEXTCOLOR",(6,i),(6,i),res_col))
            ms.append(("FONTNAME",(6,i),(6,i),"Helvetica-Bold"))
            cgpa_col = _sgpa_color(s.get("cgpa",0) or 0)
            ms.append(("TEXTCOLOR",(3,i),(3,i),cgpa_col))
        mt.setStyle(TableStyle(ms))
        story.append(mt)
        story.append(Spacer(1, 10*mm))

    # Final footer
    story.append(Spacer(1,5*mm))
    story.append(HRFlowable(width="100%",thickness=0.5,color=C_CYAN))
    story.append(Paragraph(
        "<font size='6.5' color='gray'>Generated by SPAS · Student Performance Analysis System · Confidential Academic Record</font>",
        ParagraphStyle("ft",alignment=TA_CENTER)
    ))

    doc.build(story)
    return buf.getvalue()
