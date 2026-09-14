from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from app import db
from app.models import Company, InspectionReport

companies_bp = Blueprint("companies", __name__, url_prefix="/companies")

# Every statutory form type that can be linked to a company. Add new keys
# here (label + list endpoint) as new form modules get built, so the
# per-company report view and any "which forms exist" dropdown pick them
# up automatically.
FORM_TYPE_LABELS = {
    "form9": "Form 9 - Hoists/Lifts",
    "form10": "Form 10 - Lifting Machines/Cranes",
    "form11": "Form 11 - Pressure Vessel",
    "psv": "PSV Certificate",
    "centrifuge": "Centrifuge Report",
}

# Edit/PDF endpoint names per form type. Form 9 uses its own hand-built
# endpoints; Form 10/11/PSV/Centrifuge share the generic engine in
# inspections/routes.py, so they all point at the same generic_edit/
# generic_pdf endpoints (form_type is passed at the call site below).
FORM_TYPE_ENDPOINTS = {
    "form9": {"edit": "inspections.form9_edit", "pdf": "inspections.form9_pdf"},
    "form10": {"edit": "inspections.generic_edit", "pdf": "inspections.generic_pdf"},
    "form11": {"edit": "inspections.generic_edit", "pdf": "inspections.generic_pdf"},
    "psv": {"edit": "inspections.generic_edit", "pdf": "inspections.generic_pdf"},
    "centrifuge": {"edit": "inspections.generic_edit", "pdf": "inspections.generic_pdf"},
}


SORT_COLUMNS = {
    "name": Company.name,
    "occupier": Company.occupier_name,
    "created": Company.created_at,
}


@companies_bp.route("/")
@login_required
def list_companies():
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "name")
    direction = request.args.get("dir", "asc")

    query = Company.query
    if search:
        query = query.filter(
            db.or_(
                Company.name.ilike(f"%{search}%"),
                Company.occupier_name.ilike(f"%{search}%"),
                Company.registration_no.ilike(f"%{search}%"),
                Company.license_no.ilike(f"%{search}%"),
            )
        )

    sort_col = SORT_COLUMNS.get(sort, Company.name)
    query = query.order_by(sort_col.desc() if direction == "desc" else sort_col.asc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    companies = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    # Registry-wide stats for the top cards -- deliberately unfiltered
    # (whole network), unlike `total` above which reflects the search box.
    total_companies = Company.query.count()
    all_reports = InspectionReport.query.all()
    total_reports_count = len(all_reports)

    if total_reports_count:
        # Import kept local to avoid a module-load-order cycle between the
        # inspections and companies blueprints -- this is the same
        # due-date resolver the Compliance Dashboard uses, so this figure
        # always agrees with what that page reports.
        from app.inspections.routes import _report_due_date
        overdue = sum(
            1 for r in all_reports
            if (_report_due_date(r) or date.max) < date.today()
        )
        compliance_pct = round((total_reports_count - overdue) / total_reports_count * 100)
    else:
        compliance_pct = 100

    return render_template(
        "companies/list.html",
        companies=companies, search=search, per_page=per_page,
        page=page, total_pages=total_pages, total=total, start=start, end=end,
        sort=sort, direction=direction,
        total_companies=total_companies, total_reports_count=total_reports_count,
        compliance_pct=compliance_pct,
    )


@companies_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_company():
    return _save_company(company=None)


@companies_bp.route("/<int:company_id>/edit", methods=["GET", "POST"])
@login_required
def edit_company(company_id):
    company = Company.query.get_or_404(company_id)
    return _save_company(company=company)


def _save_company(company):
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Company name is required.", "danger")
            return redirect(request.url)

        if company is None:
            company = Company()
            db.session.add(company)

        company.name = name
        company.address = request.form.get("address", "").strip()
        company.occupier_name = request.form.get("occupier_name", "").strip()
        company.registration_no = request.form.get("registration_no", "").strip()
        company.license_no = request.form.get("license_no", "").strip()
        company.company_code = request.form.get("company_code", "").strip()
        db.session.commit()

        flash("Company saved.", "success")
        return redirect(url_for("companies.list_companies"))

    return render_template("companies/form.html", company=company)


@companies_bp.route("/<int:company_id>/delete", methods=["POST"])
@login_required
def delete_company(company_id):
    company = Company.query.get_or_404(company_id)
    db.session.delete(company)
    db.session.commit()
    flash("Company deleted.", "success")
    return redirect(url_for("companies.list_companies"))


@companies_bp.route("/<int:company_id>/reports")
@login_required
def company_reports(company_id):
    company = Company.query.get_or_404(company_id)

    search = request.args.get("q", "").strip()
    year = request.args.get("year", "").strip()
    form_type = request.args.get("form_type", "").strip()

    query = InspectionReport.query.filter_by(company_id=company.id)
    if search:
        query = query.filter(InspectionReport.report_no.ilike(f"%{search}%"))
    if year:
        query = query.filter(db.extract("year", InspectionReport.report_date) == int(year))
    if form_type:
        query = query.filter_by(form_type=form_type)
    query = query.order_by(InspectionReport.report_date.desc().nullslast())

    reports = query.all()

    # Group reports by form_type for the "grouped sections" view, in the
    # fixed order defined by FORM_TYPE_LABELS above.
    grouped = {ft: [] for ft in FORM_TYPE_LABELS}
    for r in reports:
        grouped.setdefault(r.form_type, []).append(r)

    # Years available for this company, for the year-filter dropdown.
    years = sorted({
        r.report_date.year for r in company.reports if r.report_date
    }, reverse=True)

    return render_template(
        "companies/report_view.html",
        company=company, grouped=grouped, form_type_labels=FORM_TYPE_LABELS,
        form_type_endpoints=FORM_TYPE_ENDPOINTS,
        search=search, year=year, form_type=form_type, years=years,
        total_reports=len(reports),
    )


@companies_bp.route("/reports/bulk-delete", methods=["POST"])
@login_required
def bulk_delete_reports():
    """Deletes several InspectionReport rows at once from the company report
    view's bulk-action checkboxes, then returns to that company's page."""
    report_ids = request.form.getlist("report_ids")
    company_id = request.form.get("company_id", type=int)

    if report_ids:
        InspectionReport.query.filter(InspectionReport.id.in_(report_ids)).delete(
            synchronize_session=False
        )
        db.session.commit()
        flash(f"Deleted {len(report_ids)} report(s).", "success")
    else:
        flash("No reports were selected.", "info")

    return redirect(url_for("companies.company_reports", company_id=company_id))