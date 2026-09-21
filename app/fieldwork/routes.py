from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from app import db
from app.models import SiteVisit, Measurement, Engineer, Lead, User

fieldwork_bp = Blueprint("fieldwork", __name__, url_prefix="/fieldwork")


@fieldwork_bp.route("/site-visits")
@login_required
def site_visits():
    status_filter = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = SiteVisit.query.join(Lead, SiteVisit.lead_id == Lead.id)

    if status_filter:
        query = query.filter(SiteVisit.status == status_filter)
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                Lead.client_name.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(SiteVisit.id.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    visits = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template( 
        "fieldwork/site_visits.html",
        visits=visits,
        leads=Lead.query.all(),
        engineers=Engineer.query.all(),
        status_filter=status_filter,
        search=search,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total=total,
        start=start,
        end=end,
    )


@fieldwork_bp.route("/site-visits/create", methods=["POST"])
@login_required
def create_site_visit():
    visit = SiteVisit(
        lead_id=request.form.get("lead_id"),
        engineer_id=request.form.get("engineer_id") or None,
        scheduled_date=request.form.get("scheduled_date") or None,
    )
    db.session.add(visit)
    db.session.commit()
    flash("Site visit scheduled.", "success")
    return redirect(url_for("fieldwork.site_visits"))


@fieldwork_bp.route("/site-visits/<int:visit_id>/complete", methods=["POST"])
@login_required
def complete_site_visit(visit_id):
    visit = SiteVisit.query.get_or_404(visit_id)
    visit.status = "done"
    # Completing a site visit hands the job to Measurements — create that
    # record now (it didn't exist before, so the Measurements/Assign pages
    # were always empty).
    if not visit.measurement:
        db.session.add(Measurement(site_visit_id=visit.id))
    db.session.commit()
    flash("Site visit marked done.", "success")
    return redirect(url_for("fieldwork.site_visits"))


@fieldwork_bp.route("/measurements")
@login_required
def measurements():
    status_filter = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = (
        Measurement.query
        .join(SiteVisit, Measurement.site_visit_id == SiteVisit.id)
        .join(Lead, SiteVisit.lead_id == Lead.id)
    )

    if status_filter == "pending":
        query = query.filter(Measurement.status != "done")
    elif status_filter == "done":
        query = query.filter(Measurement.status == "done")
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                Lead.client_name.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(Measurement.id.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    measurements = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "fieldwork/measurements.html",
        measurements=measurements,
        status_filter=status_filter,
        search=search,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total=total,
        start=start,
        end=end,
    )


@fieldwork_bp.route("/measurements/<int:measurement_id>/status", methods=["POST"])
@login_required
def update_measurement_status(measurement_id):
    m = Measurement.query.get_or_404(measurement_id)
    m.status = request.form.get("status") or m.status
    db.session.commit()
    flash("Measurement status updated.", "success")
    return redirect(url_for("fieldwork.measurements"))


@fieldwork_bp.route("/measurements/assign")
@login_required
def assign_measurement_list():
    status_filter = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = (
        Measurement.query
        .join(SiteVisit, Measurement.site_visit_id == SiteVisit.id)
        .join(Lead, SiteVisit.lead_id == Lead.id)
    )

    if status_filter == "unassigned":
        query = query.filter(Measurement.status == "pending")
    elif status_filter == "assigned":
        query = query.filter(Measurement.status == "assigned")
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                Lead.client_name.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(Measurement.id.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    measurements = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "fieldwork/assign_measurement.html",
        measurements=measurements,
        users=User.query.filter_by(is_active_flag=True).all(),
        status_filter=status_filter,
        search=search,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total=total,
        start=start,
        end=end,
    )

@fieldwork_bp.route("/measurements/<int:measurement_id>/assign", methods=["POST"])
@login_required
def assign_measurement(measurement_id):
    m = Measurement.query.get_or_404(measurement_id)
    m.assigned_to_id = request.form.get("user_id")
    m.status = "assigned"
    db.session.commit()
    flash("Measurement assigned.", "success")
    return redirect(url_for("fieldwork.assign_measurement_list"))