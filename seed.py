"""Run once after first install: python seed.py"""
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

    default_departments = ["DISH", "GIDC", "BAUDA"]
    for name in default_departments:
        if not Department.query.filter_by(name=name).first():
            db.session.add(Department(name=name))

    db.session.commit()

    admin_email = "admin@globalhse.com"
    if not User.query.filter_by(email=admin_email).first():
        admin = User(name="Admin", email=admin_email, is_admin=True)
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        print(f"Created admin user: {admin_email} / admin123  (change this password immediately)")
    else:
        print("Admin user already exists.")

    print("Seed complete.")