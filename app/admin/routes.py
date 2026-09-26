import os
import shutil
import sqlite3
import zipfile
from io import BytesIO
from uuid import uuid4
from datetime import datetime, timedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
    current_app, send_file,
)
from flask_login import login_required, current_user
from sqlalchemy import func
from werkzeug.utils import secure_filename
from app import db
from app.models import (
    User, Role, Department, Lead, Estimation, Payment,
    WorkStage, SiteVisit, Measurement, Engineer, ConfigSetting, CompanyProfile
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)

    return wrapper


@admin_bp.route("/dashboard")
@login_required
def dashboard():
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ---- Top 6 stat cards ----
    new_works_month = Lead.query.filter(Lead.created_at >= month_start).count()
    unconfirmed_works = Lead.query.filter_by(status="unconfirmed").count()
    pending_stages = WorkStage.query.filter(WorkStage.status != "done").count()
    completed_stages = WorkStage.query.filter_by(status="done").count()
    monthly_revenue = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.date >= month_start, Payment.status == "received")
        .scalar()
    )
    payment_pending = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == "pending")
        .scalar()
    )
    payment_received = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == "received")
        .scalar()
    )

    # ---- "Pending Work Control" list (counts with click-through arrows) ----
    pending_control = {
        "site_visit": SiteVisit.query.filter_by(status="pending").count(),
        "estimation": Estimation.query.filter_by(status="pending").count(),
        "measurement_assign": Measurement.query.filter_by(status="pending").count(),
        "measurement": Measurement.query.filter_by(status="assigned").count(),
    }

    # ---- "Employee Performance" table ----
    perf_rows = (
        db.session.query(
            User.name,
            Role.name,
            func.sum(db.case((WorkStage.status == "done", 1), else_=0)),
            func.sum(db.case((WorkStage.status != "done", 1), else_=0)),
        )
        .join(WorkStage, WorkStage.assigned_to_id == User.id)
        .outerjoin(Role, Role.id == User.role_id)
        .group_by(User.id, User.name, Role.name)
        .all()
    )   
    employee_performance = []
    for name, role, completed, pending in perf_rows:
        employee_performance.append({
            "name": name,
            "role": role or "-",
            "completed": completed or 0,
            "pending": pending or 0,
            "status": "OVERLOAD" if (pending or 0) > 20 else "NORMAL",
        })

    # ---- "Overdue by Work Type" stat cards ----
    overdue_rows = (
        db.session.query(WorkStage.stage_name, func.count(WorkStage.id))
        .filter(WorkStage.status != "done", WorkStage.due_date < now)
        .group_by(WorkStage.stage_name)
        .all()
    )

    # ---- "High Risk Works" table ----
    # High risk = a lead that has at least one overdue work stage
    # AND/OR at least one payment still marked pending (per your rule: "both combined").
    overdue_lead_ids = {
        lead_id
        for lead_id, cnt in (
            db.session.query(WorkStage.lead_id, func.count(WorkStage.id))
            .filter(WorkStage.status != "done", WorkStage.due_date < now)
            .group_by(WorkStage.lead_id)
            .all()
        )
        if cnt
    }
    pending_payment_lead_ids = {
        row[0]
        for row in db.session.query(Payment.lead_id)
        .filter(Payment.status == "pending")
        .distinct()
        .all()
    }
    high_risk_lead_ids = overdue_lead_ids | pending_payment_lead_ids

    high_risk_works = []
    for lead_id in high_risk_lead_ids:
        lead = Lead.query.get(lead_id)
        if not lead:
            continue
        overdue_stages = [s for s in lead.work_stages if s.is_overdue]
        has_pending_payment = lead_id in pending_payment_lead_ids
        # Table (per screenshot) shows the overdue-stage deadlines, so only
        # list leads that have at least one overdue stage to display.
        if not overdue_stages:
            continue
        high_risk_works.append({
            "lead_id": lead.id,
            "company": lead.company_name,
            "department": lead.department.name if lead.department else "-",
            "deadlines": [
                f"{s.stage_name} : {s.due_date.strftime('%d-%m-%Y')}" for s in overdue_stages
            ],
            "delayed_count": len(overdue_stages) + (1 if has_pending_payment else 0),
        })
    high_risk_works.sort(key=lambda r: r["delayed_count"], reverse=True)
    high_risk_works = high_risk_works[:10]

    # ---- "Top Revenue Clients" table (highest total RECEIVED payments) ----
    top_revenue_rows = (
        db.session.query(Lead.company_name, func.coalesce(func.sum(Payment.amount), 0))
        .join(Payment, Payment.lead_id == Lead.id)
        .filter(Payment.status == "received")
        .group_by(Lead.id)
        .order_by(func.sum(Payment.amount).desc())
        .limit(10)
        .all()
    )
    top_revenue_clients = [{"company": c, "total": float(t)} for c, t in top_revenue_rows]

    # ---- "High Risk Payment Clients" table (highest PENDING payment amount) ----
    high_risk_payment_rows = (
        db.session.query(
            Lead.company_name,
            func.coalesce(func.sum(Payment.amount), 0),
            func.coalesce(
                func.sum(db.case((Payment.status == "pending", Payment.amount), else_=0)), 0
            ),
        )
        .join(Payment, Payment.lead_id == Lead.id)
        .group_by(Lead.id)
        .having(func.sum(db.case((Payment.status == "pending", Payment.amount), else_=0)) > 0)
        .order_by(func.sum(db.case((Payment.status == "pending", Payment.amount), else_=0)).desc())
        .limit(10)
        .all()
    )
    high_risk_payment_clients = [
        {"company": c, "total": float(t), "pending": float(p)}
        for c, t, p in high_risk_payment_rows
    ]

    # ---- Total revenue figure shown under the department donut ----
    department_revenue_total = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == "received")
        .scalar()
    )

    # ---- Employee Productivity + Salary vs Work Value ----
    # "Work Value" per employee = sum of RECEIVED payments on every lead where
    # this employee has completed at least one work stage. If two employees
    # both worked on the same lead, that lead's payment counts for both of
    # them (this is a simplification you asked for, not a per-stage split).
    employees_all = User.query.filter(User.is_admin == False).all()
    employee_productivity = []
    for emp in employees_all:
        completed_stage_leads = {
            s.lead_id
            for s in WorkStage.query.filter_by(assigned_to_id=emp.id, status="done").all()
        }
        work_value = 0.0
        if completed_stage_leads:
            work_value = (
                db.session.query(func.coalesce(func.sum(Payment.amount), 0))
                .filter(Payment.lead_id.in_(completed_stage_leads), Payment.status == "received")
                .scalar()
            )
            work_value = float(work_value)
        salary = float(emp.salary or 0)
        productivity = round(work_value / salary, 2) if salary > 0 else 0
        employee_productivity.append({
            "name": emp.name,
            "work_value": work_value,
            "salary": salary,
            "productivity": productivity,
            "status": "DANGER" if productivity < 1 else "GOOD",
        })

    return render_template(
        "admin/dashboard.html",
        new_works_month=new_works_month,
        unconfirmed_works=unconfirmed_works,
        pending_stages=pending_stages,
        completed_stages=completed_stages,
        monthly_revenue=monthly_revenue,
        payment_pending=payment_pending,
        payment_received=payment_received,
        pending_control=pending_control,
        employee_performance=employee_performance,
        overdue_rows=overdue_rows,
        high_risk_works=high_risk_works,
        top_revenue_clients=top_revenue_clients,
        high_risk_payment_clients=high_risk_payment_clients,
        department_revenue_total=department_revenue_total,
        employee_productivity=employee_productivity,
    )


@admin_bp.route("/dashboard/work")
@login_required
def work_dashboard():
    stages = (
        WorkStage.query.filter(WorkStage.status != "done", WorkStage.assigned_to_id.isnot(None))
        .order_by(WorkStage.assigned_to_id)
        .all()
    )
    grouped = {}
    for s in stages:
        key = s.assigned_to.name if s.assigned_to else "Unassigned"
        grouped.setdefault(key, []).append({
            "lead_name": s.lead.company_name if s.lead else "-",
            "department": s.lead.department.name if s.lead and s.lead.department else "-",
            "status_label": s.status.replace("_", " ").upper(),
            "stage_name": s.stage_name,
        })
    work_groups = [{"employee_name": name, "tasks": tasks} for name, tasks in grouped.items()]
    return render_template("admin/work_dashboard.html", work_groups=work_groups)


@admin_bp.route("/dashboard/employee")
@login_required
def employee_dashboard():
    employees = User.query.filter(User.is_admin == False).all()
    selected_id = request.args.get("user_id", type=int)
    selected = None
    if selected_id:
        selected = User.query.get(selected_id)
    if not selected and employees:
        selected = employees[0]

    target_points = 100
    progress_pct = 0
    if selected:
        completed = WorkStage.query.filter_by(assigned_to_id=selected.id, status="done").count()
        progress_pct = min(100, round((completed / target_points) * 100)) if target_points else 0

    return render_template(
        "admin/employee_dashboard.html",
        employees=employees,
        selected=selected or User(name="-"),
        target_points=target_points,
        progress_pct=progress_pct,
    )

def _month_bucket(column):
    """Groups a date/datetime column by year-month, working on both
    SQLite (local dev) and PostgreSQL (Render production)."""
    if db.engine.dialect.name == "postgresql":
        return func.to_char(column, "YYYY-MM")
    return func.strftime("%Y-%m", column)

@admin_bp.route("/dashboard/chart-data")
@login_required
def chart_data():
    now = datetime.utcnow()
    twelve_months_ago = now - timedelta(days=365)
    six_months_ago = now - timedelta(days=180)

    # ---- Business Growth (bar): new leads per month, last 12 months ----
    monthly_counts = (
        db.session.query(
            _month_bucket(Lead.created_at), func.count(Lead.id)
        )
        .filter(Lead.created_at >= twelve_months_ago)
        .group_by(_month_bucket(Lead.created_at))
        .all()
    )

    # ---- Department Revenue (donut): received-payment total per department ----
    dept_revenue = (
        db.session.query(Department.name, func.coalesce(func.sum(Payment.amount), 0))
        .join(Lead, Lead.department_id == Department.id)
        .join(Payment, Payment.lead_id == Lead.id)
        .filter(Payment.status == "received")
        .group_by(Department.name)
        .all()
    )

    # ---- Role Wise Pending Load (horizontal bar): open work-stage count per stage type ----
    role_load_rows = (
        db.session.query(WorkStage.stage_name, func.count(WorkStage.id))
        .filter(WorkStage.status != "done")
        .group_by(WorkStage.stage_name)
        .all()
    )

    # ---- Department Overdue Load (bar): overdue work-stage count per department ----
    dept_overdue_rows = (
        db.session.query(Department.name, func.count(WorkStage.id))
        .join(Lead, Lead.department_id == Department.id)
        .join(WorkStage, WorkStage.lead_id == Lead.id)
        .filter(WorkStage.status != "done", WorkStage.due_date < now)
        .group_by(Department.name)
        .all()
    )

    # ---- Payment Trend (line): total payment amount logged per month, any status ----
    payment_trend_rows = (
        db.session.query(
            _month_bucket(Payment.date), func.coalesce(func.sum(Payment.amount), 0)
        )
        .filter(Payment.date >= twelve_months_ago)
        .group_by(_month_bucket(Payment.date))
        .all()
    )

    # ---- Monthly Revenue (area): RECEIVED-only payment amount per month ----
    monthly_revenue_rows = (
        db.session.query(
            _month_bucket(Payment.date), func.coalesce(func.sum(Payment.amount), 0)
        )
        .filter(Payment.date >= twelve_months_ago, Payment.status == "received")
        .group_by(_month_bucket(Payment.date))
        .all()
    )

    # ---- Overdue Trend (line): overdue work-stage count per month, last 6 months ----
    overdue_trend_rows = (
        db.session.query(
            _month_bucket(WorkStage.due_date), func.count(WorkStage.id)
        )
        .filter(
            WorkStage.status != "done",
            WorkStage.due_date < now,
            WorkStage.due_date >= six_months_ago,
        )
        .group_by(_month_bucket(WorkStage.due_date))
        .all()
    )

    # ---- Employee Salary vs Work Value (grouped bar) ----
    # Same "work value" definition as the dashboard's Employee Productivity table:
    # received payments on leads where the employee completed a work stage.
    employees_all = User.query.filter(User.is_admin == False).all()
    salary_labels, salary_values, work_values = [], [], []
    for emp in employees_all:
        completed_stage_leads = {
            s.lead_id
            for s in WorkStage.query.filter_by(assigned_to_id=emp.id, status="done").all()
        }
        work_value = 0.0
        if completed_stage_leads:
            work_value = float(
                db.session.query(func.coalesce(func.sum(Payment.amount), 0))
                .filter(Payment.lead_id.in_(completed_stage_leads), Payment.status == "received")
                .scalar()
            )
        salary_labels.append(emp.name)
        salary_values.append(float(emp.salary or 0))
        work_values.append(work_value)

    return jsonify({
        "business_growth": {"labels": [m for m, _ in monthly_counts],
                             "values": [c for _, c in monthly_counts]},
        "department_revenue": {"labels": [d for d, _ in dept_revenue],
                                "values": [float(v) for _, v in dept_revenue]},
        "role_load": {"labels": [r for r, _ in role_load_rows],
                      "values": [c for _, c in role_load_rows]},
        "department_overdue": {"labels": [d for d, _ in dept_overdue_rows],
                                "values": [c for _, c in dept_overdue_rows]},
        "payment_trend": {"labels": [m for m, _ in payment_trend_rows],
                           "values": [float(v) for _, v in payment_trend_rows]},
        "monthly_revenue_trend": {"labels": [m for m, _ in monthly_revenue_rows],
                                   "values": [float(v) for _, v in monthly_revenue_rows]},
        "overdue_trend": {"labels": [m for m, _ in overdue_trend_rows],
                           "values": [c for _, c in overdue_trend_rows]},
        "salary_vs_work_value": {"labels": salary_labels, "salary": salary_values, "work_value": work_values},
    })


@admin_bp.route("/roles", methods=["GET", "POST"])
@login_required
@admin_required
def roles():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            db.session.add(Role(name=name))
            db.session.commit()
            flash("Role added.", "success")
        return redirect(url_for("admin.roles"))
    return render_template("admin/roles.html", roles=Role.query.all())


# Deletes a role. Blocked if any user is still assigned to it, so you can't
# accidentally orphan users -- reassign or remove those users first.
@admin_bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_role(role_id):
    role = Role.query.get_or_404(role_id)
    if role.users:
        flash(f"Can't delete '{role.name}' -- {len(role.users)} user(s) still have this role.", "danger")
        return redirect(url_for("admin.roles"))
    db.session.delete(role)
    db.session.commit()
    flash("Role deleted.", "success")
    return redirect(url_for("admin.roles"))


@admin_bp.route("/users")
@login_required
@admin_required
def users():
    return render_template("admin/users.html", users=User.query.all(), roles=Role.query.all())


@admin_bp.route("/users/create", methods=["POST"])
@login_required
@admin_required
def create_user():
    name = request.form.get("name")
    email = request.form.get("email", "").lower().strip()
    password = request.form.get("password")
    role_id = request.form.get("role_id") or None
    is_admin = bool(request.form.get("is_admin"))
    salary = request.form.get("salary") or 0

    if User.query.filter_by(email=email).first():
        flash("A user with that email already exists.", "danger")
        return redirect(url_for("admin.users"))

    plain_password = password or "changeme123"
    user = User(name=name, email=email, role_id=role_id, is_admin=is_admin, salary=salary)
    user.set_password(plain_password)
    db.session.add(user)
    db.session.commit()

    from email_service import send_new_user_email
    login_url = url_for("auth.login", _external=True)
    email_sent = send_new_user_email(user, plain_password, login_url)

    if email_sent:
        flash("User created and notified by email.", "success")
    else:
        flash("User created, but the notification email couldn't be sent -- check Email Settings.", "danger")
    return redirect(url_for("admin.users"))


# Deletes a user. Only reachable by an admin (admin_required below).
# Blocked if it's your own account (avoid locking yourself out) or the
# last remaining admin (avoid leaving the app with no admin at all).
# Historical records that reference this user (assigned tasks, reports
# they created, etc.) keep their existing "-" fallback in templates once
# the reference no longer resolves to a real user.
@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You can't remove your own account.", "danger")
        return redirect(url_for("admin.users"))

    if user.is_admin and User.query.filter_by(is_admin=True).count() <= 1:
        flash("Can't remove the last remaining admin.", "danger")
        return redirect(url_for("admin.users"))

    db.session.delete(user)
    db.session.commit()
    flash(f"{user.name} removed.", "success")
    return redirect(url_for("admin.users"))


# Inline "Save" on the Users/Employees page updates just the salary field.
# Used by both admin/users.html and admin/employees.html.
@admin_bp.route("/users/<int:user_id>/salary", methods=["POST"])
@login_required
@admin_required
def update_salary(user_id):
    user = User.query.get_or_404(user_id)
    salary = request.form.get("salary") or 0
    user.salary = salary
    db.session.commit()
    flash(f"Salary updated for {user.name}.", "success")
    return redirect(request.referrer or url_for("admin.users"))


# Used by the pencil "Edit" action on admin/employees.html -- updates both
# salary and the employment start month (from_month) for one employee at once.
@admin_bp.route("/employees/<int:user_id>/update", methods=["POST"])
@login_required
@admin_required
def update_employee(user_id):
    user = User.query.get_or_404(user_id)
    salary = request.form.get("salary") or None
    from_month = request.form.get("from_month") or None
    user.salary = salary
    user.from_month = from_month
    db.session.commit()
    flash(f"Updated details for {user.name}.", "success")
    return redirect(url_for("admin.employees"))


@admin_bp.route("/config")
@login_required
@admin_required
def config_page():
    _ensure_default_config_rows()
    editing_id = request.args.get("edit", type=int)
    editing = ConfigSetting.query.get(editing_id) if editing_id else None
    return render_template(
        "admin/config.html",
        configs=ConfigSetting.query.order_by(ConfigSetting.id).all(),
        editing=editing,
    )


@admin_bp.route("/config/<int:config_id>/update", methods=["POST"])
@login_required
@admin_required
def update_config(config_id):
    setting = ConfigSetting.query.get_or_404(config_id)
    if setting.field_type == "file":
        file = request.files.get("file")
        if file and file.filename:
            upload_dir = os.path.join(current_app.root_path, "static", "uploads", "config")
            os.makedirs(upload_dir, exist_ok=True)
            filename = secure_filename(f"config_{setting.id}_{file.filename}")
            file.save(os.path.join(upload_dir, filename))
            setting.file_path = filename
    else:
        setting.value = request.form.get("value")
    db.session.commit()
    flash("Config updated.", "success")
    return redirect(url_for("admin.config_page"))


def _ensure_default_config_rows():
    defaults = [
        ("document_reminder_days", "After X days document upload reminder", "2", "number"),
        ("required_document_pdf", "Required Document List PDF", None, "file"),
    ]
    for key, title, value, field_type in defaults:
        if not ConfigSetting.query.filter_by(key=key).first():
            db.session.add(ConfigSetting(key=key, title=title, value=value, field_type=field_type))
    db.session.commit()


# ---------------------------------------------------------------------------
# ENGINEERS PAGE
# Lists everyone in the Engineer table + a form to turn an existing User
# into an Engineer (adds a row to the "engineer" table). A single user can
# have more than one Engineer row -- e.g. one license as "Engineer" and a
# separate one as "Structure Engineer" -- so there's no one-per-user limit.
# ---------------------------------------------------------------------------
def _save_engineer_photo(photo):
    """Saves an uploaded engineer photo under static/uploads/engineers and
    returns the stored filename, or None if no file was uploaded."""
    if not photo or not photo.filename:
        return None
    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "engineers")
    os.makedirs(upload_dir, exist_ok=True)
    filename = secure_filename(photo.filename)
    stored_name = f"{uuid4().hex}_{filename}"
    photo.save(os.path.join(upload_dir, stored_name))
    return stored_name


def _parse_license_expiry(value):
    """Parses a yyyy-mm-dd date-input value. Returns (date_or_none, ok)."""
    if not value:
        return None, True
    try:
        return datetime.strptime(value, "%Y-%m-%d").date(), True
    except ValueError:
        return None, False


@admin_bp.route("/engineers")
@login_required
@admin_required
def engineers():
    edit_id = request.args.get("edit", type=int)
    editing = Engineer.query.get(edit_id) if edit_id else None
    return render_template(
        "admin/engineers.html",
        engineers=Engineer.query.order_by(Engineer.id).all(),  # existing engineer profiles, shown in the table
        users=User.query.all(),                                # every user, shown in the "Select User" dropdown
        editing=editing,                                       # set when the "Edit" icon opened the form
    )


# Handles the "Add Engineer" form submit on admin/engineers.html.
# Turns a chosen User into an Engineer by creating a linked Engineer row.
@admin_bp.route("/engineers/create", methods=["POST"])
@login_required
@admin_required
def create_engineer():
    user_id = request.form.get("user_id")
    designation = request.form.get("designation", "").strip()
    license_number = request.form.get("license_number", "").strip()
    expiry_date, ok = _parse_license_expiry(request.form.get("license_expiry_date", "").strip())

    if not user_id:
        flash("Please select a user.", "danger")
        return redirect(url_for("admin.engineers"))
    if not ok:
        flash("Invalid license expiry date.", "danger")
        return redirect(url_for("admin.engineers"))

    engineer = Engineer(
        user_id=user_id,
        designation=designation or None,
        license_number=license_number or None,
        license_expiry_date=expiry_date,
        photo_filename=_save_engineer_photo(request.files.get("photo")),
    )
    db.session.add(engineer)
    db.session.commit()
    flash("Engineer added.", "success")
    return redirect(url_for("admin.engineers"))


# Handles the "Save Changes" form submit when editing an existing engineer row.
@admin_bp.route("/engineers/<int:engineer_id>/edit", methods=["POST"])
@login_required
@admin_required
def edit_engineer(engineer_id):
    engineer = Engineer.query.get_or_404(engineer_id)
    expiry_date, ok = _parse_license_expiry(request.form.get("license_expiry_date", "").strip())
    if not ok:
        flash("Invalid license expiry date.", "danger")
        return redirect(url_for("admin.engineers", edit=engineer_id))

    engineer.designation = request.form.get("designation", "").strip() or None
    engineer.license_number = request.form.get("license_number", "").strip() or None
    engineer.license_expiry_date = expiry_date

    new_photo = _save_engineer_photo(request.files.get("photo"))
    if new_photo:
        engineer.photo_filename = new_photo

    db.session.commit()
    flash("Engineer updated.", "success")
    return redirect(url_for("admin.engineers"))


@admin_bp.route("/engineers/<int:engineer_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_engineer(engineer_id):
    engineer = Engineer.query.get_or_404(engineer_id)
    db.session.delete(engineer)
    db.session.commit()
    flash("Engineer removed.", "success")
    return redirect(url_for("admin.engineers"))


# ---------------------------------------------------------------------------
# EMPLOYEES PAGE
# Simple read-only list of all non-admin users. Fixes the second missing
# endpoint: url_for('admin.employees') from base.html sidebar.
# ---------------------------------------------------------------------------
@admin_bp.route("/employees")
@login_required
@admin_required
def employees():
    return render_template(
        "admin/employees.html",
        users=User.query.filter(User.is_admin == False).all(),
    )


# ---------------------------------------------------------------------------
# MY COMPANY (Organization Profile)
# A singleton settings page for YOUR OWN org's branding/contact details,
# plus a small database backup/restore panel underneath it.
# ---------------------------------------------------------------------------
def _get_or_create_company_profile():
    profile = CompanyProfile.query.first()
    if not profile:
        profile = CompanyProfile(legal_name=current_app.config.get("COMPANY_NAME", ""))
        db.session.add(profile)
        db.session.commit()
    return profile


def _save_company_asset(file_obj, tag):
    """Saves an uploaded logo/signature/stamp image under
    static/uploads/company and returns the stored filename, or None if no
    file was uploaded."""
    if not file_obj or not file_obj.filename:
        return None
    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "company")
    os.makedirs(upload_dir, exist_ok=True)
    filename = secure_filename(file_obj.filename)
    stored_name = f"{tag}_{uuid4().hex}_{filename}"
    file_obj.save(os.path.join(upload_dir, stored_name))
    return stored_name


def _backups_dir():
    path = os.path.abspath(os.path.join(current_app.root_path, "..", "backups"))
    os.makedirs(path, exist_ok=True)
    return path


def _sqlite_db_path():
    """Returns the on-disk path for the app's SQLite database, or None if
    the app is configured for a different database engine (e.g. Postgres
    on Render) -- the backup/restore tools below only support SQLite."""
    db_uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not db_uri.startswith("sqlite:///"):
        return None
    return db_uri.replace("sqlite:///", "", 1)


def _build_backup_zip():
    """Builds an in-memory ZIP containing a human-readable .sql dump and a
    raw .backup binary copy of the current database. Returns
    (BytesIO, timestamp) or (None, None) if backups aren't supported."""
    db_path = _sqlite_db_path()
    if not db_path or not os.path.exists(db_path):
        return None, None

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        conn = sqlite3.connect(db_path)
        sql_text = "\n".join(conn.iterdump())
        conn.close()
        zf.writestr(f"hse_backup_{timestamp}.sql", sql_text)
        zf.write(db_path, arcname=f"hse_backup_{timestamp}.backup")
    buffer.seek(0)
    return buffer, timestamp


@admin_bp.route("/my-company")
@login_required
@admin_required
def my_company():
    profile = _get_or_create_company_profile()
    backup_dir = _backups_dir()
    recent_backups = sorted(
        (f for f in os.listdir(backup_dir) if f.endswith(".zip")), reverse=True
    )[:5]
    return render_template(
        "admin/my_company.html", profile=profile, recent_backups=recent_backups
    )


@admin_bp.route("/my-company/update", methods=["POST"])
@login_required
@admin_required
def update_my_company():
    profile = _get_or_create_company_profile()
    profile.legal_name = request.form.get("legal_name", "").strip() or profile.legal_name
    profile.tagline = request.form.get("tagline", "").strip()
    profile.email = request.form.get("email", "").strip()
    profile.phone = request.form.get("phone", "").strip()
    profile.website = request.form.get("website", "").strip()
    profile.address = request.form.get("address", "").strip()

    new_logo = _save_company_asset(request.files.get("logo"), "logo")
    if new_logo:
        profile.logo_filename = new_logo

    new_signature = _save_company_asset(request.files.get("signature"), "signature")
    if new_signature:
        profile.signature_filename = new_signature

    new_stamp = _save_company_asset(request.files.get("stamp"), "stamp")
    if new_stamp:
        profile.stamp_filename = new_stamp

    db.session.commit()
    flash("Organization profile updated.", "success")
    return redirect(url_for("admin.my_company"))


@admin_bp.route("/my-company/backup/download")
@login_required
@admin_required
def download_backup():
    buffer, timestamp = _build_backup_zip()
    if not buffer:
        flash("Backup is only supported for SQLite databases right now.", "danger")
        return redirect(url_for("admin.my_company"))
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"HSE-Backup-{timestamp}.zip",
    )


@admin_bp.route("/my-company/backup/run", methods=["POST"])
@login_required
@admin_required
def run_auto_backup():
    buffer, timestamp = _build_backup_zip()
    if not buffer:
        flash("Backup is only supported for SQLite databases right now.", "danger")
        return redirect(url_for("admin.my_company"))

    backup_dir = _backups_dir()
    filename = f"HSE-Backup-{timestamp}.zip"
    with open(os.path.join(backup_dir, filename), "wb") as f:
        f.write(buffer.getvalue())

    # Keep only the 10 most recent backups on disk so this folder doesn't
    # grow forever.
    backups = sorted(
        (f for f in os.listdir(backup_dir) if f.endswith(".zip")), reverse=True
    )
    for old in backups[10:]:
        os.remove(os.path.join(backup_dir, old))

    flash(f"Backup saved on the server as {filename}.", "success")
    return redirect(url_for("admin.my_company"))


@admin_bp.route("/my-company/backup/restore", methods=["POST"])
@login_required
@admin_required
def restore_backup():
    db_path = _sqlite_db_path()
    if not db_path:
        flash("Restore is only supported for SQLite databases right now.", "danger")
        return redirect(url_for("admin.my_company"))

    file = request.files.get("backup_file")
    if not file or not file.filename:
        flash("Please choose a .backup file to restore.", "danger")
        return redirect(url_for("admin.my_company"))

    raw = file.read()
    if not raw.startswith(b"SQLite format 3\x00"):
        flash("That file doesn't look like a valid SQLite backup.", "danger")
        return redirect(url_for("admin.my_company"))

    # Snapshot the current db first, in case the restore needs undoing.
    if os.path.exists(db_path):
        safety_copy = db_path + f".before-restore-{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        shutil.copyfile(db_path, safety_copy)

    db.session.remove()
    db.engine.dispose()
    with open(db_path, "wb") as f:
        f.write(raw)

    flash("Backup restored. Please restart the application for it to take full effect.", "success")
    return redirect(url_for("admin.my_company"))