"""Small shared helpers used by several blueprints."""
from functools import wraps
from flask import flash, redirect, request, url_for
from flask_login import current_user


def home_url():
    """Where a signed-in user lands: admins -> dashboard, everyone else -> their todo list."""
    if current_user.is_authenticated and current_user.is_admin:
        return url_for("admin.dashboard")
    return url_for("workflow.todo")


def admin_required(fn):
    """Use UNDER @login_required. Non-admins are sent to their todo list (never back to
    the login page -- that caused a redirect loop for signed-in employees)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.path))
        if not current_user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("workflow.todo"))
        return fn(*args, **kwargs)
    return wrapper


def get_per_page(default=10, maximum=100):
    """?per_page= from the URL, kept between 1 and `maximum` (0 or negative used to crash pagination)."""
    value = request.args.get("per_page", default, type=int)
    if value is None:
        value = default
    return max(1, min(maximum, value))
