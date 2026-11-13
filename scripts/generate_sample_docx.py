"""
generate_sample_docx.py
Generates rich formatted .docx documents for Cortex sample document dataset using python-docx.
Strictly configured for the Indian corporate context (INR currency, Indian compliance frameworks).
"""

import os
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def set_cell_background(cell, fill_hex):
    """Set background color of a table cell."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill_hex)
    tc_pr.append(shd)


def create_eng_travel_offsite_docx(output_path: str):
    doc = Document()

    # Document Title
    title = doc.add_heading("Cortex Engineering Policy Directive", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.name = "Arial"
        run.font.size = Pt(22)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x1E, 0x3A, 0x8A)  # Deep Navy

    subtitle = doc.add_paragraph("Engineering Department Travel and Technical Offsite Policy (POL-ENG-2026-003)")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in subtitle.runs:
        run.font.name = "Arial"
        run.font.size = Pt(12)
        run.font.italic = True
        run.font.color.rgb = RGBColor(0x47, 0x55, 0x69)

    doc.add_paragraph()

    # Metadata Table
    meta_table = doc.add_table(rows=5, cols=2)
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_data = [
        ("Department", "Engineering"),
        ("Clearance Level", "Internal"),
        ("Effective Date", "January 1, 2026"),
        ("Daily Meal Allowance (Per Diem)", "₹2,500 per day"),
        ("Hotel Allowance (Tier-1 Cities)", "Up to ₹6,500 per night (Bengaluru, Mumbai, Gurugram)")
    ]
    for i, (k, v) in enumerate(meta_data):
        row = meta_table.rows[i]
        c0, c1 = row.cells[0], row.cells[1]
        c0.text = k
        c1.text = v
        set_cell_background(c0, "F1F5F9")
        set_cell_background(c1, "F8FAFC")
        c0.paragraphs[0].runs[0].font.bold = True
        c0.paragraphs[0].runs[0].font.size = Pt(10)
        c1.paragraphs[0].runs[0].font.size = Pt(10)

    doc.add_paragraph()

    # Section 1
    h1 = doc.add_heading("1. Scope and Technical Offsite Objectives", level=1)
    for run in h1.runs:
        run.font.name = "Arial"
        run.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    p1 = doc.add_paragraph(
        "This policy governs business travel, technical offsite hackathons, architecture reviews, "
        "and developer conference participation for engineering personnel across our Indian development hubs "
        "in Bengaluru (Bellandur Tech Corridor), Hyderabad (HITEC City), and Pune (Hinjawadi Phase 1)."
    )
    p1.runs[0].font.size = Pt(11)

    # Section 2
    h2 = doc.add_heading("2. Daily Meal Allowance (₹2,500/day Per Diem)", level=1)
    for run in h2.runs:
        run.font.name = "Arial"
        run.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    p2 = doc.add_paragraph(
        "Engineers on travel or offsite assignments are entitled to a daily meal allowance of ₹2,500 per day. "
        "This flat per diem covers breakfast, lunch, dinner, specialty coffee, and incidentals during technical sprints. "
        "Individual receipts are not required when claiming within this standard allowance. "
        "Pre-approved engineering team dinners (4+ engineers) may be expensed up to ₹3,500 per person with itemized GST invoices."
    )
    p2.runs[0].font.size = Pt(11)

    # Section 3
    h3 = doc.add_heading("3. Hotel Accommodation Ceilings", level=1)
    for run in h3.runs:
        run.font.name = "Arial"
        run.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    p3 = doc.add_paragraph(
        "Lodging limits ensure safety, comfort, and proximity to engineering facilities:"
    )
    p3.runs[0].font.size = Pt(11)

    bp1 = doc.add_paragraph("Tier-1 Cities (Bengaluru, Mumbai, Gurugram, Delhi NCR): Up to ₹6,500 per night (excl. GST).", style='List Bullet')
    bp1.runs[0].font.size = Pt(10.5)
    bp2 = doc.add_paragraph("Tier-2 Tech Hubs (Pune, Hyderabad, Chennai): Up to ₹5,000 per night (excl. GST).", style='List Bullet')
    bp2.runs[0].font.size = Pt(10.5)
    bp3 = doc.add_paragraph("Extended Residencies (>14 days): Serviced business apartment coordination via Workplace Services.", style='List Bullet')
    bp3.runs[0].font.size = Pt(10.5)

    # Section 4
    h4 = doc.add_heading("4. Ground Transportation: Uber & Ola for Business", level=1)
    for run in h4.runs:
        run.font.name = "Arial"
        run.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    p4 = doc.add_paragraph(
        "All active engineers are provisioned with an Uber for Business and Ola Corporate corporate account linked to their work email. "
        "Airport transit to/from Bengaluru (BLR), Mumbai (BOM), or Hyderabad (HYD) is fully authorized via corporate cab profiles or pre-paid counters. "
        "Intra-city auto-rickshaw or metro commute is claimable up to ₹800/day against UPI transaction screenshots."
    )
    p4.runs[0].font.size = Pt(11)

    # Section 5
    h5 = doc.add_heading("5. Flight and High-Speed Rail Guidelines", level=1)
    p5 = doc.add_paragraph(
        "Domestic air travel is booked in economy class (IndiGo, Air India, Akasa Air, Vistara). "
        "For inter-city regional travel (e.g., Bengaluru to Chennai, Mumbai to Pune), Vande Bharat Express (Executive Chair Car) "
        "or Shatabdi 1st AC is fully approved."
    )
    p5.runs[0].font.size = Pt(11)

    doc.save(output_path)
    print(f"Generated DOCX: {output_path}")


def create_hr_leave_policy_docx(output_path: str):
    doc = Document()

    title = doc.add_heading("Cortex Human Resources Policy", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.name = "Arial"
        run.font.size = Pt(22)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x15, 0x80, 0x3D)  # Forest Green

    subtitle = doc.add_paragraph("Leave Entitlements, Statutory Benefits, and Remote Work Framework (POL-HR-2026-001)")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in subtitle.runs:
        run.font.name = "Arial"
        run.font.size = Pt(12)
        run.font.italic = True
        run.font.color.rgb = RGBColor(0x47, 0x55, 0x69)

    doc.add_paragraph()

    meta_table = doc.add_table(rows=5, cols=2)
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_data = [
        ("Department", "Human Resources (HR)"),
        ("Clearance Level", "Internal"),
        ("Effective Date", "January 1, 2026"),
        ("Annual Leave Entitlement", "24 Working Days per Calendar Year"),
        ("Home Office Reimbursement", "₹35,000 (One-time) + ₹1,500/month Broadband")
    ]
    for i, (k, v) in enumerate(meta_data):
        row = meta_table.rows[i]
        c0, c1 = row.cells[0], row.cells[1]
        c0.text = k
        c1.text = v
        set_cell_background(c0, "DCFCE7")  # Light Green
        set_cell_background(c1, "F0FDF4")
        c0.paragraphs[0].runs[0].font.bold = True
        c0.paragraphs[0].runs[0].font.size = Pt(10)
        c1.paragraphs[0].runs[0].font.size = Pt(10)

    doc.add_paragraph()

    h1 = doc.add_heading("1. Annual Leave and Casual/Sick Leave", level=1)
    p1 = doc.add_paragraph(
        "Regular full-time employees receive 24 working days of paid annual leave per calendar year (accrued at 2.0 days/month). "
        "Up to 8 days may be carried forward into the next year (to be utilized by March 31). "
        "Employees also receive 12 days of casual/sick leave annually for personal exigencies and recovery from illness. "
        "Absences exceeding two consecutive days require a registered medical practitioner's certificate."
    )
    p1.runs[0].font.size = Pt(11)

    h2 = doc.add_heading("2. Maternity, Paternity, and Adoption Benefits", level=1)
    p2 = doc.add_paragraph(
        "Under the Maternity Benefit (Amendment) Act, 2017, eligible female employees receive 26 weeks of fully paid maternity leave "
        "for up to two surviving children. Creche facilities or an allowance of ₹6,000/month is provided for children under 6 years. "
        "Male employees receive 10 working days of paid paternity leave within six months of childbirth. "
        "Adoptive and commissioning mothers receive 12 weeks of paid leave."
    )
    p2.runs[0].font.size = Pt(11)

    h3 = doc.add_heading("3. Remote Work: Home Office Setup & Broadband Reimbursements", level=1)
    p3 = doc.add_paragraph(
        "To ensure ergonomic and safe home office workstations, permanent remote and hybrid employees receive a one-time "
        "reimbursement of up to ₹35,000 for approved ergonomic furniture, desk monitors, and computer accessories with valid GST invoices. "
        "Additionally, employees receive up to ₹1,500 per month for high-speed fiber broadband upon submission of monthly telecom bills. "
        "Mandatory Core Working Hours are established as 10:00 AM – 4:00 PM IST."
    )
    p3.runs[0].font.size = Pt(11)

    h4 = doc.add_heading("4. Statutory Social Security (EPF & Gratuity)", level=1)
    p4 = doc.add_paragraph(
        "Statutory retirement benefits are administered strictly under Indian labor laws. "
        "Mandatory contributions equal to 12% of basic wages are remitted to the Employees' Provident Fund Organisation (EPFO). "
        "Employees completing five continuous years of service are entitled to statutory gratuity under the Payment of Gratuity Act, 1972."
    )
    p4.runs[0].font.size = Pt(11)

    doc.save(output_path)
    print(f"Generated DOCX: {output_path}")


if __name__ == "__main__":
    target_dir = os.path.abspath(r"d:\rankuno\cortex\data\sample_documents")
    os.makedirs(target_dir, exist_ok=True)

    eng_docx_path = os.path.join(target_dir, "ENG_Travel_and_Offsite_Policy.docx")
    hr_docx_path = os.path.join(target_dir, "HR_Remote_Work_and_Leave_Policy_2026.docx")

    create_eng_travel_offsite_docx(eng_docx_path)
    create_hr_leave_policy_docx(hr_docx_path)
    print("All sample DOCX documents successfully created!")
