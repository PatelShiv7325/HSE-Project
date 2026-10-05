from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.from_object("config.Config")

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # dd/mm/yyyy everywhere: {{ value|dmy }} or {{ value|dmy('-') }} (custom
    # text when empty). Accepts date/datetime objects and ISO / dd-mm-yyyy
    # strings; anything that isn't a recognisable date (e.g. "Not Applicable")
    # is returned unchanged.
    @app.template_filter("dmy")
    def dmy_filter(value, default=""):
        from datetime import date as _date, datetime as _dt
        if value is None or value == "":
            return default
        if isinstance(value, _dt):
            return value.strftime("%d/%m/%Y")
        if isinstance(value, _date):
            return value.strftime("%d/%m/%Y")
        text = str(value).strip()
        for candidate in (text, text[:10]):
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
                try:
                    return _dt.strptime(candidate, fmt).strftime("%d/%m/%Y")
                except ValueError:
                    pass
        return text

    # Address cell on the PDFs: company name (bold) on line 1, address (normal) on line 2.
    #   {{ data.address|address_block(report.company.name if report.company else '') }}
    @app.template_filter("address_block")
    def address_block_filter(address, company_name=""):
        import re
        from markupsafe import Markup, escape
        text = re.sub(r"\*+", "", str(address or "")).strip()
        company = re.sub(r"\*+", "", str(company_name or "")).strip()
        if not text:
            return ""
        name, rest = "", text
        if "\n" in text:                                    # already typed on 2 lines
            name, rest = [p.strip() for p in text.split("\n", 1)]
        elif company and text.upper().startswith(company.upper()):
            name, rest = company, text[len(company):].lstrip(" ,-")
        else:
            m = re.match(r"^(M/S\.?\s+.+?)[\s,]+((?:PLOT|SURVEY|BLOCK|UNIT|SHED|S\.?\s?NO|R\.?S\.?\s?NO)\b.*)$",
                         text, re.I | re.S)
            if m:
                name, rest = m.group(1).strip(), m.group(2).strip()
            elif company:
                name, rest = company, text
        rest = re.sub(r"\s*\n\s*", ", ", rest).strip()
        # company name bold, address line normal weight
        out = []
        if name:
            out.append("<strong>%s</strong>" % escape(name))
        if rest:
            out.append(str(escape(rest)))
        return Markup("<br>".join(out))

    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    from app.models import User, Lead, Estimation, SiteVisit, Measurement, WorkStage, InspectionReport, Company

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.auth.routes import auth_bp
    from app.admin.routes import admin_bp
    from app.sales.routes import sales_bp
    from app.fieldwork.routes import fieldwork_bp
    from app.dish.routes import dish_bp
    from app.workflow.routes import workflow_bp
    from app.inspections.routes import inspections_bp
    from app.notifications.routes import notifications_bp
    from app.api.routes import api_bp
    from app.accounts.routes import accounts_bp
    from app.companies.routes import companies_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(fieldwork_bp)
    app.register_blueprint(dish_bp)
    app.register_blueprint(workflow_bp)
    app.register_blueprint(inspections_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(companies_bp)

    # NOTE: api_bp is no longer CSRF-exempt -- it uses the logged-in session cookie, so exempting
    # it let any other website change task status on a user's behalf. Fetch calls must send
    # the X-CSRFToken header (base.html already exposes the token in <meta name="csrf-token">).

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    @app.route("/")
    def index():
        # Bare root URL (e.g. https://your-app.onrender.com/) had no route
        # at all before, so it 404'd -- send people straight to login, or
        # to the dashboard if they're already signed in.
        from flask import redirect, url_for
        from flask_login import current_user
        from app.utils import home_url
        if current_user.is_authenticated:
            return redirect(home_url())
        return redirect(url_for("auth.login"))

    @app.context_processor
    def inject_company():
        import os as _os
        try:   # changes automatically whenever theme.css is edited -> no more stale CSS after deploys
            css_version = int(_os.path.getmtime(_os.path.join(app.static_folder, "css", "theme.css")))
        except OSError:
            css_version = 1
        return {
            "css_version": css_version,
            "company_name": app.config["COMPANY_NAME"],
            "company_tagline": app.config["COMPANY_TAGLINE"],
        }

    @app.context_processor
    def inject_nav_counts():
        from flask_login import current_user
        from app.models import Department, Payment, DishCase, DishDocument
        if not current_user.is_authenticated:
            return {}

        todo_query = WorkStage.query.filter(WorkStage.status != "done")
        if not current_user.is_admin:
            todo_query = todo_query.filter(WorkStage.assigned_to_id == current_user.id)

        department_counts = {}
        for dept in Department.query.all():
            department_counts[dept.name] = (
                WorkStage.query.join(Lead, WorkStage.lead_id == Lead.id)
                .filter(Lead.department_id == dept.id, WorkStage.status != "done")
                .count()
            )

        return {
            "nav_counts": {
                "new_lead": Lead.query.filter_by(status="new_lead").count(),
                "estimation": Estimation.query.filter_by(status="pending").count(),
                "site_visit": SiteVisit.query.filter_by(status="pending").count(),
                "assign_measurement": Measurement.query.filter_by(status="pending").count(),
                "measurement": Measurement.query.filter(Measurement.status != "pending").count(),
                "todo": todo_query.count(),
                "pending_payments": Payment.query.filter_by(status="pending").count(),
                "dish_form": DishCase.query.filter_by(form_status="pending").count(),
                "dish_documents": DishDocument.query.filter_by(uploaded=False).count(),
                "dish_drafting": DishCase.query.filter(DishCase.drafting_status != "done").count(),
                "dish_applications": DishCase.query.filter(DishCase.map_application_status != "submitted").count(),
                "dish_liaisoning_applications": DishCase.query.filter(DishCase.liaisoning_status != "done").count(),
                "dish_certificates": DishCase.query.filter_by(stability_status="pending").count(),
                "dish_license": DishCase.query.filter(DishCase.license_status != "submitted").count(),
            },
            "nav_departments": Department.query.all(),
            "nav_department_counts": department_counts,
        }

    with app.app_context():
        db.create_all()
        _ensure_schema_upgrades()

    return app


def _ensure_schema_upgrades():
    """Add any column that exists in a model but not yet in the real DB table."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    dialect = db.engine.dialect

    with db.engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all() already handled brand-new tables
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                col_type = col.type.compile(dialect=dialect)
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'
                ))
                print(f"[schema upgrade] added {table.name}.{col.name}", flush=True)

