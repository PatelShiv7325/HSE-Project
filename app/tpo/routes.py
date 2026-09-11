from flask import Blueprint, render_template
from flask_login import login_required

tpo_bp = Blueprint("tpo", __name__, url_prefix="/tpo")


@tpo_bp.route("/assign-documentation")
@login_required
def assign_documentation():
    return render_template("tpo/coming_soon.html", title="Assign Documentation")


@tpo_bp.route("/documents")
@login_required
def documents():
    return render_template("tpo/coming_soon.html", title="Documents")


@tpo_bp.route("/assign-drawing")
@login_required
def assign_drawing():
    return render_template("tpo/coming_soon.html", title="Assign Drawing")


@tpo_bp.route("/drafting")
@login_required
def drafting():
    return render_template("tpo/coming_soon.html", title="TPO Drafting")