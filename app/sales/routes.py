from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from app import db
from app.models import Lead, Estimation, Department, WorkStage, Payment

sales_bp = Blueprint("sales", __name__, url_prefix="/sales")

LEAD_STATUSES = [
    "New Lead", "Site Visit Scheduled", "Site Visit Complete",
    "Estimation Sent", "Estimation Approved", "Estimation Rejected",
    "Work Order Received",
]

# Matches the drafting stage names already referenced in app/__init__.py's
# inject_nav_counts(). Getting a work order kicks off these three stages.
DRAFTING_STAGE_NAMES = [
    "Architectural Drafting", "Structural Drafting", "3D Elevation Drafting",
]


def status_key(label):
    return label.lower().replace(" ", "_")


@sales_bp.route("/leads")
@login_required
def leads():
    view = request.args.get("view", "list")

    if view == "create":
        return render_template(
            "sales/leads.html",
            view="create",
            departments=Department.query.all(),
        )

    dept_filter = request.args.get("department", "")
    status_filter = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = Lead.query
    if dept_filter:
        query = query.join(Department).filter(Department.name == dept_filter)
    if status_filter:
        query = query.filter(Lead.status == status_filter)
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                Lead.client_name.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(Lead.id.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    leads = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "sales/leads.html",
        view="list",
        leads=leads,
        departments=Department.query.all(),
        statuses=LEAD_STATUSES,
        dept_filter=dept_filter,
        status_filter=status_filter,
        search=search,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total=total,
        start=start,
        end=end,
    )


@sales_bp.route("/leads/create", methods=["POST"])
@login_required
def create_lead():
    lead = Lead(
        company_name=request.form.get("company_name"),
        company_address=request.form.get("company_address"),
        client_name=request.form.get("client_name"),        
        contact_no=request.form.get("contact_no"),
        department_id=request.form.get("department_id") or None,
        status="new_lead",
    )
    db.session.add(lead)
    db.session.commit()
    flash("Lead created.", "success")
    return redirect(url_for("sales.leads"))


@sales_bp.route("/leads/<int:lead_id>/edit", methods=["POST"])
@login_required
def edit_lead(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    previous_status = lead.status
    lead.company_name = request.form.get("company_name")
    lead.company_address = request.form.get("company_address")
    lead.client_name = request.form.get("client_name")
    lead.contact_no = request.form.get("contact_no")
    lead.department_id = request.form.get("department_id") or None
    lead.status = request.form.get("status") or lead.status

    # Getting a work order is what should actually start drafting work —
    # nothing previously created WorkStage rows anywhere, so the Workflow
    # To-Do list and Work Dashboard were always empty. Create the default
    # stages the first time a lead reaches this status.
    if lead.status == "work_order_received" and previous_status != "work_order_received":
        existing_stage_names = {s.stage_name for s in lead.work_stages}
        for stage_name in DRAFTING_STAGE_NAMES:
            if stage_name not in existing_stage_names:
                db.session.add(WorkStage(
                    lead_id=lead.id,
                    stage_name=stage_name,
                    due_date=datetime.utcnow() + timedelta(days=14),
                ))

    db.session.commit()
    flash("Lead updated.", "success")
    return redirect(url_for("sales.leads"))


@sales_bp.route("/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
def delete_lead(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    db.session.delete(lead)
    db.session.commit()
    flash("Lead deleted.", "success")
    return redirect(url_for("sales.leads"))


@sales_bp.route("/estimation")
@login_required
def estimation():
    status_filter = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = Estimation.query.join(Lead, Estimation.lead_id == Lead.id)

    if status_filter:
        query = query.filter(Estimation.status == status_filter)
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                Lead.client_name.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(Estimation.id.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    estimations = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "sales/estimation.html",
        estimations=estimations,
        status_filter=status_filter,
        search=search,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total=total,
        start=start,
        end=end,
    )


@sales_bp.route("/estimation/create", methods=["POST"])
@login_required
def create_estimation():
    lead_id = request.form.get("lead_id")
    amount = request.form.get("amount") or 0

    est = Estimation(lead_id=lead_id, amount=amount)
    db.session.add(est)

    existing_payment = Payment.query.filter_by(lead_id=lead_id).first()
    if not existing_payment:
        db.session.add(Payment(lead_id=lead_id, amount=amount, paid_percentage=0, status="estimate_generated"))

    db.session.commit()
    flash("Estimation created.", "success")
    return redirect(url_for("sales.estimation"))