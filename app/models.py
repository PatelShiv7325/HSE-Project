from datetime import datetime
import re
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db


class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    users = db.relationship("User", backref="role", lazy=True)


class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)


class Company(db.Model):
    """
    Master record for a client company/site whose statutory inspection
    certificates (Form 9, Form 10, Form 11, PSV, Centrifuge...) we issue.
    One Company can have many InspectionReport rows across form types --
    see Company.reports below and InspectionReport.company_id.
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    address = db.Column(db.Text)
    occupier_name = db.Column(db.String(255))
    registration_no = db.Column(db.String(100))
    license_no = db.Column(db.String(100))
    company_code = db.Column(db.String(100))  # short internal reference code (like "company_id" in older exports)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    reports = db.relationship(
        "InspectionReport", backref="company", lazy=True,
        order_by="InspectionReport.report_date.desc()",
    )

    @property
    def report_count(self):
        return len(self.reports)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("role.id"), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    is_active_flag = db.Column(db.Boolean, default=True)
    salary = db.Column(db.Numeric(12, 2), default=0)  # monthly salary, used for productivity widgets
    from_month = db.Column(db.String(7))  # employment start month, e.g. "2026-01" -- shown as "-" if unset
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_flag


class Engineer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    specialization = db.Column(db.String(80))
    # A single user can hold more than one Engineer row (e.g. licensed as both
    # "Engineer" and "Structure Engineer"), so there is intentionally no
    # unique constraint on user_id here.
    designation = db.Column(db.String(80))
    license_number = db.Column(db.String(80))
    license_expiry_date = db.Column(db.Date)
    photo_filename = db.Column(db.String(255))
    user = db.relationship("User", backref="engineer_profile")


class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(150), nullable=False)
    company_address = db.Column(db.String(255))
    client_name = db.Column(db.String(120))
    contact_no = db.Column(db.String(20))
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"))
    status = db.Column(db.String(30), default="new")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    department = db.relationship("Department")
    estimations = db.relationship("Estimation", backref="lead", cascade="all, delete-orphan")
    site_visits = db.relationship("SiteVisit", backref="lead", cascade="all, delete-orphan")
    work_stages = db.relationship("WorkStage", backref="lead", cascade="all, delete-orphan")
    payments = db.relationship("Payment", backref="lead", cascade="all, delete-orphan")


class Estimation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2))
    status = db.Column(db.String(30), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SiteVisit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), nullable=False)
    engineer_id = db.Column(db.Integer, db.ForeignKey("engineer.id"))
    scheduled_date = db.Column(db.DateTime)
    status = db.Column(db.String(30), default="pending")

    engineer = db.relationship("Engineer")
    measurement = db.relationship("Measurement", backref="site_visit", uselist=False,
                                   cascade="all, delete-orphan")


class Measurement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_visit_id = db.Column(db.Integer, db.ForeignKey("site_visit.id"), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    status = db.Column(db.String(30), default="pending")

    assigned_to = db.relationship("User")


class WorkStage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), nullable=False)
    stage_name = db.Column(db.String(50), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("role.id"))
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    designer_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    map_reference = db.Column(db.String(150))
    due_date = db.Column(db.DateTime)
    status = db.Column(db.String(30), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    role = db.relationship("Role")
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    designer = db.relationship("User", foreign_keys=[designer_id])

    @property
    def is_overdue(self):
        return (
            self.status != "done"
            and self.due_date is not None
            and self.due_date < datetime.utcnow()
        )


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2))
    paid_percentage = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="estimate_generated")
    # estimate_generated, advance_received, pending, complete
    date = db.Column(db.DateTime, default=datetime.utcnow)


class WhatsAppLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    to_number = db.Column(db.String(20))
    message = db.Column(db.Text)
    status = db.Column(db.String(20))
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"))
    trigger_source = db.Column(db.String(255))   # e.g. "send_dish_update() @ dish/routes.py:41"
    triggered_by = db.Column(db.String(120))     # a user's name, or "SYSTEM CRON / CLI"
    attachment_filename = db.Column(db.String(255))
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)

    lead = db.relationship("Lead")


class MessageTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True)
    title = db.Column(db.String(150))
    category = db.Column(db.String(80), default="General")
    body = db.Column(db.Text)
    status = db.Column(db.String(20), default="active")  # active, inactive

    @property
    def dynamic_keywords(self):
        """Every {PLACEHOLDER} found in the body, e.g. {COMPANY_NAME}, {LEAD_NUMBER}."""
        return sorted(set(re.findall(r"\{([A-Za-z0-9_]+)\}", self.body or "")))

class DishCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), nullable=False)

    map_status = db.Column(db.String(30), default="new")  # new, revised, revised_with_extension, not_in_scope

    documentation_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    stability_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    drafting_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    drafting_deadline = db.Column(db.Date)
    online_application_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    liaisoning_map_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    license_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    liaisoning_license_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    form_status = db.Column(db.String(20), default="pending")  # pending, done

    drafting_status = db.Column(db.String(30), default="file_upload_pending")
    # file_upload_pending, internal_qc_pending, company_approval_pending, qc_rejected, company_rejected, done

    map_portal_id = db.Column(db.String(120))
    map_portal_password = db.Column(db.String(120))
    map_application_status = db.Column(db.String(20), default="online_pending")  # online_pending, offline_pending, submitted

    liaisoning_status = db.Column(db.String(30), default="regional_forward_pending")
    # regional_forward_pending, regional_approval_pending, query, hard_copy_pending, done

    stability_type = db.Column(db.String(10))  # new, renew
    stability_status = db.Column(db.String(20), default="pending")  # pending, done

    license_type = db.Column(db.String(10))  # new, renew
    license_portal_id = db.Column(db.String(120))
    license_portal_password = db.Column(db.String(120))
    license_status = db.Column(db.String(20), default="online_pending")  # online_pending, submitted

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lead = db.relationship("Lead")
    documentation_user = db.relationship("User", foreign_keys=[documentation_user_id])
    stability_user = db.relationship("User", foreign_keys=[stability_user_id])
    drafting_user = db.relationship("User", foreign_keys=[drafting_user_id])
    online_application_user = db.relationship("User", foreign_keys=[online_application_user_id])
    liaisoning_map_user = db.relationship("User", foreign_keys=[liaisoning_map_user_id])
    license_user = db.relationship("User", foreign_keys=[license_user_id])
    liaisoning_license_user = db.relationship("User", foreign_keys=[liaisoning_license_user_id])

    documents = db.relationship("DishDocument", backref="case", cascade="all, delete-orphan")


class DishDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dish_case_id = db.Column(db.Integer, db.ForeignKey("dish_case.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    uploaded = db.Column(db.Boolean, default=False)


class BaudaCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), nullable=False)

    documentation_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    drawing_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    drafting_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    form_status = db.Column(db.String(20), default="pending")
    drafting_status = db.Column(db.String(30), default="pending")
    approved_status = db.Column(db.String(20), default="pending")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lead = db.relationship("Lead")
    documentation_user = db.relationship("User", foreign_keys=[documentation_user_id])
    drawing_user = db.relationship("User", foreign_keys=[drawing_user_id])
    drafting_user = db.relationship("User", foreign_keys=[drafting_user_id])

    documents = db.relationship("BaudaDocument", backref="case", cascade="all, delete-orphan")


class BaudaDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bauda_case_id = db.Column(db.Integer, db.ForeignKey("bauda_case.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    uploaded = db.Column(db.Boolean, default=False)

class GidcCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), nullable=False)

    documentation_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    drawing_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    drafting_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    drafting_status = db.Column(db.String(30), default="pending")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lead = db.relationship("Lead")
    documentation_user = db.relationship("User", foreign_keys=[documentation_user_id])
    drawing_user = db.relationship("User", foreign_keys=[drawing_user_id])
    drafting_user = db.relationship("User", foreign_keys=[drafting_user_id])

    documents = db.relationship("GidcDocument", backref="case", cascade="all, delete-orphan")


class GidcDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    gidc_case_id = db.Column(db.Integer, db.ForeignKey("gidc_case.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    uploaded = db.Column(db.Boolean, default=False)


class TpoCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), nullable=False)

    documentation_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    drawing_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    drafting_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    drafting_status = db.Column(db.String(30), default="pending")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lead = db.relationship("Lead")
    documentation_user = db.relationship("User", foreign_keys=[documentation_user_id])
    drawing_user = db.relationship("User", foreign_keys=[drawing_user_id])
    drafting_user = db.relationship("User", foreign_keys=[drafting_user_id])

    documents = db.relationship("TpoDocument", backref="case", cascade="all, delete-orphan")


class TpoDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tpo_case_id = db.Column(db.Integer, db.ForeignKey("tpo_case.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    uploaded = db.Column(db.Boolean, default=False)


class InspectionReport(db.Model):
    """
    Generic storage for the DISH-style statutory inspection certificates
    (Form 9 - Hoists/Lifts, Form 10 - Lifting Machinery, Form 11 - GFR 61,
    PSV Certificates, Centrifuge Machine reports, ...).

    Rather than one table per form type, every form's fields live in the
    `data` JSON column. `form_type` says which form it is ("form9",
    "form10", "form11", "psv", "centrifuge") so the same table can grow to
    hold every certificate type without a schema migration each time.
    """
    id = db.Column(db.Integer, primary_key=True)
    form_type = db.Column(db.String(30), nullable=False, index=True)
    report_no = db.Column(db.String(80), unique=True, nullable=False)
    report_date = db.Column(db.Date)
    occupier_name = db.Column(db.String(200))  # denormalized for fast list/search
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True, index=True)
    renewed_from_id = db.Column(db.Integer, db.ForeignKey("inspection_report.id"), nullable=True)
    data = db.Column(db.JSON, nullable=False, default=dict)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship("User", foreign_keys=[created_by_id])


class EmailLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    to_email = db.Column(db.String(120))
    subject = db.Column(db.String(255))
    status = db.Column(db.String(20))  # "sent" or "failed"
    error = db.Column(db.String(255))  # short reason when status == "failed"
    trigger_source = db.Column(db.String(255))   # e.g. "create_user() @ admin/routes.py:449"
    triggered_by = db.Column(db.String(120))     # a user's name, or "SYSTEM CRON / CLI"
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)


class ConfigSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    title = db.Column(db.String(150), nullable=False)
    value = db.Column(db.String(255))
    file_path = db.Column(db.String(255))
    field_type = db.Column(db.String(20), default="text")  # "number" or "file"