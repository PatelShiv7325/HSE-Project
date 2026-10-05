import re
from urllib.parse import urlparse
from flask import Blueprint, render_template, redirect, url_for, flash, request, Response, abort
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.auth.forms import LoginForm
from app.models import User
from app.utils import home_url

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# ---- simple login brute-force protection (per worker process, in memory) ----
import time
_FAILS = {}                 # key -> [timestamps of failed attempts]
_WINDOW = 15 * 60           # look back 15 minutes
_MAX_PER_ACCOUNT = 5        # failed attempts per (ip + email)
_MAX_PER_IP = 20            # failed attempts per ip, any email


def _recent(key):
    now = time.time()
    hits = [t for t in _FAILS.get(key, []) if now - t < _WINDOW]
    if hits:
        _FAILS[key] = hits
    else:
        _FAILS.pop(key, None)
    return hits


def _is_locked(ip, email):
    return len(_recent(("acct", ip, email))) >= _MAX_PER_ACCOUNT or len(_recent(("ip", ip))) >= _MAX_PER_IP


def _record_failure(ip, email):
    now = time.time()
    _FAILS.setdefault(("acct", ip, email), []).append(now)
    _FAILS.setdefault(("ip", ip), []).append(now)
    if len(_FAILS) > 5000:                      # keep memory bounded
        for k in list(_FAILS):
            _recent(k)


def _is_safe_next_url(target):
    """Only allow redirecting to a path on this same site (blocks open redirect)."""
    if not target:
        return False
    parsed = urlparse(target)
    return parsed.scheme == "" and parsed.netloc == "" and target.startswith("/")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(home_url())

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        ip = request.remote_addr or "?"
        if _is_locked(ip, email):
            flash("Too many failed attempts. Please wait 15 minutes and try again.", "danger")
            return render_template("auth/login.html", form=form), 429
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(form.password.data):
            _FAILS.pop(("acct", ip, email), None)
            login_user(user)
            next_page = request.args.get("next")
            if not _is_safe_next_url(next_page):
                next_page = None
            return redirect(next_page or home_url())
        _record_failure(ip, email)
        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# MY PROFILE
# The logged-in user's own account settings -- name/email, and a separate
# change-password form. Anyone can reach this for their own account (not
# admin-only); it never lets a user change their own role/admin flag.
# ---------------------------------------------------------------------------
@auth_bp.route("/profile")
@login_required
def profile():
    return render_template("auth/profile.html")


# Profile photo rules: JPG / PNG / GIF only, max 1024K. The type is checked from the
# file's real first bytes, not from its name, so a renamed file cannot sneak in.
MAX_PHOTO_BYTES = 1024 * 1024


def _image_mime(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


@auth_bp.route("/avatar/<int:user_id>")
@login_required
def avatar(user_id):
    """Serves a user's profile photo (stored in the database)."""
    user = User.query.get_or_404(user_id)
    if not user.avatar_mime or not user.avatar_data:
        abort(404)
    resp = Response(user.avatar_data, mimetype=user.avatar_mime)
    resp.headers["Cache-Control"] = "private, max-age=86400"   # the ?v= in the URL busts it on change
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@auth_bp.route("/profile/update", methods=["POST"])
@login_required
def update_profile():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").lower().strip()
    contact = request.form.get("contact", "").strip()

    if not name or not email:
        flash("Name and email are required.", "danger")
        return redirect(url_for("auth.profile"))

    if contact and (len(contact) > 20 or not re.fullmatch(r"[0-9+\-()\s]+", contact)):
        flash("Contact can only contain digits, spaces, + - ( ) and be at most 20 characters.", "danger")
        return redirect(url_for("auth.profile"))

    existing = User.query.filter(User.email == email, User.id != current_user.id).first()
    if existing:
        flash("That email is already used by another account.", "danger")
        return redirect(url_for("auth.profile"))

    # optional new profile photo
    photo = request.files.get("photo")
    if photo and photo.filename:
        data = photo.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            flash("That photo is larger than 1024K. Please choose a smaller one.", "danger")
            return redirect(url_for("auth.profile"))
        mime = _image_mime(data)
        if not mime:
            flash("Please choose a JPG, GIF or PNG image.", "danger")
            return redirect(url_for("auth.profile"))
        current_user.avatar_data = data
        current_user.avatar_mime = mime
        current_user.avatar_version = (current_user.avatar_version or 0) + 1

    current_user.name = name
    current_user.email = email
    current_user.contact = contact or None
    db.session.commit()
    flash("Profile updated.", "success")
    return redirect(url_for("auth.profile"))


@auth_bp.route("/profile/photo/remove", methods=["POST"])
@login_required
def remove_photo():
    current_user.avatar_data = None
    current_user.avatar_mime = None
    current_user.avatar_version = (current_user.avatar_version or 0) + 1
    db.session.commit()
    flash("Profile photo removed.", "success")
    return redirect(url_for("auth.profile"))


@auth_bp.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not current_user.check_password(current_password):
        flash("Current password is incorrect.", "danger")
        return redirect(url_for("auth.profile"))
    if len(new_password) < 8:
        flash("New password must be at least 8 characters.", "danger")
        return redirect(url_for("auth.profile"))
    if new_password != confirm_password:
        flash("New password and confirmation do not match.", "danger")
        return redirect(url_for("auth.profile"))

    current_user.set_password(new_password)
    db.session.commit()
    flash("Password updated.", "success")
    return redirect(url_for("auth.profile"))