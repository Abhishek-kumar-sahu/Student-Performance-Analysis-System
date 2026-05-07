# ── Programme → Branch catalogue ─────────────────────────────────────
# Single source of truth for all programme categories and their branches.
# Imported by admin.py, teacher.py, auth.py, upload.py
#
# Each entry: (branch_code, branch_full_name)
# Branch codes follow DTE/University conventions where applicable.

PROGRAMME_BRANCHES = {
    "BTech": {
        "label": "B.Tech – Bachelor of Technology",
        "branches": [
            ("CSE",   "Computer Science & Engineering"),
            ("AIML", "CSE – AI & Machine Learning"),
            ("AIDS", "AI & Data Science"),
            ("CY", "CSE – Cyber Security"),
            ("CC", "CSE – Cloud Computing"),
            ("IOT","CSE – Internet of Things"),
            ("IT",   "Information Technology"),
            ("ECE",   "Electronics & Communication Engg"),
            ("VLSI", "Electronics & VLSI Design"),
            ("EE",   "Electrical Engineering"),
            ("EV", "Electrical – EV Technology"),
            ("MECH",   "Mechanical Engineering"),
            ("RO", "Mechanical – Robotics & Automation"),
            ("CE",   "Civil Engineering"),
            ("CHE",   "Chemical Engineering"),
            ("BT",   "Biotechnology"),
            ("AE",   "Aerospace Engineering"),
            ("AG",   "Agricultural Engineering"),
            ("MIN",   "Mining Engineering"),
        ],
    },
    "BPharm": {
        "label": "B.Pharm – Bachelor of Pharmacy",
        "branches": [
            ("BP01",  "Bachelor of Pharmacy"),
        ],
    },
    "BArch": {
        "label": "B.Arch – Bachelor of Architecture",
        "branches": [
            ("BA01",  "Bachelor of Architecture"),
        ],
    },
    "BCA": {
        "label": "BCA – Bachelor of Computer Applications",
        "branches": [
            ("BCA", "Bachelor of Computer Applications"),
            ("BCA-CT", "BCA – Cloud Technology & Information Security"),
            ("BCA-DA", "BCA – Data Analytics"),
        ],
    },
    "MTech": {
        "label": "M.Tech – Master of Technology",
        "branches": [
            ("MTech-CSE",  "M.Tech – Computer Science & Engineering"),
            ("MTech-IT",  "M.Tech – Information Technology"),
            ("MTech-ECE",  "M.Tech – Electronics & Communication Engg"),
            ("MTech-EE",  "M.Tech – Electrical Engineering"),
            ("MTech-MECH",  "M.Tech – Mechanical Engineering"),
            ("MTech-CE",  "M.Tech – Civil Engineering"),
            ("MTech-CHE",  "M.Tech – Chemical Engineering"),
            ("MTech-BT",  "M.Tech – Biotechnology"),
            ("MTech-VLSI",  "M.Tech – VLSI Design"),
            ("MTech-PED",  "M.Tech – Power Electronics & Drives"),
            ("MTech-SE",  "M.Tech – Structural Engineering"),
        ],
    },
    "ME": {
        "label": "M.E. – Master of Engineering",
        "branches": [
            ("MTech-CSE",  "M.E. – Computer Science & Engineering"),
            ("MTech-ECE",  "M.E. – Electronics & Communication Engg"),
            ("MTech-EE",  "M.E. – Electrical Engineering"),
            ("MTech-MECH",  "M.E. – Mechanical Engineering"),
            ("MTech-CE",  "M.E. – Civil Engineering"),
        ],
    },
    "MCA": {
        "label": "MCA – Master of Computer Applications",
        "branches": [
            ("MCA", "Master of Computer Applications"),
        ],
    },
    "MBA": {
        "label": "MBA – Master of Business Administration",
        "branches": [
            ("MBA-GM", "MBA – General Management"),
            ("MBA-FIN", "MBA – Finance"),
            ("MBA-HR", "MBA – Human Resource Management"),
            ("MBA-MK", "MBA – Marketing"),
            ("MBA-OM", "MBA – Operations Management"),
            ("MBA-BA", "MBA – Business Analytics"),
        ],
    },
    "MPharm": {
        "label": "M.Pharm – Master of Pharmacy",
        "branches": [
            ("MPharm-P",  "M.Pharm – Pharmaceutics"),
            ("MPharm-PP",  "M.Pharm – Pharmacology"),
            ("MPharm-PC",  "M.Pharm – Pharmaceutical Chemistry"),
        ],
    },
    "PhD": {
        "label": "Ph.D – Doctor of Philosophy",
        "branches": [
            ("PhD-E", "Ph.D – Engineering"),
            ("PhD-S", "Ph.D – Sciences"),
            ("PhD-M", "Ph.D – Management"),
            ("PhD-P", "Ph.D – Pharmacy"),
            ("PhD-A", "Ph.D – Architecture & Planning"),
        ],
    },
    "Diploma": {
        "label": "Diploma – Diploma in Engineering",
        "branches": [
            ("Dip-CE", "Diploma – Civil Engineering"),
            ("Dip-MECH", "Diploma – Mechanical Engineering"),
            ("Dip-EE", "Diploma – Electrical Engineering"),
            ("Dip-ECE", "Diploma – Electronics & Communication Engg"),
            ("Dip-CSE", "Diploma – Computer Science & Engineering"),
            ("Dip-IT", "Diploma – Information Technology"),
            ("Dip-CHE", "Diploma – Chemical Engineering"),
            ("Dip-AUTO", "Diploma – Automobile Engineering"),
        ],
    },
    "INT_BCA_MCA": {
        "label": "Integrated BCA + MCA",
        "branches": [
            ("IBCAMCA", "Integrated BCA + MCA"),
        ],
    },
    "INT_DDI_PG": {
        "label": "Integrated UG + PG (DDI‑PG)",
        "branches": [
            ("INT_CSE", "Integrated B.Tech + M.Tech – CSE"),
            ("INT_ECE", "Integrated B.Tech + M.Tech – ECE"),
            ("INT_MECH", "Integrated B.Tech + M.Tech – ME"),
            ("INT_CE", "Integrated B.Tech + M.Tech – CE"),
            ("INT_EE", "Integrated B.Tech + M.Tech – EE"),
        ],
    },
}

# ── Derived flat lists ────────────────────────────────────────────────

# Full flat list of (code, name) across all programmes
BRANCH_CHOICES = [
    (code, name)
    for prog_data in PROGRAMME_BRANCHES.values()
    for code, name in prog_data["branches"]
]

# Dict: code → name
BRANCH_DICT = dict(BRANCH_CHOICES)

# Programme-level choices: (value, label)
PROGRAMME_CHOICES = [
    (key, data["label"])
    for key, data in PROGRAMME_BRANCHES.items()
]

# Dict: programme_key → label
PROGRAMME_DICT = {key: data["label"] for key, data in PROGRAMME_BRANCHES.items()}
