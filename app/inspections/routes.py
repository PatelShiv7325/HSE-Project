from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response
from flask_login import login_required, current_user
from app import db
from app.models import InspectionReport

inspections_bp = Blueprint("inspections", __name__, url_prefix="/inspections")

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


def _parse_date(value):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


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

        if report is None:
            report = InspectionReport(form_type="form9", created_by_id=current_user.id)
            db.session.add(report)

        report.report_no = report_no
        report.report_date = _parse_date(request.form.get("date")) or date.today()
        report.occupier_name = data.get("occupier_name")
        report.data = data
        db.session.commit()

        flash("Form 9 report saved.", "success")
        return redirect(url_for("inspections.form9_list"))

    return render_template(
        "inspections/form9_form.html",
        report=report,
        data=(report.data if report else {}),
        today=date.today().isoformat(),
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

    # WeasyPrint is an optional dependency -- imported here (not at module
    # level) so the rest of the app still runs even before `pip install`
    # has been re-run to pick it up from requirements.txt.
    try:
        from weasyprint import HTML
    except ImportError:
        flash("PDF export needs the 'weasyprint' package -- run: pip install -r requirements.txt", "danger")
        return redirect(url_for("inspections.form9_list"))

    html = render_template("inspections/form9_pdf.html", report=report, data=report.data)
    pdf_bytes = HTML(string=html, base_url=request.url_root).write_pdf()

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'inline; filename="Form9-{report.report_no}.pdf"'
    return response
