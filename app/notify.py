"""
Helpers for creating notifications (shown by the topbar bell and the
Notifications page).

    from app.notify import notify, admin_users, nice_date

    notify(admin_users(), "Work Order Received",
           "Work Order Received for the company ABC on Date %s." % nice_date(),
           url_for("sales.leads"))

Call notify() AFTER your own db.session.commit(). It commits by itself and
never raises, so a notification problem can never break the real action.
"""
from datetime import datetime, timedelta
from flask import current_app
from app import db
from app.models import Notification, User


def admin_users():
    return User.query.filter_by(is_admin=True, is_active_flag=True).all()


def notify(recipients, title, message, link=None):
    """One notification per recipient (User objects or ids; duplicates / None are skipped)."""
    try:
        seen = set()
        for r in recipients:
            uid = getattr(r, "id", r)
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                continue
            if uid in seen:
                continue
            seen.add(uid)
            db.session.add(Notification(user_id=uid, title=title, message=message, link=link))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not create notification '%s'", title)


def nice_date(value=None):
    """'3rd Oct 2026'. value = date/datetime/'YYYY-MM-DD...' string; default = today (India time)."""
    if value is None or value == "":
        d = datetime.utcnow() + timedelta(hours=5, minutes=30)
    elif isinstance(value, str):
        try:
            d = datetime.strptime(value.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return value
    else:
        d = value
    day = d.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return "%d%s %s" % (day, suffix, d.strftime("%b %Y"))