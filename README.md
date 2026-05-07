# Student Performance Analysis System (SPAS)

A professional academic intelligence platform for RGPV-affiliated colleges in Madhya Pradesh.

## What's New (Latest Update)
- **Multi-programme support**: All 13 programme categories now supported across the entire system
- **Centralised branch catalogue**: Single source of truth in `routes/branches.py` — no more duplicate lists
- **Smart branch dropdowns**: Branch selector filters live by chosen programme using `<optgroup>` grouping
- **Bug fix**: Branch names containing `&` (e.g. "Electronics & Communication Engg") were being HTML-escaped to `&amp;` before database storage — now fixed across all registration routes and existing DB records

## Supported Programmes
| Programme | Examples |
|-----------|---------|
| B.Tech | CSE, AI/ML, IoT, ECE, EE, ME, CE, Chemical, Bio, and more |
| B.Pharm | Bachelor of Pharmacy |
| B.Arch | Bachelor of Architecture |
| BCA | General, Cloud Security, Data Analytics |
| M.Tech | CSE, ECE, EE, ME, CE, VLSI, Structural, and more |
| M.E. | CSE, ECE, EE, ME, CE |
| MCA | Master of Computer Applications |
| MBA | General, Finance, HR, Marketing, Operations, Analytics |
| M.Pharm | Pharmaceutics, Pharmacology, Pharmaceutical Chemistry |
| Ph.D | Engineering, Sciences, Management, Pharmacy, Architecture |
| Diploma | Civil, Mechanical, Electrical, ECE, CSE, IT, Chemical, Automobile |
| Integrated BCA + MCA | 5-year integrated programme |
| Integrated DDI-PG | B.Tech + M.Tech dual degree in CSE, ECE, ME, CE, EE |

## Features
- **Multi-role system**: Super Admin → College Admin → Teacher → Student
- **Student registration**: Self-register or register by teacher
- **RGPV result scraping**: Brute-force enrollment number generation + OCR captcha handling
- **Demo fallback**: Realistic synthetic data when portal unreachable
- **AI/ML predictions**: Academic outcome forecasting based on CGPA, attendance & trends
- **PDF reports**: Individual student & class-wide downloadable reports
- **Email notifications**: All auth events (working SMTP + console fallback)
- **Professional dark UI**: Clean, responsive design with Chart.js analytics

## Quick Start
```bash
pip install -r requirements.txt
cp .env.example .env   # Edit your config
python app.py
```
Open http://localhost:5000 — credentials in `instance/credential.txt`

## Email Setup
Set `SMTP_USER` and `SMTP_PASS` in `.env`. For Gmail, use an App Password.
Without SMTP config, emails are printed to console (no crash).

## Student Registration Flow
1. **Self-registration**: Student fills `/register-student` → pending → admin/teacher approves → credentials sent by email
2. **Teacher registers**: Teacher goes to `/teacher/register-student` → immediately approved → credentials sent to student email

## Roles
| Role | Access |
|------|--------|
| Super Admin | All colleges, approve college registrations |
| Admin | Own college: fetch RGPV data, manage teachers, approve student registrations |
| Teacher | View class, register & approve students, download PDFs |
| Student | View own academic record, download report PDF |

## Project Structure
```
routes/
  branches.py        ← Centralised programme & branch catalogue (single source of truth)
  admin.py           ← Admin dashboard, teacher management
  teacher.py         ← Class management, student registration
  auth.py            ← Login, self-registration
  upload.py          ← RGPV result & attendance upload
  student.py         ← Student dashboard & analytics
services/
  gemini_service.py  ← AI/ML predictions & advisor
templates/           ← Jinja2 HTML templates
static/              ← CSS, JS, images
database.py          ← Schema, seeding, DB helpers
security.py          ← Sanitisation, CSRF, rate limiting
app.py               ← Flask app entry point
```
