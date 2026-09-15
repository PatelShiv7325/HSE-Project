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
    from app.bauda.routes import bauda_bp
    from app.gidc.routes import gidc_bp
    from app.tpo.routes import tpo_bp
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
    app.register_blueprint(bauda_bp)
    app.register_blueprint(gidc_bp)
    app.register_blueprint(tpo_bp)
    app.register_blueprint(workflow_bp)
    app.register_blueprint(inspections_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(companies_bp)

    csrf.exempt(api_bp)

    @app.route("/")
    def index():
        # Bare root URL (e.g. https://your-app.onrender.com/) had no route
        # at all before, so it 404'd -- send people straight to login, or
        # to the dashboard if they're already signed in.
        from flask import redirect, url_for
        from flask_login import current_user
        if current_user.is_authenticated:
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("auth.login"))

    @app.context_processor
    def inject_company():
        return {
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

        drafting_stage_names = ["Architectural Drafting", "Structural Drafting", "3D Elevation Drafting"]
        stage_counts = {
            name: WorkStage.query.filter_by(stage_name=name).filter(WorkStage.status != "done").count()
            for name in drafting_stage_names
        }

        return {
            "nav_counts": {
                "new_lead": Lead.query.filter_by(status="new").count(),
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
            "nav_stage_counts": stage_counts,
        }

    with app.app_context():
        db.create_all()
        _ensure_schema_upgrades()

    return app


def _ensure_schema_upgrades():
    """
    Adds columns to EXISTING tables that db.create_all() can't add on its own
    -- create_all() only creates tables that don't exist yet, it never alters
    a table that's already there. This runs on every app startup, checks
    which columns are missing on each table below, and adds them with a raw
    ALTER TABLE if needed. Safe to run every time; does nothing once the
    columns already exist. Brand new tables (like ConfigSetting) don't need
    an entry here -- db.create_all() already creates those automatically.

    This replaces the old one-off migrate_add_salary.py / migrate_add_from_month.py
    scripts -- there's no separate migration file to remember to run anymore.

    Add a new "table_name": {...} entry any time a future field gets added
    to a table that may already have rows in it.

    IMPORTANT: this talks to `db.engine` (whatever SQLALCHEMY_DATABASE_URI
    actually is -- SQLite locally, PostgreSQL on Render) via SQLAlchemy's
    own dialect-agnostic inspector, instead of hardcoding a raw sqlite3
    connection to a local hse.db file. The old sqlite3-only version silently
    no-op'd on any deploy using Postgres (the file it looked for simply
    doesn't exist in that container), which meant company_id/renewed_from_id
    never actually got added to the live Postgres table -- every page that
    queried InspectionReport then 500'd with "column ... does not exist".
    """
    from sqlalchemy import inspect, text

    # table name -> { new column name: SQL column definition }
    tables_to_upgrade = {
        "user": {
            "salary": "NUMERIC(12, 2) DEFAULT 0",
            "from_month": "VARCHAR(7)",
        },
        "message_template": {
            "title": "VARCHAR(150)",
            "category": "VARCHAR(80) DEFAULT 'General'",
            "status": "VARCHAR(20) DEFAULT 'active'",
        },
        "engineer": {
            "designation": "VARCHAR(80)",
            "license_number": "VARCHAR(80)",
            "license_expiry_date": "DATE",
            "photo_filename": "VARCHAR(255)",
        },
        "whatsapp_log": {
            "lead_id": "INTEGER",
            "trigger_source": "VARCHAR(255)",
            "triggered_by": "VARCHAR(120)",
        },
        "inspection_report": {
            "company_id": "INTEGER",
            "renewed_from_id": "INTEGER",
        },
    }

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    with db.engine.begin() as conn:
        for table_name, columns in tables_to_upgrade.items():
            # Skip tables that don't exist yet -- nothing to upgrade on them
            if table_name not in existing_tables:
                continue

            existing_columns = {col["name"] for col in inspector.get_columns(table_name)}

            for column_name, column_def in columns.items():
                if column_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"))