"""Run once after first install: python seed.py"""
import os
import secrets
from app import create_app, db
from app.models import User, Role, Department

app = create_app()

with app.app_context():
    db.create_all()

    default_roles = [
        "Sales Executive", "Site Visit Engineer", "Documentation Officer",
        "Drafting Engineer", "Application Officer",
    ]
    for name in default_roles:
        if not Role.query.filter_by(name=name).first():
            db.session.add(Role(name=name))

    default_departments = ["DISH"]
    for name in default_departments:
        if not Department.query.filter_by(name=name).first():
            db.session.add(Department(name=name))

    db.session.commit()

    admin_email = "admin@globalhse.com"
    if not User.query.filter_by(email=admin_email).first():
        admin = User(name="Admin", email=admin_email, is_admin=True)
        admin_password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(12)
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        print(f"Created admin user: {admin_email} / {admin_password}  (shown once -- change it after first login)")
    else:
        print("Admin user already exists.")

    print("Seed complete.")