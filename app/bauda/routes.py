from flask import Blueprint, render_template
from flask_login import login_required

bauda_bp = Blueprint("bauda", __name__, url_prefix="/bauda")


@bauda_bp.route("/assign-documentation")
@login_required
def assign_documentation():
    return render_template("bauda/coming_soon.html", title="Assign Documentation")


@bauda_bp.route("/form")
@login_required
def form():
    return render_template("bauda/coming_soon.html", title="Form")


@bauda_bp.route("/documents")
@login_required
def documents():
    return render_template("bauda/coming_soon.html", title="Documents")


@bauda_bp.route("/assign-drawing")
@login_required
def assign_drawing():
    return render_template("bauda/coming_soon.html", title="Assign Drawing")


@bauda_bp.route("/drafting")
@login_required
def drafting():
    return render_template("bauda/coming_soon.html", title="BAUDA Drafting")


@bauda_bp.route("/approved")
@login_required
def approved():
    return render_template("bauda/coming_soon.html", title="Approved")