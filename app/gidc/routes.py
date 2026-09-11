from flask import Blueprint, render_template
from flask_login import login_required

gidc_bp = Blueprint("gidc", __name__, url_prefix="/gidc")


@gidc_bp.route("/assign-documentation")
@login_required
def assign_documentation():
    return render_template("gidc/coming_soon.html", title="Assign Documentation")


@gidc_bp.route("/documents")
@login_required
def documents():
    return render_template("gidc/coming_soon.html", title="Documents")


@gidc_bp.route("/assign-drawing")
@login_required
def assign_drawing():
    return render_template("gidc/coming_soon.html", title="Assign Drawing")


@gidc_bp.route("/drafting")
@login_required
def drafting():
    return render_template("gidc/coming_soon.html", title="GIDC Drafting")