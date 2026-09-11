from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from app import db
from app.models import DishCase, Lead, User

dish_bp = Blueprint("dish", __name__, url_prefix="/dish")


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
    return render_template("dish/coming_soon.html", title="Form")


@dish_bp.route("/documents")
@login_required
def documents():
    return render_template("dish/coming_soon.html", title="Documents")


@dish_bp.route("/drafting")
@login_required
def drafting():
    return render_template("dish/coming_soon.html", title="Drafting")


@dish_bp.route("/applications-dashboard")
@login_required
def dish_applications():
    return render_template("dish/coming_soon.html", title="Dish Applications")


@dish_bp.route("/applications")
@login_required
def applications():
    return render_template("dish/coming_soon.html", title="Applications")


@dish_bp.route("/liaisoning-of-applications")
@login_required
def liaisoning_applications():
    return render_template("dish/coming_soon.html", title="Liaisoning of Applications")


@dish_bp.route("/certificates")
@login_required
def certificates():
    return render_template("dish/coming_soon.html", title="Certificates")


@dish_bp.route("/license")
@login_required
def license_page():
    return render_template("dish/coming_soon.html", title="License")


@dish_bp.route("/liaisoning-of-license")
@login_required
def liaisoning_license():
    return render_template("dish/coming_soon.html", title="Liaisoning of License")