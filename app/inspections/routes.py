from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response
from flask_login import login_required, current_user
from app import db
from app.models import InspectionReport, Company, CompanyProfile
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.utils import get_column_letter
from io import BytesIO
import re

inspections_bp = Blueprint("inspections", __name__, url_prefix="/inspections")


def _html_to_pdf_bytes(html, header_html=None, footer_html=None, fit_one_page=False):
    """Renders an HTML string to PDF bytes, via a separate subprocess
    running app/pdf_worker.py (see that file for why it's a subprocess
    rather than an in-process Playwright call). Replaces WeasyPrint, which
    depends on native GTK/Pango/Cairo system libraries (gobject-2.0-0.dll
    etc.) that pip cannot install on Windows -- Playwright's Chromium
    engine renders the exact same CSS (including @page, @font-face,
    Google Fonts) as a real browser would, so nothing about the PDF
    templates needs to change.

    header_html/footer_html, if given, become Playwright's native
    repeating page header/footer (see pdf_worker.py) -- the correct way
    to get a letterhead that shows on every page with working page
    numbers, rather than CSS running-element tricks that don't work
    outside WeasyPrint.

    Raises RuntimeError with a clear setup message if Playwright itself,
    or its Chromium browser, isn't installed yet -- callers catch this
    the same way they used to catch weasyprint's ImportError.
    """
    import subprocess
    import sys
    import os
    import json

    worker_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pdf_worker.py")
    payload = json.dumps({
        "html": html, "header_html": header_html, "footer_html": footer_html,
        "fit_one_page": fit_one_page,
    })

    try:
        result = subprocess.run(
            [sys.executable, worker_path],
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "PDF export needs the 'playwright' package -- run: pip install -r requirements.txt"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("PDF export timed out -- try again, or check that Chromium is installed.")

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        if "Executable doesn't exist" in stderr or "playwright install" in stderr:
            raise RuntimeError(
                "PDF export needs Chromium installed -- run: playwright install chromium"
            )
        if "No module named" in stderr and "playwright" in stderr:
            raise RuntimeError(
                "PDF export needs the 'playwright' package -- run: pip install -r requirements.txt"
            )
        # Anything else: surface the real error rather than a generic one,
        # since this is a fresh, previously-unseen failure mode.
        raise RuntimeError(f"PDF export failed: {stderr.strip()[-500:]}")

    return result.stdout


def _logo_data_uri(profile=None):
    """The org logo as a base64 data: URI, for embedding directly in PDF
    headers/footers. PDFs now render inside a separate subprocess (see
    pdf_worker.py) that has no connection to the running Flask server, so
    a normal /static/... URL wouldn't resolve at all -- embedding the
    image's actual bytes sidesteps that entirely.

    Prefers profile.logo_filename (an uploaded logo from the My Company
    settings page, stored under static/uploads/company/) when set;
    otherwise falls back to the default static/images/logo.png.
    """
    import base64
    import os

    app_dir = os.path.dirname(os.path.dirname(__file__))

    if profile and profile.logo_filename:
        custom_path = os.path.join(app_dir, "static", "uploads", "company", profile.logo_filename)
        if os.path.isfile(custom_path):
            with open(custom_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("ascii")
            ext = os.path.splitext(profile.logo_filename)[1].lstrip(".").lower() or "png"
            return f"data:image/{ext};base64,{encoded}"

    if not hasattr(_logo_data_uri, "_default_cached"):
        default_path = os.path.join(app_dir, "static", "images", "logo.png")
        with open(default_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        _logo_data_uri._default_cached = f"data:image/png;base64,{encoded}"
    return _logo_data_uri._default_cached


def _header_font_data_uri():
    """Bank Gothic font (static/fonts/BankGothic.ttf) as a base64 data: URI,
    so the PDF header can use it via @font-face. Playwright's header frame
    can't load files or /static URLs, so the font bytes are embedded
    directly -- same technique as _logo_data_uri().
    """
    import base64
    import os

    if not hasattr(_header_font_data_uri, "_cached"):
        app_dir = os.path.dirname(os.path.dirname(__file__))
        font_path = os.path.join(app_dir, "static", "fonts", "BankGothic.ttf")
        if os.path.isfile(font_path):
            with open(font_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("ascii")
            _header_font_data_uri._cached = f"data:font/ttf;base64,{encoded}"
        else:
            _header_font_data_uri._cached = ""  # falls back to Arial in template
    return _header_font_data_uri._cached


def _pdf_header_footer_html(profile):
    """Renders the letterhead header and footer as self-contained HTML
    fragments for Playwright's native header_template/footer_template PDF
    options -- the correct, Chromium-supported way to get a letterhead
    that repeats on every page, with working Page X of Y numbers.

    (The previous approach -- CSS `position: running()` / `element()` in
    the page body -- is a WeasyPrint-only extension. Under Chromium it
    silently only shows on page 1, which is why multi-page PDFs were
    missing their header/footer on every page after the first, and why
    the page-number counter showed as static "Page 0 of 0" text instead
    of counting.)
    """
    header_html = render_template(
        "inspections/_pdf_header.html", profile=profile, logo_data_uri=_logo_data_uri(profile),
        font_data_uri=_header_font_data_uri(),
    )
    footer_html = render_template("inspections/_pdf_footer.html", profile=profile)
    return header_html, footer_html


def _companies_json():
    """Every company as a plain dict, for the type-to-search Company box on
    the Form 9/10/11/PSV/Centrifuge forms -- embedded once as JSON so
    filtering happens client-side per keystroke with no extra requests."""
    return [
        {
            "id": c.id,
            "name": c.name,
            "occupier_name": c.occupier_name or "",
            "address": c.address or "",
            "reg_no": c.registration_no or "",
            "license_no": c.license_no or "",
        }
        for c in Company.query.order_by(Company.name.asc()).all()
    ]


def _remember_company_details(company_id, data):
    """After saving a report, copy occupier / address / registration / license
    into the Company record -- ONLY into fields that are still empty there --
    so the next form for that company auto-fills them when it is selected."""
    company = Company.query.get(company_id) if company_id else None
    if not company:
        return
    for attr, key in (("occupier_name", "occupier_name"), ("address", "address"),
                      ("registration_no", "reg_no"), ("license_no", "license_no")):
        value = (data.get(key) or "").strip()
        if value and not getattr(company, attr):
            setattr(company, attr, value)

# Every field that lives inside InspectionReport.data for a Form 9
# (Hoists/Lifts examination certificate). report_no and report_date are
# stored as real columns (for search/sort/uniqueness); everything else
# lives in this JSON blob. Add new keys here to add a field to the form --
# no DB migration needed since `data` is a single JSON column.
FORM9_FIELDS = [
    "reg_no", "license_no", "nic_code", "occupier_name", "address",
    "hoist_description", "hoist_make", "tag_no", "capacity", "no_of_floors",
    "location", "construction_date",
    "good_mechanical_order", "enclosure_findings", "landing_gates_findings",
    "interlock_findings", "other_gate_fastenings", "cage_platform_findings",
    "overrunning_devices", "suspension_ropes", "safety_gear", "brakes",
    "worm_gearing", "electrical_equipment", "other_parts",
    "inaccessible_parts", "max_safe_load", "repairs_required", "remarks",
    "certification_date", "next_exam_date", "reminder_date",
    "competent_person_name", "competent_person_title",
    "competent_person_authority", "competent_person_no",
    "competent_person_state", "certification_text",
]

# Fields on the Form 9 that default to "Satisfactory" when left blank --
# these are the pass/fail findings for each part inspected.
FORM9_FINDING_FIELDS = [
    "good_mechanical_order", "enclosure_findings", "landing_gates_findings",
    "interlock_findings", "other_gate_fastenings", "cage_platform_findings",
    "overrunning_devices", "suspension_ropes", "safety_gear", "brakes",
    "worm_gearing", "electrical_equipment", "other_parts",
]


def _parse_custom_fields(raw):
    """Parses the JSON list posted by the "Manage Custom Fields" modal into a
    clean list of {"name", "value"} dicts (stored in report.data["custom_fields"]
    and printed inside item 3 of the Form 9 PDF). Bad/empty input -> []."""
    import json
    try:
        items = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    result = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:100]
        value = str(item.get("value") or "").strip()[:200]
        if name or value:
            result.append({"name": name, "value": value})
    return result


def _parse_date(value):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _add_months(d, months):
    """Calendar-month add, clamping the day (31 Aug + 6 months -> 28/29 Feb)."""
    import calendar
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


def _fill_exam_dates(data, form_type=None):
    """Server-side safety net for the dates the browser normally auto-fills.
    Only fills values that were left blank -- never overwrites what the user typed.

      Next Exam / Next NDT Due = Certification Date + 6 months - 1 day
      Next Hydro Due (Form 11) = Hydraulic exam date (or Certification Date) + 2 years - 1 day
      Reminder Date            = Next Exam / NDT Due - 1 month
    """
    from datetime import timedelta
    cert = data.get("certification_date")
    if not cert:
        return
    try:
        cert_d = _parse_date(cert)
    except (ValueError, TypeError):
        return
    due_d = _add_months(cert_d, 6) - timedelta(days=1)
    if not data.get("next_exam_date"):
        data["next_exam_date"] = due_d.isoformat()
    if form_type == "form11":
        if not data.get("next_ndt_date"):
            data["next_ndt_date"] = due_d.isoformat()
        if not data.get("next_hydro_date"):
            base = cert_d
            if data.get("last_hydraulic_exam") == "Date" and data.get("last_hydraulic_exam_date"):
                try:
                    base = _parse_date(data["last_hydraulic_exam_date"])
                except (ValueError, TypeError):
                    base = cert_d
            data["next_hydro_date"] = (_add_months(base, 24) - timedelta(days=1)).isoformat()
    if not data.get("reminder_date"):
        data["reminder_date"] = _add_months(due_d, -1).isoformat()


def _profile_image_uri(filename):
    """Signature / stamp uploaded on the My Company page, as a base64 data: URI
    (the PDF renders in a separate process, so /static URLs would not resolve)."""
    import base64
    import mimetypes
    import os
    if not filename:
        return ""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "static", "uploads", "company", filename)
    if not os.path.isfile(path):
        return ""
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def _sign_stamp_uris():
    profile = CompanyProfile.query.first()
    if not profile:
        return "", ""
    return (_profile_image_uri(profile.signature_filename),
            _profile_image_uri(profile.stamp_filename))


# ---------------------------------------------------------------------------
# "Copy Previous Form Data" -- JSON search used by the modal on every
# inspection form (Form 9, 10, 11, PSV, Centrifuge). Returns recent past
# reports of the same form type, optionally filtered by ?q= (matches
# report no, company, occupier, tag no, make, description ... anything
# inside the report), so the user can click one and auto-fill the form.
# ---------------------------------------------------------------------------
@inspections_bp.route("/previous/<any(form9, form10, form11, psv, centrifuge):form_type>")
@login_required
def previous_reports(form_type):
    from flask import jsonify
    q = (request.args.get("q") or "").strip().lower()
    exclude_id = request.args.get("exclude", type=int)
    company_id = request.args.get("company_id", type=int)
    limit = max(1, min(request.args.get("limit", 30, type=int), 30))

    if request.args.get("any_form") == "1":
        query = InspectionReport.query          # used to borrow occupier/address/reg/license from any form
    else:
        query = InspectionReport.query.filter_by(form_type=form_type)
    if company_id:
        query = query.filter(InspectionReport.company_id == company_id)
    if exclude_id:
        query = query.filter(InspectionReport.id != exclude_id)
    query = query.order_by(InspectionReport.report_date.desc(), InspectionReport.id.desc())

    results = []
    for r in query.limit(500).all():
        data = r.data or {}
        company_name = r.company.name if r.company else ""
        haystack = " ".join(
            [r.report_no or "", company_name, r.occupier_name or ""]
            + [str(v) for v in data.values() if isinstance(v, (str, int, float))]
        ).lower()
        if q and q not in haystack:
            continue
        results.append({
            "id": r.id,
            "report_no": r.report_no or "",
            "date": r.report_date.strftime("%d/%m/%Y") if r.report_date else "",
            "company_id": r.company_id or "",
            "company_name": company_name or r.occupier_name or "",
            "tag_no": data.get("tag_no", ""),
            "make": data.get("hoist_make") or data.get("make") or "",
            "description": (data.get("hoist_description") or data.get("equipment_description")
                            or data.get("vessel_description") or data.get("machine_name_description") or ""),
            "data": data,
        })
        if len(results) >= limit:
            break
    return jsonify(results)


@inspections_bp.route("/form9")
@login_required
def form9_list():
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = InspectionReport.query.filter_by(form_type="form9")
    if search:
        query = query.filter(
            db.or_(
                InspectionReport.report_no.ilike(f"%{search}%"),
                InspectionReport.occupier_name.ilike(f"%{search}%"),
            )
        )
    query = query.order_by(InspectionReport.report_date.desc().nullslast(), InspectionReport.id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    reports = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "inspections/form9_list.html",
        reports=reports, search=search, per_page=per_page,
        page=page, total_pages=total_pages, total=total, start=start, end=end,
    )


@inspections_bp.route("/form9/new", methods=["GET", "POST"])
@login_required
def form9_new():
    return _form9_save(report=None)


@inspections_bp.route("/form9/<int:report_id>/edit", methods=["GET", "POST"])
@login_required
def form9_edit(report_id):
    report = InspectionReport.query.filter_by(id=report_id, form_type="form9").first_or_404()
    return _form9_save(report=report)


def _form9_save(report):
    if request.method == "POST":
        report_no = request.form.get("report_no", "").strip()
        if not report_no:
            flash("Report No. is required.", "danger")
            return redirect(request.url)

        existing = InspectionReport.query.filter(
            InspectionReport.report_no == report_no,
            InspectionReport.form_type == "form9",
        )
        if report:
            existing = existing.filter(InspectionReport.id != report.id)
        if existing.first():
            flash("A Form 9 report with this Report No. already exists.", "danger")
            return redirect(request.url)

        data = {}
        for field in FORM9_FIELDS:
            value = request.form.get(field, "").strip()
            if not value and field in FORM9_FINDING_FIELDS:
                value = "Satisfactory"
            data[field] = value
        data["custom_fields"] = _parse_custom_fields(request.form.get("custom_fields"))
        _fill_exam_dates(data)
        if report is not None and (report.data or {}).get("reminder_sent_on") \
                and (report.data or {}).get("reminder_date") == data.get("reminder_date"):
            data["reminder_sent_on"] = report.data["reminder_sent_on"]

        if report is None:
            report = InspectionReport(form_type="form9", created_by_id=current_user.id)
            db.session.add(report)

        company_id = request.form.get("company_id", type=int)
        report.company_id = company_id or None

        report.report_no = report_no
        report.report_date = _parse_date(request.form.get("date")) or date.today()
        report.occupier_name = data.get("occupier_name")
        report.data = data
        _remember_company_details(report.company_id, data)
        db.session.commit()

        flash("Form 9 report saved.", "success")
        return redirect(url_for("inspections.form9_list"))

    return render_template(
        "inspections/form9_form.html",
        form_type="form9",
        report=report,
        data=(report.data if report else {}),
        today=date.today().isoformat(),
        suggested_report_no=(report.report_no if report else _next_report_no()),
        companies=Company.query.order_by(Company.name.asc()).all(),
        companies_json=_companies_json(),
    )


@inspections_bp.route("/form9/<int:report_id>/delete", methods=["POST"])
@login_required
def form9_delete(report_id):
    report = InspectionReport.query.filter_by(id=report_id, form_type="form9").first_or_404()
    db.session.delete(report)
    db.session.commit()
    flash("Form 9 report deleted.", "success")
    return redirect(url_for("inspections.form9_list"))


@inspections_bp.route("/form9/<int:report_id>/pdf")
@login_required
def form9_pdf(report_id):
    report = InspectionReport.query.filter_by(id=report_id, form_type="form9").first_or_404()

    html = render_template("inspections/form9_pdf.html", report=report, data=report.data)
    profile = CompanyProfile.query.first()
    header_html, footer_html = _pdf_header_footer_html(profile)
    try:
        pdf_bytes = _html_to_pdf_bytes(html, header_html=header_html, footer_html=footer_html, fit_one_page=True)
    except RuntimeError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("inspections.form9_list"))

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'inline; filename="Form9-{report.report_no}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Generic engine for Form 10, Form 11, PSV and Centrifuge.
#
# Form 9 above is hand-built (one route per action). These four forms share
# the exact same InspectionReport/JSON pattern, so instead of copy-pasting
# Form 9's routes four more times, every field for every one of these forms
# is declared once in FORMS_CONFIG below, and a single set of routes
# (parameterized by <form_type>) drives list/new/edit/delete/pdf/renew for
# whichever of the four the URL asks for. Add a fifth form the same way:
# just add one more entry to FORMS_CONFIG and to the `any(...)` converter
# in each @inspections_bp.route() below -- no new view functions needed.
# ---------------------------------------------------------------------------

FORMS_CONFIG = {
    "form10": {
        "label": "Form 10 - Lifting Machines / Cranes",
        "rule": "(Prescribed under Rule 60)",
        # "Manage Custom Fields" button is shown right after this section
        "custom_fields_section": "3) Equipment Identification",
        "required": ["certification_date", "next_exam_date", "reminder_date",
                     "competent_person_name", "competent_person_no"],
        "auto_dates": True,
        "options": {
            "heat_treatment_details": ["Not applicable"],
        },
        "sections": [
            ("Report Identifiers", [
                ("reg_no", "Registration No.", "text", None),
                ("license_no", "License No.", "text", None),
                ("nic_code", "NIC Code", "text", None),
            ]),
            ("3) Equipment Identification", [
                ("equipment_description", "Equipment Description", "text", None),
                ("serial_no", "Serial No.", "text", None),
                ("make", "Make", "text", None),
                ("capacity", "Capacity", "text", None),
                ("location", "Location", "text", None),
            ]),
            ("4) Date of First Use", [
                ("first_use_date", "Date when the lifting machine, chain, rope or lifting tackle was first used in the factory", "text", None),
            ]),
            ("5) Examination Details", [
                ("examination_details_text", "Date of each examination made under section 29(1)(a)(iii) and by whom it was carried out", "textarea", None),
                ("last_exam_date", "Last Exam Date", "date", None),
                ("examination_date", "Examination Date", "text", None),
                ("examined_by", "Examined By", "text", None),
            ]),
            ("6) Certificate of Test (Rule 60/1)", [
                ("certificate_details", "Date and number of the certificate relating to any test and examination made under sub-rule (1) of rule 60, with the name of the person who issued it", "textarea", None),
            ]),
            ("7) Annealing / Heat Treatment (Rule 60/5)", [
                ("heat_treatment_details", "Date of annealing or other heat treatment (sub-rule 5 of rule 60) and by whom it was carried out", "text", None),
            ]),
            ("8) Particulars of Defects & Safe Working Load", [
                ("defects_found", "Particulars of any defect found at any such examination or after annealing and affecting the safe working load, and the steps taken to remedy it", "textarea", None),
            ]),
            ("Statutory Certification", [
                ("certification_text", "Certification Text", "textarea",
                 "I certify that on {date} the lifting machine, chain, rope or lifting tackle described above was thoroughly examined and that the above is a true report of the result of the examination."),
            ]),
            ("Next Examination Dates", [
                ("certification_date", "Certification Date", "date", "__today__"),
                ("next_exam_date", "Next Exam Date", "date", None),
                ("reminder_date", "Reminder Date", "date", None),
            ]),
            ("Certifying Authority", [
                ("competent_person_name", "Competent Person Name", "text", None),
                ("competent_person_title", "Title", "text", "Competent Person Declared by Director"),
                ("competent_person_authority", "Authority", "text", "Industrial Safety & Health Gujarat State"),
                ("competent_person_no", "Competent Person No.", "text", None),
                ("competent_person_state", "State", "text", "Gujarat"),
            ]),
        ],
        "finding_fields": [],
    },
    "form11": {
        "label": "Form 11 - Pressure Vessel",
        "rule": "(Prescribed under Rule 61 of GFR 1963)",
        "custom_fields_section": "3) Vessel Details",
        # Unit labels shown inside the input boxes (key -> unit text).
        "units": {
            "capacity": "KL", "temperature": "\u00b0C", "pressure": "kg/cm\u00b2",
            "safe_working_pressure": "KG/CM\u00b2", "recommended_swp": "KG/CM\u00b2",
            "reduced_working_pressure": "KG/CM\u00b2", "calculated_swp": "KG/CM\u00b2",
            "thickness_shell": "mm", "thickness_jacket": "mm", "thickness_limpet": "mm",
            "thickness_pipeline": "mm", "ultrasonic_shell": "mm", "ultrasonic_jacket": "mm",
            "ultrasonic_limpet": "mm", "ultrasonic_pipeline": "mm",
        },
        # "Last ... exam" selectors: First Time / By Manufacturer / Date.
        # Choosing "Date" reveals <key>_date and <key>_comment (hidden otherwise).
        "exam_types": {
            "last_external_exam": "7a) Last external examination",
            "last_hydraulic_exam": "7c) Last hydraulic examination",
            "last_ultrasonic_test": "7d) Last ultrasonic / NDT",
        },
        # Must be filled before saving (checked in the browser AND on the server).
        "required": ["certification_date", "next_ndt_date", "next_hydro_date",
                     "reminder_date", "competent_person_name", "competent_person_no"],
        # Date fields that are auto-calculated from the Certification Date.
        "auto_dates": True,
        "options": {
            "last_internal_exam": ["Satisfactory", "Good", "Not Applicable", "Nil"],
            "lagging_removed": ["Lagging provided", "Lagging Partial Removed", "Not Applicable"],
            "vessel_condition": ["Satisfactory", "Good", "Not Applicable", "Nil"],
            "piping_condition": ["Satisfactory", "Good", "Not Applicable", "Nil"],
            "pressure_gauges_condition": ["Available", "Not Applicable"],
            "safety_valve_condition": ["Available", "Not Applicable"],
            "stop_valve_condition": ["Available", "Not Applicable"],
            "reducing_valve_condition": ["Available", "Not Applicable"],
            "additional_safety_valve_condition": ["Available", "Not Applicable"],
            "other_devices_condition": ["Available", "Not Applicable"],
            "repairs_period": ["Available", "Not Applicable", "Nil"],
            "reduced_working_pressure": ["Not Applicable", "NA", "Nil"],
            "calculated_swp": ["Available", "Not Applicable", "Nil"],
        },
        "sections": [
            ("Report Identifiers", [
                ("reg_no", "Registration No.", "text", None),
                ("license_no", "License No.", "text", None),
                ("nic_code", "NIC Code", "text", None),
            ]),
            ("3) Vessel Details", [
                ("vessel_name", "Vessel Name", "text", None),
                ("vessel_description", "Vessel Description", "text", None),
                ("tag_no", "Tag No.", "text", None),
                ("capacity", "Capacity", "text", None),
                ("location", "Location", "text", None),
            ]),
            ("4) Name and Address of Manufacturers", [
                ("manufacturer", "Manufacturer", "text", None),
            ]),
            ("5) Nature of Process (including temperature and pressure parameters)", [
                ("nature_of_process", "Nature of Process", "text", None),
                ("temperature", "Temperature", "text", None),
                ("pressure", "Pressure", "text", None),
            ]),
            ("6) Particulars of Pressure Vessel or Plant", [
                ("date_of_construction", "Date of Construction", "date", None),
                ("safe_working_pressure", "Safe Working Pressure", "text", None),
                ("thickness_shell", "Shell Thickness", "text", None),
                ("thickness_jacket", "Jacket Thickness", "text", None),
                ("thickness_limpet", "Limpet Thickness", "text", None),
                ("thickness_pipeline", "Pipeline Thickness", "text", None),
            ]),
            ("7) Date of", [
                ("first_use_date", "First Use Date", "date", None),
                ("last_exam_date", "Last Exam Date", "date", None),
                ("last_external_exam", "Last External Exam", "text", None),
                ("last_external_exam_date", "Last External Exam Date", "date", None),
                ("last_external_exam_comment", "Last External Exam Comment", "text", None),
                ("last_internal_exam", "Last Internal Exam", "text", "Not Applicable"),
                ("last_hydraulic_exam", "Last Hydraulic Exam", "text", None),
                ("last_hydraulic_exam_date", "Last Hydraulic Exam Date", "date", None),
                ("last_hydraulic_exam_comment", "Last Hydraulic Exam Comment", "text", None),
                ("last_ultrasonic_test", "Last Ultrasonic Test", "text", None),
                ("last_ultrasonic_test_date", "Last Ultrasonic Test Date", "date", None),
                ("last_ultrasonic_test_comment", "Last Ultrasonic Test Comment", "text", None),
                ("last_hydro_test_date", "Last Hydro Test Date", "date", None),
            ]),
            ("8) Whether lagging was removed for purposes of examination", [
                ("lagging_removed", "Lagging Removed", "text", None),
            ]),
            ("9) Description of examinations carried out and findings", [
                ("external_findings", "External Findings", "textarea", None),
                ("internal_findings", "Internal Findings", "textarea", None),
                ("ultrasonic_findings", "Ultrasonic Findings", "textarea", None),
                ("ultrasonic_shell", "Ultrasonic - Shell", "text", None),
                ("ultrasonic_jacket", "Ultrasonic - Jacket", "text", None),
                ("ultrasonic_limpet", "Ultrasonic - Limpet", "text", None),
                ("ultrasonic_pipeline", "Ultrasonic - Pipeline", "text", None),
            ]),
            ("10) Condition of Pressure Plants", [
                ("vessel_condition", "Vessel Condition", "text", None),
                ("piping_condition", "Piping Condition", "text", None),
            ]),
            ("11) Condition of Fitting and Appliances", [
                ("pressure_gauges_condition", "Pressure Gauges Condition", "text", None),
                ("safety_valve_condition", "Safety Valve Condition", "text", None),
                ("stop_valve_condition", "Stop Valve Condition", "text", None),
                ("reducing_valve_condition", "Reducing Valve Condition", "text", None),
                ("additional_safety_valve_condition", "Additional Safety Valve Condition", "text", None),
                ("other_devices_condition", "Other Devices Condition", "text", None),
            ]),
            ("12) Safe Working Pressure Recommended After Examinations", [
                ("recommended_swp", "Recommended SWP", "text", None),
            ]),
            ("13) Repairs & Other Conditions", [
                ("repairs_required", "Repairs Required", "textarea", None),
                ("repairs_period", "Repairs Period", "text", None),
                ("safety_conditions", "Safety Conditions", "textarea",
                 "Client is advised for regular Testing of Safety Valve & Keeping Records Every Year."),
            ]),
            ("14) Reduced Working Pressure Pending Repairs", [
                ("reduced_working_pressure", "Reduced Working Pressure", "text", "Not Applicable"),
            ]),
            ("15) Safe Working Pressure Calculated (Sub-Rule 8, thin walled vessel or plant)", [
                ("calculated_swp", "Calculated SWP", "text", None),
            ]),
            ("16) Other Observations", [
                ("other_observations", "Other Observations", "textarea",
                 "Pressure Gauge and Safety Valve should be checked periodically to ensure correct functioning and safe operation. Proper inspection and maintenance records should be maintained for compliance and safety purposes."),
            ]),
            ("Statutory Certification", [
                ("certification_text", "Certification Text", "textarea",
                 "I certify that on {date} the pressure vessel or plant described above was thorough cleaned and (so far, its construction permits) made accessible for thorough examination and for such tests as were necessary for thorough examination and that on the said date I thoroughly examination this pressure vessel or plants, including its fitting and that above is a true report of examination."),
            ]),
            ("Next Examination Dates", [
                ("certification_date", "Certification Date", "date", "__today__"),
                ("next_ndt_date", "Next NDT Date", "date", None),
                ("next_hydro_date", "Next Hydro Date", "date", None),
                ("next_exam_date", "Next Exam Date", "date", None),
                ("reminder_date", "Reminder Date", "date", None),
            ]),
            ("Certifying Authority", [
                ("competent_person_name", "Competent Person Name", "text", None),
                ("competent_person_title", "Title", "text", "Competent Person Declared by Director"),
                ("competent_person_authority", "Authority", "text", "Industrial Safety & Health Gujarat State"),
                ("competent_person_no", "Competent Person No.", "text", None),
                ("competent_person_state", "State", "text", "Gujarat"),
            ]),
        ],
        "finding_fields": [
            "vessel_condition", "piping_condition", "pressure_gauges_condition",
            "safety_valve_condition", "stop_valve_condition", "reducing_valve_condition",
            "additional_safety_valve_condition", "other_devices_condition",
        ],
    },
    "psv": {
        "label": "PSV - Pressure Safety Valve Certificate",
        "rule": "Pressure Safety Valve Test Certificate",
        "units": {"set_pressure": "kg/cm\u00b2", "temperature": "\u00b0C", "humidity": "%",
                  "operated_at": "kg/cm\u00b2"},
        "required": ["next_exam_date", "reminder_date", "competent_person_name", "competent_person_no"],
        "options": {},
        "sections": [
            ("3) Fitted Location", [
                ("fitted_location", "Fitted Location", "text", None),
            ]),
            ("4) Year of Mfg.", [
                ("year_of_mfg", "Year of Manufacture", "text", None),
            ]),
            ("5) Make", [
                ("make", "Make", "text", None),
            ]),
            ("6) Set Pressure", [
                ("set_pressure", "Set Pressure", "text", None),
            ]),
            ("7) Temperature", [
                ("temperature", "Temperature", "text", None),
            ]),
            ("8) Relative Humidity", [
                ("humidity", "Humidity", "text", None),
            ]),
            ("9) Testing Standard Used & Traceability", [
                ("cal_std_used", "Cal. Std. Used", "text", None),
                ("cal_accuracy", "Accuracy", "text", None),
                ("cal_make", "Make", "text", None),
                ("cal_by", "Calibrated By", "text", None),
                ("cal_equip_used", "Equip. Used", "text", None),
                ("cal_cert_no", "Certificate No.", "text", None),
                ("cal_model", "Model", "text", None),
                ("cal_date", "Calibration Date", "date", "__today__"),
                ("cal_sr_no", "Sr. No.", "text", None),
                ("cal_due", "Calibration Due", "date", "__plus1y__"),
                ("cal_range", "Range", "text", None),
                ("cal_least_count", "Least Count", "text", None),
                ("cal_traceability", "Traceability", "text", None),
            ]),
            ("10) Testing Result", [
                ("operated_at", "Operated At (Kg/Cm²)", "text", None),
            ]),
            ("11) Next Examination Dates", [
                ("next_exam_date", "Next Exam Date", "date", "__plus1y__"),
                ("reminder_date", "Reminder Date", "date", "__plus1y_m1__"),
            ]),
            ("12) Certifying Authority", [
                ("competent_person_name", "Competent Person Name", "text", "Amit H. Jethwa"),
                ("competent_person_no", "Competent Person No.", "text", "AM-3177303"),
                ("competent_person_state", "State", "text", None),
                ("competent_person_title", "Title", "text", "CHARTER ENGINEER"),
            ]),
        ],
        "finding_fields": [],
    },
    "centrifuge": {
        "label": "Centrifuge Machine Test Report",
        "rule": "Centrifuge Machine Test Report",
        "custom_fields_section": "3) Machine Identity",
        "units": {"basket_speed": "RPM", "operating_speed_stamped": "RPM"},
        "required": ["certification_date", "next_exam_date", "reminder_date",
                     "competent_person_name", "competent_person_no"],
        "auto_dates": True,
        "options": {
            "condition_of_machine": ["Satisfactory", "Not Applicable"],
            "interlock_top_cover": ["Satisfactory", "Not Applicable"],
            "interlock_mechanical_breaker": ["Satisfactory", "Not Applicable"],
            "earthing_provided": ["Satisfactory", "Not Applicable"],
        },
        "sections": [
            ("Report Identifiers", [
                ("license_no", "License No.", "text", None),
            ]),
            ("3) Machine Identity", [
                ("machine_name_description", "Machine Name/Description", "text", None),
                ("machine_name", "Machine Name", "text", None),
                ("tag_no", "Tag No.", "text", None),
                ("capacity", "Capacity", "text", None),
                ("location", "Location", "text", None),
            ]),
            ("4) Manufacturer", [
                ("manufacturer_name_address", "Manufacturer Name & Address", "textarea", None),
            ]),
            ("5) Machine Particulars", [
                ("date_of_construction", "Date of Construction", "date", None),
                ("machine_size", "Machine Size", "text", None),
            ]),
            ("6) Machine Condition", [
                ("condition_of_machine", "Condition of Machine", "text", "Satisfactory"),
            ]),
            ("7) Inter Locking System", [
                ("interlock_top_cover", "Interlock - Top Cover", "text", "Satisfactory"),
                ("interlock_mechanical_breaker", "Interlock - Mechanical Breaker", "text", "Satisfactory"),
            ]),
            ("8) Earthing Provided", [
                ("earthing_provided", "Earthing Provided", "text", "Satisfactory"),
            ]),
            ("9) Basket Speed", [
                ("basket_speed", "Basket Speed", "text", None),
                ("operating_speed_stamped", "Operating Speed (Stamped)", "text", None),
            ]),
            ("10) Last Examination", [
                ("last_exam_date", "Last Exam Date", "date", None),
            ]),
            ("11) Remarks", [
                ("remarks", "Remarks", "textarea", None),
            ]),
            ("12) Date of Examination", [
                ("date_of_examination", "Date of Examination", "date", "__today__"),
            ]),
            ("13) Defects & Remedies", [
                ("defects_and_remedies", "Defects & Remedies", "textarea", None),
            ]),
            ("Statutory Certification", [
                ("certification_text", "Certification Text", "textarea",
                 "I certify that on {date} I have thoroughly examined the centrifuge machine described above and the above is a correct report of the result of such examination."),
            ]),
            ("Next Examination Dates", [
                ("certification_date", "Certification Date", "date", "__today__"),
                ("next_exam_date", "Next Exam Date", "date", None),
                ("next_ndt_date", "Next NDT Date", "date", None),
                ("next_hydro_date", "Next Hydro Date", "date", None),
                ("reminder_date", "Reminder Date", "date", None),
            ]),
            ("Certifying Authority", [
                ("competent_person_name", "Competent Person Name", "text", None),
                ("competent_person_no", "Competent Person No.", "text", None),
                ("competent_person_state", "State", "text", "Gujarat"),
                ("competent_person_title", "Title", "text",
                 "Competent Person Declared by Director Industrial Safety & Health"),
            ]),
        ],
        "finding_fields": [
            "condition_of_machine", "interlock_top_cover",
            "interlock_mechanical_breaker", "earthing_provided",
        ],
    },
}


def _resolve_default(default):
    """Static defaults pass through; the __today__ / __plus1y__ /
    __plus1y_m1__ markers are turned into ISO dates (today, today+1 year-1 day,
    and one month before that) -- same defaults the new DISH software uses."""
    if not default or not isinstance(default, str) or not default.startswith("__"):
        return default
    today = date.today()
    if default == "__today__":
        return today.isoformat()
    due = _add_months(today, 12) - timedelta(days=1)
    if default == "__plus1y__":
        return due.isoformat()
    if default == "__plus1y_m1__":
        return _add_months(due, -1).isoformat()
    return default


def _fields_for(form_type):
    """Flat list of (key, label, input_type, default) across every section."""
    fields = []
    for _section_name, section_fields in FORMS_CONFIG[form_type]["sections"]:
        fields.extend(section_fields)
    return fields


def _company_code(company_name):
    """First letter of each word of the company name, upper-cased
    ("M/S MISTRY ENGINEERING" -> "MME"). No company -> "TEMP"."""
    letters = []
    for word in (company_name or "").split():
        if word.upper().strip(".,") in ("M/S", "M/S.", "MS", "MESSRS"):
            continue  # "M/S" is a prefix, not part of the company name
        m = re.search(r"[A-Za-z0-9]", word)
        if m:
            letters.append(m.group(0).upper())
    return "".join(letters) or "TEMP"


def _next_report_no(company_id=None):
    """GHSEA/<year>/<company code or TEMP>/<n>. n = highest existing number
    for that prefix + 1. Counted across ALL form types because
    InspectionReport.report_no is unique table-wide."""
    company = Company.query.get(company_id) if company_id else None
    prefix = f"GHSEA/{date.today().year}/{_company_code(company.name if company else '')}/"
    highest = 0
    rows = InspectionReport.query.filter(InspectionReport.report_no.like(prefix + "%")).all()
    for r in rows:
        tail = r.report_no[len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1}"


def _suggest_report_no(form_type=None, company_id=None):
    return _next_report_no(company_id)


@inspections_bp.route("/next-report-no/<any(form9, form10, form11, psv, centrifuge):form_type>")
@login_required
def next_report_no(form_type):
    from flask import jsonify
    return jsonify({"next_no": _next_report_no(request.args.get("company_id", type=int))})


@inspections_bp.route("/renumber-old-reports")
@login_required
def renumber_old_reports():
    """One-time: converts old numbers (FORM11/2026/0001 ...) to the GHSEA format.
    Open /inspections/renumber-old-reports to preview, add ?apply=1 to save."""
    if not current_user.is_admin:
        return "Admin only", 403
    apply = request.args.get("apply") == "1"

    highest = {}
    for r in InspectionReport.query.filter(InspectionReport.report_no.like("GHSEA/%")).all():
        prefix, _, tail = r.report_no.rpartition("/")
        if tail.isdigit():
            highest[prefix + "/"] = max(highest.get(prefix + "/", 0), int(tail))

    old = (InspectionReport.query
           .filter(~InspectionReport.report_no.like("GHSEA/%"))
           .order_by(InspectionReport.report_date.asc(), InspectionReport.id.asc())
           .all())

    lines = []
    for r in old:
        company = Company.query.get(r.company_id) if r.company_id else None
        year = r.report_date.year if r.report_date else (r.created_at.year if r.created_at else date.today().year)
        prefix = f"GHSEA/{year}/{_company_code(company.name if company else '')}/"
        highest[prefix] = highest.get(prefix, 0) + 1
        new_no = f"{prefix}{highest[prefix]}"
        lines.append(f"[{r.form_type}] {r.report_no}  ->  {new_no}")
        if apply:
            r.report_no = new_no

    if apply:
        db.session.commit()
        lines.append(f"\nDone. {len(old)} report(s) renumbered.")
    else:
        lines.append(f"\nPreview only. Add ?apply=1 to the URL to save {len(old)} change(s).")
    return make_response("\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"})


@inspections_bp.route("/<any(form10, form11, psv, centrifuge):form_type>/")
@login_required
def generic_list(form_type):
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = InspectionReport.query.filter_by(form_type=form_type)
    if search:
        query = query.filter(
            db.or_(
                InspectionReport.report_no.ilike(f"%{search}%"),
                InspectionReport.occupier_name.ilike(f"%{search}%"),
            )
        )
    query = query.order_by(InspectionReport.report_date.desc().nullslast(), InspectionReport.id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    reports = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "inspections/generic_list.html",
        form_type=form_type, config=FORMS_CONFIG[form_type],
        reports=reports, search=search, per_page=per_page,
        page=page, total_pages=total_pages, total=total, start=start, end=end,
    )


@inspections_bp.route("/<any(form10, form11, psv, centrifuge):form_type>/new", methods=["GET", "POST"])
@login_required
def generic_new(form_type):
    return _generic_save(form_type, report=None)


@inspections_bp.route("/<any(form10, form11, psv, centrifuge):form_type>/<int:report_id>/edit", methods=["GET", "POST"])
@login_required
def generic_edit(form_type, report_id):
    report = InspectionReport.query.filter_by(id=report_id, form_type=form_type).first_or_404()
    return _generic_save(form_type, report=report)


def _generic_save(form_type, report):
    config = FORMS_CONFIG[form_type]
    fields = _fields_for(form_type)
    finding_fields = set(config["finding_fields"])
    labels = {key: label for key, label, _t, _d in fields}
    posted = None            # set when validation fails, so the form is re-shown with what was typed

    if request.method == "POST":
        report_no = request.form.get("report_no", "").strip()
        errors = []
        if not report_no:
            errors.append("Report No.")

        existing = InspectionReport.query.filter(
            InspectionReport.report_no == report_no,
            InspectionReport.form_type == form_type,
        )
        if report:
            existing = existing.filter(InspectionReport.id != report.id)
        if report_no and existing.first():
            flash(f"A {config['label']} report with this Report No. already exists.", "danger")
            errors.append("unique Report No.")

        data = {}
        for key, _label, _input_type, default in fields:
            value = request.form.get(key, "").strip()
            if not value and key in finding_fields:
                value = "Satisfactory"
            if not value and default:
                value = _resolve_default(default)
            data[key] = value
        if config.get("custom_fields_section"):
            data["custom_fields"] = _parse_custom_fields(request.form.get("custom_fields"))

        # "Date" selectors: the date/comment boxes only mean something when
        # "Date" is chosen -- drop stale values otherwise.
        for key in (config.get("exam_types") or {}):
            if data.get(key) != "Date":
                data[f"{key}_date"] = ""
                if key != "last_hydraulic_exam":     # hydraulic comment doubles as item 9(c) text
                    data[f"{key}_comment"] = ""

        _fill_exam_dates(data, form_type)
        data["show_sign_stamp"] = request.form.get("show_sign_stamp") == "1"

        # Required-field check (after auto-fill, so calculated dates count).
        missing = [labels.get(k, k) for k in config.get("required", []) if not data.get(k)]
        if missing:
            flash("Please fill in: " + ", ".join(missing), "danger")
            errors.extend(missing)

        data["occupier_name"] = request.form.get("occupier_name", "").strip()
        data["address"] = request.form.get("address", "").strip()

        if not errors:
            if report is not None and (report.data or {}).get("reminder_sent_on") \
                    and (report.data or {}).get("reminder_date") == data.get("reminder_date"):
                data["reminder_sent_on"] = report.data["reminder_sent_on"]

            if report is None:
                report = InspectionReport(form_type=form_type, created_by_id=current_user.id)
                db.session.add(report)

            company_id = request.form.get("company_id", type=int)
            report.company_id = company_id or None
            report.report_no = report_no
            report.report_date = _parse_date(request.form.get("date")) or date.today()
            report.occupier_name = data.get("occupier_name")
            report.data = data
            _remember_company_details(report.company_id, data)
            db.session.commit()

            flash(f"{config['label']} report saved.", "success")
            return redirect(url_for("inspections.generic_list", form_type=form_type))

        posted = {"report_no": report_no, "date": request.form.get("date", ""),
                  "company_id": request.form.get("company_id", type=int), "data": data}

    shown_data = posted["data"] if posted else (report.data if report else {})
    return render_template(
        "inspections/generic_form.html",
        form_type=form_type, config=config,
        report=report, data=shown_data, posted=posted,
        today=date.today().isoformat(),
        suggested_report_no=(posted["report_no"] if posted else
                             (report.report_no if report else _suggest_report_no(form_type))),
        field_defaults={key: (_resolve_default(default) or "") for key, _l, _t, default in fields},
        companies=Company.query.order_by(Company.name.asc()).all(),
        companies_json=_companies_json(),
    )


@inspections_bp.route("/<any(form10, form11, psv, centrifuge):form_type>/<int:report_id>/delete", methods=["POST"])
@login_required
def generic_delete(form_type, report_id):
    report = InspectionReport.query.filter_by(id=report_id, form_type=form_type).first_or_404()
    db.session.delete(report)
    db.session.commit()
    flash(f"{FORMS_CONFIG[form_type]['label']} report deleted.", "success")
    return redirect(url_for("inspections.generic_list", form_type=form_type))


@inspections_bp.route("/<any(form10, form11, psv, centrifuge):form_type>/<int:report_id>/renew", methods=["POST"])
@login_required
def generic_renew(form_type, report_id):
    """Copies an existing report's data into a brand-new report (new Report
    No., new id), links it back via renewed_from_id, then sends the user
    straight to editing the copy so they can update dates/findings before
    saving it for real."""
    original = InspectionReport.query.filter_by(id=report_id, form_type=form_type).first_or_404()

    new_report = InspectionReport(
        form_type=form_type,
        created_by_id=current_user.id,
        report_no=_next_report_no(original.company_id),
        report_date=date.today(),
        occupier_name=original.occupier_name,
        company_id=original.company_id,
        renewed_from_id=original.id,
        data=dict(original.data or {}),
    )
    db.session.add(new_report)
    db.session.commit()

    flash(f"Created renewal {new_report.report_no} from {original.report_no}. Review and save.", "success")
    return redirect(url_for("inspections.generic_edit", form_type=form_type, report_id=new_report.id))


@inspections_bp.route("/<any(form10, form11, psv, centrifuge):form_type>/<int:report_id>/pdf")
@login_required
def generic_pdf(form_type, report_id):
    report = InspectionReport.query.filter_by(id=report_id, form_type=form_type).first_or_404()
    config = FORMS_CONFIG[form_type]

    signature_uri, stamp_uri = _sign_stamp_uris()
    html = render_template(
        "inspections/generic_pdf.html",
        report=report, data=report.data, config=config, form_type=form_type,
        signature_uri=signature_uri, stamp_uri=stamp_uri,
    )
    profile = CompanyProfile.query.first()
    header_html, footer_html = _pdf_header_footer_html(profile)
    try:
        pdf_bytes = _html_to_pdf_bytes(html, header_html=header_html, footer_html=footer_html, fit_one_page=True)
    except RuntimeError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("inspections.generic_list", form_type=form_type))

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'inline; filename="{form_type.upper()}-{report.report_no}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Phase 3 -- Compliance Dashboard: expiry tracker across every form type
# (Form 9/10/11, PSV, Centrifuge), a 6-month volume chart, and a recent
# activity feed. Pure read-only aggregation over InspectionReport -- no new
# DB columns, no schema changes needed.
# ---------------------------------------------------------------------------

# Which InspectionReport.data key(s) hold the "next due" date for each form
# type, in priority order (first one present wins) -- mirrors the fields
# actually offered in FORMS_CONFIG / FORM9_FIELDS above.
DUE_DATE_KEYS = {
    "form9": ["next_exam_date"],
    "form10": ["next_exam_date"],
    "form11": ["next_exam_date", "next_ndt_date", "next_hydro_date"],
    "psv": ["next_exam_date"],
    "centrifuge": ["next_exam_date", "next_ndt_date", "next_hydro_date"],
}

FORM_LABELS = {
    "form9": "Form 9", "form10": "Form 10", "form11": "Form 11",
    "psv": "PSV", "centrifuge": "Centrifuge",
}


def _report_due_date(report):
    """First present due-date field for this report's form type, parsed."""
    for key in DUE_DATE_KEYS.get(report.form_type, []):
        raw = (report.data or {}).get(key)
        if raw:
            try:
                return _parse_date(raw)
            except (ValueError, TypeError):
                continue
    return None


@inspections_bp.route("/dashboard")
@login_required
def dashboard():
    reports = InspectionReport.query.all()
    today = date.today()

    # A report counts as "Renewed" once a newer report points back at it
    # via renewed_from_id -- no report_no regex needed, the DB already knows.
    renewed_source_ids = {r.renewed_from_id for r in reports if r.renewed_from_id}

    rows = []
    for r in reports:
        due_date = _report_due_date(r)
        days_left = (due_date - today).days if due_date else None

        if r.id in renewed_source_ids:
            status = "renewed"
        elif days_left is None:
            status = "valid"
        elif days_left < 0:
            status = "expired"
        elif days_left < 15:
            status = "critical"
        elif days_left < 30:
            status = "reminder"
        else:
            status = "valid"

        rows.append({
            "report": r,
            "form_type": r.form_type,
            "form_label": FORM_LABELS.get(r.form_type, r.form_type),
            "due_date": due_date,
            "days_left": days_left,
            "status": status,
            "company_name": (r.company.name if r.company_id and r.company else None) or r.occupier_name,
        })

    stats = {
        "total_companies": Company.query.count(),
        "total_reports": len(rows),
        "critical": sum(1 for x in rows if x["status"] == "critical"),
        "reminder": sum(1 for x in rows if x["status"] == "reminder"),
        "valid": sum(1 for x in rows if x["status"] in ("valid", "renewed")),
        "expired": sum(1 for x in rows if x["status"] == "expired"),
    }

    # Expiry tracker table: anything due within 30 days (or already expired),
    # soonest first. Renewed/valid rows are reachable via the filter pills
    # in the template (client-side, no reload needed).
    expiring = sorted(
        [x for x in rows if x["days_left"] is not None and x["days_left"] <= 30],
        key=lambda x: x["days_left"],
    )

    # Recent activity: last 20 touched reports, newest first.
    recent = sorted(
        rows,
        key=lambda x: x["report"].updated_at or x["report"].created_at or datetime.min,
        reverse=True,
    )[:20]
    for x in recent:
        created = x["report"].created_at
        updated = x["report"].updated_at
        x["edited"] = bool(created and updated and (updated - created).total_seconds() > 2)

    # 6-month report volume, oldest -> newest, by created_at.
    months = []
    y, m = today.year, today.month
    for _ in range(6):
        months.insert(0, {"year": y, "month": m, "label": date(y, m, 1).strftime("%b"), "count": 0})
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    for x in rows:
        created = x["report"].created_at
        if not created:
            continue
        for bucket in months:
            if created.year == bucket["year"] and created.month == bucket["month"]:
                bucket["count"] += 1
                break
    max_count = max([b["count"] for b in months] + [1])
    for b in months:
        b["pct"] = round((b["count"] / max_count) * 100)

    return render_template(
        "inspections/dashboard.html",
        stats=stats,
        expiring=expiring,
        recent=recent,
        chart_months=months,
    )


# ---------------------------------------------------------------------------
# Phase 4 -- Bulk PDF export (merge every matching report into one PDF, one
# page per report) and Excel registry export (branded .xlsx), for Form 9
# and the generic four (Form 10/11, PSV, Centrifuge). Both respect the same
# "q" search box already used on each list page, so "export what I'm
# looking at" works exactly like it does on screen.
# ---------------------------------------------------------------------------

# Curated identification columns per form type for the Excel registry --
# deliberately a subset of FORMS_CONFIG (which has 20-40+ fields per form)
# so the exported sheet stays readable rather than dumping every field.
EXCEL_COLUMNS = {
    "form9": [
        ("Location", "location"),
        ("Hoist Description", "hoist_description"),
        ("Make", "hoist_make"),
        ("Tag No.", "tag_no"),
        ("Capacity", "capacity"),
    ],
    "form10": [
        ("Location", "location"),
        ("Equipment Description", "equipment_description"),
        ("Make", "make"),
        ("Capacity", "capacity"),
    ],
    "form11": [
        ("Vessel Name", "vessel_name"),
        ("Tag No.", "tag_no"),
        ("Capacity", "capacity"),
        ("Location", "location"),
        ("Safe Working Pressure", "safe_working_pressure"),
        ("Last Hydro Test Date", "last_hydro_test_date"),
    ],
    "psv": [
        ("Fitted Location", "fitted_location"),
        ("Make", "make"),
        ("Set Pressure", "set_pressure"),
        ("Calibration Due", "cal_due"),
    ],
    "centrifuge": [
        ("Machine Name", "machine_name"),
        ("Tag No.", "tag_no"),
        ("Capacity", "capacity"),
        ("Location", "location"),
    ],
}


def _export_query(form_type, search):
    """Same filter used by the list pages, but unpaginated -- export acts on
    everything the search box currently matches, not just the visible page."""
    query = InspectionReport.query.filter_by(form_type=form_type)
    if search:
        query = query.filter(
            db.or_(
                InspectionReport.report_no.ilike(f"%{search}%"),
                InspectionReport.occupier_name.ilike(f"%{search}%"),
            )
        )
    return query.order_by(InspectionReport.report_no.asc()).all()


def _status_and_days(report, renewed_source_ids):
    """Same bucketing rules as the compliance dashboard (Phase 3) -- kept as
    a small standalone helper here so the registry's Status/Days columns
    always agree with what the dashboard shows."""
    due_date = _report_due_date(report)
    today = date.today()
    days_left = (due_date - today).days if due_date else None

    if report.id in renewed_source_ids:
        status = "Renewed"
    elif days_left is None:
        status = "Valid"
    elif days_left < 0:
        status = "Expired"
    elif days_left < 15:
        status = "Critical"
    elif days_left < 30:
        status = "Reminder"
    else:
        status = "Valid"
    return status, due_date, days_left


def _renewed_source_ids():
    return {
        r.renewed_from_id
        for r in InspectionReport.query.with_entities(InspectionReport.renewed_from_id).all()
        if r.renewed_from_id
    }


def _company_display_name(report):
    return (report.company.name if report.company_id and report.company else None) or report.occupier_name or "-"


# ---------------------------------------------------------------------------
# Excel registry export -- ONE layout for every form in the Inspections menu
# (Form 9, Form 10, Form 11, PSV, Centrifuge), matching the "ECS index F-10"
# PDF:
#   * consultant name / client name / report title at the top
#   * one grey group row per equipment type
#   * Sr. No. restarts at 1 inside every group
#   * columns: Sr. No. | Certificate No. | Description | Tag No. | Capacity |
#              Location | Insp. Date | Due Date
#   * one sheet per client company, A4 portrait, header row repeats on every
#     printed page, "Page X of Y" footer
#
# To change what a form shows, edit only its entry in EXCEL_LAYOUT below.
#   group     : data keys tried in order for the grey group heading
#   group_default : heading used when none of those keys has a value
#   desc      : lines of the Description cell as (label, [keys], always_show)
#               always_show=True prints "Label : -" even when empty
#   tag / cap / loc : data keys tried in order for those columns
#   insp / due: data keys tried in order for the two date columns
# ---------------------------------------------------------------------------

EXCEL_LAYOUT = {
    "form9": {
        "title": "INSPECTION & TESTING REPORT IN FORM NO. 9 AS PER GFR",
        "group": ["hoist_description"], "group_default": "Hoist / Lift",
        "desc": [("Make", ["hoist_make"], True),
                 ("No. of Floors", ["no_of_floors"], False),
                 ("Max Safe Load", ["max_safe_load"], False)],
        "tag": ["tag_no"], "cap": ["capacity"], "cap_header": "Capacity",
        "loc": ["location"],
        "insp": ["certification_date"],
        "due": ["next_exam_date"],
    },
    "form10": {
        "title": "INSPECTION & TESTING REPORT IN FORM NO. 10 AS PER GFR",
        "group": ["equipment_description"], "group_default": "Other",
        # model / engine_no / vehicle_reg_no / span only print if you have
        # added those fields to the Form 10 config (they are optional).
        "desc": [("Make", ["make"], True),
                 ("Model", ["model"], False),
                 ("Engine No.", ["engine_no"], False),
                 ("Reg. No.", ["vehicle_reg_no"], False),
                 ("Span", ["span"], False)],
        "tag": ["serial_no"], "cap": ["capacity"], "cap_header": "Capacity",
        "loc": ["location"],
        "insp": ["examination_date", "last_exam_date", "certification_date"],
        "due": ["next_exam_date"],
    },
    "form11": {
        "title": "INSPECTION & TESTING REPORT IN FORM NO. 11 AS PER GFR",
        "group": ["vessel_name"], "group_default": "Pressure Vessel",
        "desc": [("Description", ["vessel_description"], False),
                 ("Make", ["manufacturer"], False),
                 ("SWP", ["safe_working_pressure"], False)],
        "tag": ["tag_no"], "cap": ["capacity"], "cap_header": "Capacity",
        "loc": ["location"],
        "insp": ["certification_date", "last_exam_date"],
        "due": ["next_exam_date", "next_ndt_date", "next_hydro_date"],
    },
    "psv": {
        "title": "INSPECTION & TESTING REPORT OF PRESSURE SAFETY VALVES",
        "group": [], "group_default": "Pressure Safety Valve",
        "desc": [("Make", ["make"], True),
                 ("Year of Mfg", ["year_of_mfg"], False)],
        "tag": ["tag_no"], "cap": ["set_pressure"], "cap_header": "Set Pressure",
        "loc": ["fitted_location"],
        "insp": ["cal_date", "certification_date"],
        "due": ["next_exam_date", "cal_due"],
    },
    "centrifuge": {
        "title": "INSPECTION & TESTING REPORT OF CENTRIFUGE MACHINES",
        "group": ["machine_name"], "group_default": "Centrifuge Machine",
        "desc": [("Description", ["machine_name_description"], False),
                 ("Size", ["machine_size"], False),
                 ("Speed", ["operating_speed_stamped", "basket_speed"], False)],
        "tag": ["tag_no"], "cap": ["capacity"], "cap_header": "Capacity",
        "loc": ["location"],
        "insp": ["date_of_examination", "last_exam_date", "certification_date"],
        "due": ["next_exam_date", "next_ndt_date", "next_hydro_date"],
    },
}

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y")


def _first_value(data, keys):
    """First non-empty value among data[key] for key in keys, as a string."""
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _any_date(value):
    """Turn whatever is stored (ISO string, dd/mm/yyyy text, date object)
    into a date, or None if it isn't a recognisable date."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _date_cell(value):
    """Returns (cell_value, is_real_date). Real dates are written as true
    Excel dates (sortable/filterable); anything unparseable stays as text."""
    parsed = _any_date(value)
    if parsed:
        return parsed, True
    return (str(value).strip() if value else ""), False


def _description_cell(data, layout):
    """Multi-line Description cell, e.g.  Make : ACE / Model : AF30E."""
    lines = []
    for label, keys, always in layout["desc"]:
        value = _first_value(data, keys)
        if label == "Make" and value.lower().startswith("make"):
            lines.append(value)                    # user already typed "Make : ..."
        elif value:
            lines.append(f"{label} : {value}")
        elif always:
            lines.append(f"{label} : -")
    return "\n".join(lines)


def _wrapped_lines(text, width_chars):
    """Rough count of visual lines a cell needs, so row heights look right."""
    total = 0
    for part in str(text or "").split("\n"):
        total += max(1, -(-len(part) // max(int(width_chars), 1)))
    return total


def _build_excel_registry(form_type, reports, label):
    from flask import current_app

    layout = EXCEL_LAYOUT.get(form_type) or EXCEL_LAYOUT["form10"]

    profile = CompanyProfile.query.first()
    consultant = (
        (profile.legal_name if profile and profile.legal_name else None)
        or current_app.config.get("COMPANY_NAME", "HSE Project")
    )

    thin = Side(style="thin", color="000000")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    grey = PatternFill("solid", start_color="F2F2F2", end_color="F2F2F2")

    headers = ["Sr.\nNo.", "Certificate No.", "Description", "Tag No.",
               layout["cap_header"], "Location", "Insp. Date", "Due Date"]
    widths = [7, 17, 30, 24, 11, 20, 13, 13]
    ncols = len(headers)

    # ---- one sheet per client company (first-appearance order) ----------
    by_company = {}
    for r in sorted(reports, key=lambda x: x.id):
        by_company.setdefault(_company_display_name(r), []).append(r)

    wb = Workbook()
    wb.remove(wb.active)
    used_titles = set()

    for company_name, company_reports in by_company.items():
        title = re.sub(r'[\\/?*\[\]:]', '-', company_name)[:31] or "Registry"
        base, n = title, 2
        while title.lower() in used_titles:          # sheet names must be unique
            title = f"{base[:28]}-{n}"
            n += 1
        used_titles.add(title.lower())
        ws = wb.create_sheet(title)

        for c, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(c)].width = w

        # ---- title block ------------------------------------------------
        titles = [
            (consultant.upper(), Font(bold=True, size=12)),
            (company_name, Font(size=11, underline="single")),
            (layout["title"], Font(size=10)),
        ]
        for i, (text, font) in enumerate(titles, start=1):
            ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=ncols)
            cell = ws.cell(row=i, column=1, value=text)
            cell.font = font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.row_dimensions[i].height = 20

        # ---- header row -------------------------------------------------
        header_row = 5
        for c, h in enumerate(headers, start=1):
            cell = ws.cell(row=header_row, column=c, value=h)
            cell.font = Font(size=10)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = box
        ws.row_dimensions[header_row].height = 32

        # ---- group by equipment type (first-appearance order) ----------
        groups = {}
        for r in company_reports:
            name = _first_value(r.data or {}, layout["group"]) or layout["group_default"]
            groups.setdefault(name, []).append(r)

        row_idx = header_row + 1
        for group_name, group_reports in groups.items():
            ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=ncols)
            gcell = ws.cell(row=row_idx, column=1, value=group_name)
            gcell.font = Font(size=10)
            gcell.fill = grey
            gcell.alignment = Alignment(horizontal="left", vertical="center")
            for c in range(1, ncols + 1):
                ws.cell(row=row_idx, column=c).border = box
            ws.row_dimensions[row_idx].height = 18
            row_idx += 1

            for sr, r in enumerate(group_reports, start=1):
                data = r.data or {}
                description = _description_cell(data, layout)
                tag = _first_value(data, layout["tag"])
                capacity = _first_value(data, layout["cap"])
                location = _first_value(data, layout["loc"])

                insp_raw = _first_value(data, layout["insp"]) or r.report_date
                insp_val, insp_is_date = _date_cell(insp_raw)
                due_val, due_is_date = _date_cell(_first_value(data, layout["due"]))

                values = [sr, r.report_no, description, tag, capacity, location,
                          insp_val, due_val]
                for c, value in enumerate(values, start=1):
                    cell = ws.cell(row=row_idx, column=c, value=value)
                    cell.font = Font(size=10)
                    cell.border = box
                    cell.alignment = Alignment(
                        horizontal="center" if (c == 1 or (c >= 5 and c != 6)) else "left",
                        vertical="top", wrap_text=True,
                    )
                if insp_is_date:
                    ws.cell(row=row_idx, column=7).number_format = "DD/MM/YYYY"
                if due_is_date:
                    ws.cell(row=row_idx, column=8).number_format = "DD/MM/YYYY"

                lines = max(
                    _wrapped_lines(description, widths[2] - 2),
                    _wrapped_lines(tag, widths[3] - 2),
                    _wrapped_lines(location, widths[5] - 2),
                    _wrapped_lines(capacity, widths[4] - 2),
                    _wrapped_lines(r.report_no, widths[1] - 2),
                )
                ws.row_dimensions[row_idx].height = max(18, 13.5 * lines + 4)
                row_idx += 1

        # ---- print / view setup ----------------------------------------
        ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
        ws.print_title_rows = f"{header_row}:{header_row}"
        ws.page_setup.orientation = "portrait"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.page_margins.left = ws.page_margins.right = 0.4
        ws.page_margins.top = ws.page_margins.bottom = 0.5
        ws.oddFooter.center.text = "Page &P of &N"
        ws.sheet_view.showGridLines = False

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


@inspections_bp.route("/form9/export/pdf")
@login_required
def form9_export_pdf():
    search = request.args.get("q", "").strip()
    reports = _export_query("form9", search)
    if not reports:
        flash("No Form 9 reports match the current search to export.", "danger")
        return redirect(url_for("inspections.form9_list", q=search))

    html = render_template("inspections/form9_bulk_pdf.html", reports=reports)
    profile = CompanyProfile.query.first()
    header_html, footer_html = _pdf_header_footer_html(profile)
    try:
        pdf_bytes = _html_to_pdf_bytes(html, header_html=header_html, footer_html=footer_html)
    except RuntimeError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("inspections.form9_list", q=search))

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'inline; filename="Form9-Registry-{date.today().isoformat()}.pdf"'
    return response


@inspections_bp.route("/form9/export/excel")
@login_required
def form9_export_excel():
    search = request.args.get("q", "").strip()
    reports = _export_query("form9", search)
    if not reports:
        flash("No Form 9 reports match the current search to export.", "danger")
        return redirect(url_for("inspections.form9_list", q=search))

    buffer = _build_excel_registry("form9", reports, "Form 9")
    response = make_response(buffer.read())
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    response.headers["Content-Disposition"] = f'attachment; filename="Form9-Registry-{date.today().isoformat()}.xlsx"'
    return response


@inspections_bp.route("/<any(form10, form11, psv, centrifuge):form_type>/export/pdf")
@login_required
def generic_export_pdf(form_type):
    search = request.args.get("q", "").strip()
    reports = _export_query(form_type, search)
    config = FORMS_CONFIG[form_type]
    if not reports:
        flash(f"No {config['label']} reports match the current search to export.", "danger")
        return redirect(url_for("inspections.generic_list", form_type=form_type, q=search))

    signature_uri, stamp_uri = _sign_stamp_uris()
    html = render_template("inspections/generic_bulk_pdf.html", reports=reports, config=config, form_type=form_type,
                           signature_uri=signature_uri, stamp_uri=stamp_uri)
    profile = CompanyProfile.query.first()
    header_html, footer_html = _pdf_header_footer_html(profile)
    try:
        pdf_bytes = _html_to_pdf_bytes(html, header_html=header_html, footer_html=footer_html)
    except RuntimeError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("inspections.generic_list", form_type=form_type, q=search))

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'inline; filename="{form_type.upper()}-Registry-{date.today().isoformat()}.pdf"'
    return response


@inspections_bp.route("/<any(form10, form11, psv, centrifuge):form_type>/export/excel")
@login_required
def generic_export_excel(form_type):
    search = request.args.get("q", "").strip()
    reports = _export_query(form_type, search)
    config = FORMS_CONFIG[form_type]
    if not reports:
        flash(f"No {config['label']} reports match the current search to export.", "danger")
        return redirect(url_for("inspections.generic_list", form_type=form_type, q=search))

    buffer = _build_excel_registry(form_type, reports, config["label"])
    response = make_response(buffer.read())
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    response.headers["Content-Disposition"] = f'attachment; filename="{form_type.upper()}-Registry-{date.today().isoformat()}.xlsx"'
    return response