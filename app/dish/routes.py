from datetime import datetime, timedelta
import os
from uuid import uuid4
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    current_app, send_from_directory,
)
from flask_login import login_required
from werkzeug.utils import secure_filename
from app import db
from app.models import DishCase, DishApplication, Lead, User, SiteVisit

dish_bp = Blueprint("dish", __name__, url_prefix="/dish")

# ---------------------------------------------------------------------------
# DISH Applications config
# ---------------------------------------------------------------------------
APP_TYPE_LABELS = {"map": "MAP", "stability": "STAB", "license": "LICENSE"}
SUBTYPE_LABELS = {
    "new": "NEW", "revised": "REV", "revised_with_extension": "REV. WITH EXT.", "renew": "RENEW",
}
DUE_DAYS = {"map": 45, "stability": 30, "license": 45}

MAP_APP_STATUS_LABELS = {"online_pending": "Online Application Pending", "offline_pending": "Offline Application Pending", "submitted": "Submitted"}
LIAISONING_LABELS = {"regional_forward_pending": "Regional Office Forward Pending", "regional_approval_pending": "Regional Office Approval Pending", "query": "Inward Letter Query", "hard_copy_pending": "Hard Copy Upload Pending", "done": "Done"}
STABILITY_LABELS = {"pending": "Pending", "done": "Done"}
LICENSE_LABELS = {
    "online_pending": "Online Application Pending",
    "offline_pending": "Offline Application Pending",
    "query": "Inward Letter Query",
    "submitted": "Submitted",
}


def application_status_label(app):
    """Live status text for an application, pulled from its case's current pipeline status."""
    case = app.case
    if app.application_type == "map":
        if case.map_application_status != "submitted":
            return "MAP: " + MAP_APP_STATUS_LABELS.get(case.map_application_status, case.map_application_status or "-")
        return "MAP: " + LIAISONING_LABELS.get(case.liaisoning_status, case.liaisoning_status or "-")
    if app.application_type == "stability":
        return "STABILITY: " + STABILITY_LABELS.get(case.stability_status, case.stability_status or "-")
    if app.application_type == "license":
        return "LICENSE: " + LICENSE_LABELS.get(case.license_status, case.license_status or "-")
    return "-"


def application_status_pill(app):
    """CSS pill class for the live status (reuses the existing .status-pill variants)."""
    case = app.case
    if app.application_type == "map":
        if case.map_application_status != "submitted":
            return "pending"
        if case.liaisoning_status == "query":
            return "overdue"
        if case.liaisoning_status == "done":
            return "done"
        return "in_progress"
    if app.application_type == "stability":
        return "done" if case.stability_status == "done" else "pending"
    if app.application_type == "license":
        if case.license_status == "submitted":
            return "done"
        if case.license_status == "query":
            return "overdue"
        if case.license_status == "offline_pending":
            return "in_progress"
        return "pending"
    return "pending"


def generate_application_no(application_type, subtype):
    year = datetime.utcnow().year
    count = DishApplication.query.filter_by(application_type=application_type, subtype=subtype).count() + 1
    return f"{APP_TYPE_LABELS[application_type]}/{SUBTYPE_LABELS[subtype]}/{year}/{count:03d}"


@dish_bp.route("/assign")
@login_required
def assign():
    cases = (
        DishCase.query.join(Lead, DishCase.lead_id == Lead.id)
        .order_by(DishCase.id.desc()).all()
    )
    cased_lead_ids = [c.lead_id for c in cases]
    # Leads in the DISH department that don't have a case yet, for the
    # "Create Case" dropdown below — DishCase rows were never created
    # anywhere before this, so this page was always empty.
    available_leads_query = Lead.query.filter(Lead.department.has(name="DISH"))
    if cased_lead_ids:
        available_leads_query = available_leads_query.filter(~Lead.id.in_(cased_lead_ids))

    return render_template(
        "dish/assign.html",
        cases=cases,
        users=User.query.filter_by(is_active_flag=True).all(),
        available_leads=available_leads_query.all(),
    )


@dish_bp.route("/assign/create", methods=["POST"])
@login_required
def create_case():
    lead_id = request.form.get("lead_id")
    if not lead_id:
        flash("Please select a lead.", "danger")
        return redirect(url_for("dish.assign"))

    if DishCase.query.filter_by(lead_id=lead_id).first():
        flash("This lead already has a DISH case.", "danger")
        return redirect(url_for("dish.assign"))

    db.session.add(DishCase(lead_id=lead_id))
    db.session.commit()
    flash("DISH case created.", "success")
    return redirect(url_for("dish.assign"))


@dish_bp.route("/assign/<int:case_id>/update", methods=["POST"])
@login_required
def update_assignment(case_id):
    case = DishCase.query.get_or_404(case_id)
    for field in [
        "documentation_user_id", "stability_user_id", "drafting_user_id",
        "online_application_user_id", "liaisoning_map_user_id",
        "license_user_id", "liaisoning_license_user_id",
    ]:
        value = request.form.get(field)
        setattr(case, field, value or None)
    deadline = request.form.get("drafting_deadline")
    case.drafting_deadline = deadline or None
    db.session.commit()
    flash("Assignment updated.", "success")
    return redirect(url_for("dish.assign"))


@dish_bp.route("/form")
@login_required
def form():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = DishCase.query.join(Lead, DishCase.lead_id == Lead.id)
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                Lead.client_name.ilike(f"%{search}%"),
                Lead.contact_no.ilike(f"%{search}%"),
            )
        )
    if status:
        query = query.filter(DishCase.form_status == status)
    query = query.order_by(DishCase.id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    cases = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "dish/form.html",
        cases=cases, search=search, status=status, per_page=per_page,
        page=page, total_pages=total_pages, total=total, start=start, end=end,
    )


@dish_bp.route("/form/<int:case_id>/edit", methods=["GET", "POST"])
@login_required
def form_edit(case_id):
    case = DishCase.query.get_or_404(case_id)
    if request.method == "POST":
        new_status = request.form.get("form_status")
        if new_status in ("pending", "done"):
            case.form_status = new_status
            db.session.commit()
            flash("Form status updated.", "success")
        return redirect(url_for("dish.form"))
    return render_template("dish/form_edit.html", case=case)    


@dish_bp.route("/documents")
@login_required
def documents():
    return render_template("dish/coming_soon.html", title="Documents")


DRAFTING_STATUS_LABELS = {
    "file_upload_pending": "File Upload Pending",
    "internal_qc_pending": "Internal QC Pending",
    "company_approval_pending": "Company Approval Pending",
    "qc_rejected": "QC Rejected",
    "company_rejected": "Company Rejected",
    "done": "Complete",
}
# Pill CSS class (from theme.css .status-pill variants) per drafting_status.
DRAFTING_STATUS_PILL = {
    "file_upload_pending": "pending",
    "internal_qc_pending": "assigned",
    "company_approval_pending": "assigned",
    "qc_rejected": "overdue",
    "company_rejected": "overdue",
    "done": "done",
}
# What the green "advance" action moves a case to, from its current status.
# Rejected states are intentionally excluded -- those need a human to open
# Upload and decide what to do next, not a one-click bump.
DRAFTING_ADVANCE_TO = {
    "file_upload_pending": "internal_qc_pending",
    "internal_qc_pending": "company_approval_pending",
    "company_approval_pending": "done",
}


def _save_drafting_file(file_obj, case_id):
    """Saves an uploaded drafting file under static/uploads/drafting and
    returns the stored filename, or None if no file was chosen."""
    if not file_obj or not file_obj.filename:
        return None
    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "drafting")
    os.makedirs(upload_dir, exist_ok=True)
    filename = secure_filename(file_obj.filename)
    stored_name = f"drafting_{case_id}_{uuid4().hex}_{filename}"
    file_obj.save(os.path.join(upload_dir, stored_name))
    return stored_name


@dish_bp.route("/drafting")
@login_required
def drafting():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = DishCase.query.join(Lead, DishCase.lead_id == Lead.id)
    if search:
        query = query.filter(Lead.company_name.ilike(f"%{search}%"))
    if status in DRAFTING_STATUS_LABELS:
        query = query.filter(DishCase.drafting_status == status)
    query = query.order_by(DishCase.id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    cases = query.offset((page - 1) * per_page).limit(per_page).all()
    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    # Most recent site visit per lead, batched into one query instead of
    # N+1 -- keyed by lead_id so the template can look each one up directly.
    lead_ids = [c.lead_id for c in cases]
    site_visit_engineer_by_lead = {}
    if lead_ids:
        visits = (
            SiteVisit.query.filter(SiteVisit.lead_id.in_(lead_ids))
            .order_by(SiteVisit.id.desc()).all()
        )
        for v in visits:
            if v.lead_id not in site_visit_engineer_by_lead and v.engineer and v.engineer.user:
                site_visit_engineer_by_lead[v.lead_id] = v.engineer.user.name

    return render_template(
        "dish/drafting.html",
        cases=cases, search=search, status=status, per_page=per_page,
        page=page, total_pages=total_pages, total=total, start=start, end=end,
        site_visit_engineer_by_lead=site_visit_engineer_by_lead,
        status_labels=DRAFTING_STATUS_LABELS, status_pill=DRAFTING_STATUS_PILL,
        map_labels=MAP_STATUS_LABELS,
    )


@dish_bp.route("/drafting/<int:case_id>/upload", methods=["GET", "POST"])
@login_required
def drafting_upload(case_id):
    case = DishCase.query.get_or_404(case_id)
    if request.method == "POST":
        stored = _save_drafting_file(request.files.get("document"), case_id)
        if stored:
            case.drafting_file_filename = stored
            if case.drafting_status == "file_upload_pending":
                case.drafting_status = "internal_qc_pending"
            db.session.commit()
            flash("Drafting file uploaded.", "success")
        else:
            flash("Please choose a file to upload.", "danger")
        return redirect(url_for("dish.drafting_upload", case_id=case_id))
    return render_template(
        "dish/drafting_upload.html", case=case,
        status_labels=DRAFTING_STATUS_LABELS,
    )


@dish_bp.route("/drafting/uploads/<path:filename>")
@login_required
def drafting_download(filename):
    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "drafting")
    return send_from_directory(upload_dir, filename)


@dish_bp.route("/drafting/<int:case_id>/advance", methods=["POST"])
@login_required
def drafting_advance(case_id):
    case = DishCase.query.get_or_404(case_id)
    next_status = DRAFTING_ADVANCE_TO.get(case.drafting_status)
    if not next_status:
        flash("This case is in a rejected state -- open Upload to review it manually.", "danger")
        return redirect(request.referrer or url_for("dish.drafting"))
    case.drafting_status = next_status
    db.session.commit()
    flash(f"'{case.lead.company_name}' moved to {DRAFTING_STATUS_LABELS[next_status]}.", "success")
    return redirect(request.referrer or url_for("dish.drafting"))


@dish_bp.route("/applications-dashboard")
@login_required
def dish_applications():
    tab = request.args.get("tab", "all")
    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "default")

    query = DishApplication.query.join(DishCase).join(Lead)

    if tab == "new":
        query = query.filter(DishApplication.subtype == "new")
    elif tab == "revised":
        query = query.filter(DishApplication.subtype == "revised")
    elif tab == "revised_ext":
        query = query.filter(DishApplication.subtype == "revised_with_extension")
    elif tab in ("map", "stability", "license"):
        query = query.filter(DishApplication.application_type == tab)
    elif tab == "approved":
        query = query.filter(DishApplication.application_status == "approved")
    elif tab == "rejected":
        query = query.filter(DishApplication.application_status == "rejected")

    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                DishApplication.application_no.ilike(f"%{search}%"),
            )
        )

    if sort == "due_asc":
        query = query.order_by(DishApplication.due_date.asc())
    elif sort == "due_desc":
        query = query.order_by(DishApplication.due_date.desc())
    elif sort == "applied_desc":
        query = query.order_by(DishApplication.created_at.desc())
    elif sort == "company":
        query = query.order_by(Lead.company_name.asc())
    else:
        query = query.order_by(
            (DishApplication.application_status == "pending").desc(),
            DishApplication.due_date.asc(),
        )

    applications = query.all()

    all_apps = DishApplication.query.all()
    today = datetime.utcnow().date()
    month_start = today.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    total_applications = len(all_apps)
    due_this_month = sum(1 for a in all_apps if a.application_status == "pending" and a.due_date and month_start <= a.due_date < next_month)
    overdue = sum(1 for a in all_apps if a.application_status == "pending" and a.due_date and a.due_date < today)
    approved = sum(1 for a in all_apps if a.application_status == "approved")
    rejected = sum(1 for a in all_apps if a.application_status == "rejected")
    pending_on_track = sum(1 for a in all_apps if a.application_status == "pending" and not (a.due_date and a.due_date < today))

    upcoming = sorted(
        [a for a in all_apps if a.application_status == "pending" and a.due_date and a.due_date >= today],
        key=lambda a: a.due_date,
    )[:5]

    cases = DishCase.query.join(Lead).order_by(Lead.company_name).all()

    return render_template(
        "dish/applications_dashboard.html",
        applications=applications, cases=cases, tab=tab, search=search, sort=sort,
        total_applications=total_applications, due_this_month=due_this_month,
        overdue=overdue, approved=approved, rejected=rejected, pending_on_track=pending_on_track,
        upcoming=upcoming, today=today,
        status_label=application_status_label, status_pill=application_status_pill,
        subtype_labels=SUBTYPE_LABELS,
    )


@dish_bp.route("/applications-dashboard/create", methods=["POST"])
@login_required
def create_application():
    case_id = request.form.get("dish_case_id")
    application_type = request.form.get("application_type")
    subtype = request.form.get("subtype")
    work_order_date = request.form.get("work_order_date")

    if not (case_id and application_type and subtype and work_order_date):
        flash("Please fill in all fields.", "danger")
        return redirect(url_for("dish.dish_applications"))

    wo_date = datetime.strptime(work_order_date, "%Y-%m-%d").date()
    due = wo_date + timedelta(days=DUE_DAYS.get(application_type, 45))

    app_row = DishApplication(
        dish_case_id=case_id,
        application_type=application_type,
        subtype=subtype,
        application_no=generate_application_no(application_type, subtype),
        work_order_date=wo_date,
        due_date=due,
    )
    db.session.add(app_row)
    db.session.commit()
    flash(f"Application {app_row.application_no} created.", "success")
    return redirect(url_for("dish.dish_applications"))


@dish_bp.route("/applications-dashboard/<int:app_id>/status", methods=["POST"])
@login_required
def update_application_status(app_id):
    app_row = DishApplication.query.get_or_404(app_id)
    new_status = request.form.get("application_status")
    if new_status in ("pending", "approved", "rejected"):
        app_row.application_status = new_status
        db.session.commit()
        flash("Application status updated.", "success")
    return redirect(url_for("dish.dish_applications"))


MAP_STATUS_LABELS = {"new": "New", "revised": "Revised", "revised_with_extension": "Revised With Extension", "not_in_scope": "Not In Scope"}

# Pill CSS class (from theme.css .status-pill variants) for each
# liaisoning_status value -- reuses the LIAISONING_LABELS text defined near
# the top of this file so the wording stays consistent with the
# Applications Dashboard's status column.
LIAISONING_STATUS_PILL = {
    "regional_forward_pending": "assigned",
    "regional_approval_pending": "assigned",
    "query": "overdue",
    "hard_copy_pending": "pending",
    "done": "done",
}
LIAISONING_STATUS_CHOICES = [
    (value, LIAISONING_LABELS[value], LIAISONING_STATUS_PILL[value])
    for value in LIAISONING_LABELS
]
# What "Application Forwarded" moves a case to, based on its current
# status. A query is assumed resolved and sent back into approval once
# forwarded again. Adjust this mapping if your actual workflow differs.
LIAISONING_PROGRESSION = {
    "regional_forward_pending": "regional_approval_pending",
    "regional_approval_pending": "hard_copy_pending",
    "query": "regional_approval_pending",
    "hard_copy_pending": "done",
    "done": "done",
}


@dish_bp.route("/applications")
@login_required
def applications():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = DishCase.query.join(Lead, DishCase.lead_id == Lead.id)
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                DishCase.map_portal_id.ilike(f"%{search}%"),
            )
        )
    if status == "query":
        query = query.filter(DishCase.liaisoning_status == "query")
    elif status in ("online_pending", "offline_pending"):
        query = query.filter(DishCase.map_application_status == status)
    query = query.order_by(DishCase.id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    cases = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "dish/applications.html",
        cases=cases, search=search, status=status, per_page=per_page,
        page=page, total_pages=total_pages, total=total, start=start, end=end,
        map_status_labels=MAP_STATUS_LABELS,
    )


@dish_bp.route("/applications/<int:case_id>/submit", methods=["POST"])
@login_required
def mark_application_submitted(case_id):
    case = DishCase.query.get_or_404(case_id)
    case.map_application_status = "submitted"
    db.session.commit()
    flash("Application marked as submitted.", "success")
    return redirect(url_for("dish.applications", **request.form.to_dict()))


@dish_bp.route("/liaisoning-of-applications")
@login_required
def liaisoning_applications():
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = DishCase.query.join(Lead, DishCase.lead_id == Lead.id)
    if search:
        query = query.filter(Lead.company_name.ilike(f"%{search}%"))
    if status_filter:
        query = query.filter(DishCase.liaisoning_status == status_filter)
    query = query.order_by(DishCase.lead_id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    cases = query.offset((page - 1) * per_page).limit(per_page).all()
    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "dish/liaisoning_applications.html",
        cases=cases, search=search, status_filter=status_filter,
        per_page=per_page, page=page, total_pages=total_pages,
        total=total, start=start, end=end,
        status_choices=LIAISONING_STATUS_CHOICES,
        status_labels=LIAISONING_LABELS,
        status_pill=LIAISONING_STATUS_PILL,
        map_labels=MAP_STATUS_LABELS,
    )


@dish_bp.route("/liaisoning-of-applications/<int:case_id>/query", methods=["POST"])
@login_required
def liaisoning_mark_query(case_id):
    case = DishCase.query.get_or_404(case_id)
    case.liaisoning_status = "query"
    db.session.commit()
    flash(f"Marked '{case.lead.company_name}' as a Regional Office Query.", "success")
    return redirect(request.referrer or url_for("dish.liaisoning_applications"))


@dish_bp.route("/liaisoning-of-applications/<int:case_id>/forward", methods=["POST"])
@login_required
def liaisoning_mark_forwarded(case_id):
    case = DishCase.query.get_or_404(case_id)
    case.liaisoning_status = LIAISONING_PROGRESSION.get(case.liaisoning_status, "done")
    db.session.commit()
    flash(
        f"'{case.lead.company_name}' moved to "
        f"{LIAISONING_LABELS[case.liaisoning_status]}.", "success"
    )
    return redirect(request.referrer or url_for("dish.liaisoning_applications"))


@dish_bp.route("/liaisoning-of-applications/officer-dashboard")
@login_required
def liaisoning_officer_dashboard():
    # Stubbed out for now -- wire this up to a real officer-facing view
    # when that workflow is ready.
    return render_template("dish/coming_soon.html", title="Officer Dashboard")


STABILITY_TYPE_LABELS = {"new": "New", "renew": "Renew"}
STABILITY_STATUS_LABELS = {"pending": "Pending", "done": "Complete"}


def _save_stability_file(file_obj, case_id, tag):
    """Saves an uploaded stability structure/certificate document under
    static/uploads/stability and returns the stored filename, or None if
    no file was chosen."""
    if not file_obj or not file_obj.filename:
        return None
    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "stability")
    os.makedirs(upload_dir, exist_ok=True)
    filename = secure_filename(file_obj.filename)
    stored_name = f"{tag}_{case_id}_{uuid4().hex}_{filename}"
    file_obj.save(os.path.join(upload_dir, stored_name))
    return stored_name


@dish_bp.route("/certificates")
@login_required
def certificates():
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = DishCase.query.join(Lead, DishCase.lead_id == Lead.id)
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                Lead.client_name.ilike(f"%{search}%"),
                Lead.contact_no.ilike(f"%{search}%"),
            )
        )
    if status_filter:
        query = query.filter(DishCase.stability_status == status_filter)
    query = query.order_by(DishCase.id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    cases = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "dish/certificates.html",
        cases=cases, search=search, status=status_filter, per_page=per_page,
        page=page, total_pages=total_pages, total=total, start=start, end=end,
        type_labels=STABILITY_TYPE_LABELS, status_labels=STABILITY_STATUS_LABELS,
    )


@dish_bp.route("/certificates/<int:case_id>/details", methods=["GET", "POST"])
@login_required
def certificate_details(case_id):
    case = DishCase.query.get_or_404(case_id)
    if request.method == "POST":
        stability_type = request.form.get("stability_type")
        stability_status = request.form.get("stability_status")
        if stability_type in ("new", "renew"):
            case.stability_type = stability_type
        if stability_status in ("pending", "done"):
            case.stability_status = stability_status
        db.session.commit()
        flash("Stability certificate details updated.", "success")
        return redirect(url_for("dish.certificates"))
    return render_template("dish/certificate_details.html", case=case)


@dish_bp.route("/certificates/<int:case_id>/structure", methods=["GET", "POST"])
@login_required
def certificate_structure(case_id):
    case = DishCase.query.get_or_404(case_id)
    if request.method == "POST":
        stored = _save_stability_file(request.files.get("document"), case_id, "structure")
        if stored:
            case.stability_structure_filename = stored
            db.session.commit()
            flash("Stability structure document uploaded.", "success")
        else:
            flash("Please choose a file to upload.", "danger")
        return redirect(url_for("dish.certificate_structure", case_id=case_id))
    return render_template(
        "dish/certificate_file.html", case=case, doc_type="structure",
        title="Stability Structure", filename=case.stability_structure_filename,
    )


@dish_bp.route("/certificates/<int:case_id>/certificate", methods=["GET", "POST"])
@login_required
def certificate_file(case_id):
    case = DishCase.query.get_or_404(case_id)
    if request.method == "POST":
        stored = _save_stability_file(request.files.get("document"), case_id, "certificate")
        if stored:
            case.stability_certificate_filename = stored
            case.stability_status = "done"
            db.session.commit()
            flash("Certificate uploaded and case marked Complete.", "success")
        else:
            flash("Please choose a file to upload.", "danger")
        return redirect(url_for("dish.certificate_file", case_id=case_id))
    return render_template(
        "dish/certificate_file.html", case=case, doc_type="certificate",
        title="Certificate", filename=case.stability_certificate_filename,
    )


@dish_bp.route("/certificates/uploads/<path:filename>")
@login_required
def certificate_download(filename):
    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "stability")
    return send_from_directory(upload_dir, filename)


@dish_bp.route("/certificates/<int:case_id>/send-review", methods=["POST"])
@login_required
def certificate_send_review(case_id):
    case = DishCase.query.get_or_404(case_id)
    case.stability_review_sent_at = datetime.utcnow()
    db.session.commit()
    # NOTE: this only records that a review was sent -- there's no client
    # email address stored on Lead yet, so it doesn't actually send an
    # email. Add an email column to Lead and wire this into
    # email_service.send_email(...) if you want a real send here.
    who = case.lead.client_name or case.lead.company_name if case.lead else "the client"
    flash(f"Marked as sent for review to {who}.", "success")
    return redirect(request.referrer or url_for("dish.certificates"))


@dish_bp.route("/license")
@login_required
def license_page():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = DishCase.query.join(Lead, DishCase.lead_id == Lead.id)
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                DishCase.license_portal_id.ilike(f"%{search}%"),
            )
        )
    if status in ("online_pending", "offline_pending", "query", "submitted"):
        query = query.filter(DishCase.license_status == status)
    query = query.order_by(DishCase.id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    cases = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "dish/license.html",
        cases=cases, search=search, status=status, per_page=per_page,
        page=page, total_pages=total_pages, total=total, start=start, end=end,
        license_type_labels={"new": "NEW", "renew": "RENEW"},
    )


@dish_bp.route("/license/<int:case_id>/details", methods=["GET", "POST"])
@login_required
def license_details(case_id):
    case = DishCase.query.get_or_404(case_id)
    if request.method == "POST":
        license_type = request.form.get("license_type")
        if license_type in ("new", "renew"):
            case.license_type = license_type
        case.license_portal_id = request.form.get("license_portal_id", "").strip()
        case.license_portal_password = request.form.get("license_portal_password", "").strip()
        new_status = request.form.get("license_status")
        if new_status in ("online_pending", "offline_pending", "query", "submitted"):
            case.license_status = new_status
        db.session.commit()
        flash("License details updated.", "success")
        return redirect(url_for("dish.license_page"))
    return render_template("dish/license_details.html", case=case)


@dish_bp.route("/license/<int:case_id>/submit", methods=["POST"])
@login_required
def mark_license_submitted(case_id):
    case = DishCase.query.get_or_404(case_id)
    case.license_status = "submitted"
    db.session.commit()
    flash(f"License application marked as submitted for '{case.lead.company_name}'.", "success")
    return redirect(request.referrer or url_for("dish.license_page"))


LICENSE_TYPE_LABELS = {"new": "New", "renew": "Renew"}
LIAISONING_LICENSE_LABELS = {
    "regional_forward_pending": "Regional Office Forward Pending",
    "regional_approval_pending": "Regional Office Approval Pending",
    "query": "Regional Office Query",
    "hard_copy_pending": "Hard Copy Upload Pending",
    "done": "Complete",
}
LIAISONING_LICENSE_NEXT = {
    "regional_forward_pending": "regional_approval_pending",
    "regional_approval_pending": "hard_copy_pending",
    "hard_copy_pending": "done",
    "query": "regional_approval_pending",
    "done": "done",
}


@dish_bp.route("/liaisoning-of-license")
@login_required
def liaisoning_license():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = DishCase.query.join(Lead, DishCase.lead_id == Lead.id)
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                DishCase.license_portal_id.ilike(f"%{search}%"),
            )
        )
    if status in LIAISONING_LICENSE_LABELS:
        query = query.filter(DishCase.liaisoning_license_status == status)
    query = query.order_by(DishCase.id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    cases = query.offset((page - 1) * per_page).limit(per_page).all()
    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "dish/liaisoning_license.html",
        cases=cases, search=search, status=status, per_page=per_page,
        page=page, total_pages=total_pages, total=total, start=start, end=end,
        license_type_labels=LICENSE_TYPE_LABELS, status_labels=LIAISONING_LICENSE_LABELS,
    )


@dish_bp.route("/liaisoning-of-license/<int:case_id>/query", methods=["POST"])
@login_required
def liaisoning_license_query(case_id):
    case = DishCase.query.get_or_404(case_id)
    case.liaisoning_license_status = "query"
    db.session.commit()
    flash("Marked as regional office query.", "success")
    return redirect(url_for("dish.liaisoning_license", **request.form.to_dict()))


@dish_bp.route("/liaisoning-of-license/<int:case_id>/forward", methods=["POST"])
@login_required
def liaisoning_license_forward(case_id):
    case = DishCase.query.get_or_404(case_id)
    case.liaisoning_license_status = LIAISONING_LICENSE_NEXT.get(case.liaisoning_license_status, "done")
    db.session.commit()
    flash("Application forwarded.", "success")
    return redirect(url_for("dish.liaisoning_license", **request.form.to_dict()))