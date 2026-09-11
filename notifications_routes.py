from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from app import db
from app.models import WhatsAppLog, MessageTemplate
from app.notifications.whatsapp_service import (
    get_evolution_settings, save_evolution_settings, check_connection_status, disconnect,
)

notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@notifications_bp.route("/whatsapp-link", methods=["GET", "POST"])
@login_required
def whatsapp_link():
    if request.method == "POST":
        save_evolution_settings(
            url=request.form.get("api_url", "").strip(),
            api_key=request.form.get("api_key", "").strip(),
            instance=request.form.get("instance_name", "").strip(),
        )
        flash("Configuration saved.", "success")
        return redirect(url_for("notifications.whatsapp_link"))

    return render_template(
        "notifications/whatsapp_link.html",
        settings=get_evolution_settings(),
        connection=check_connection_status(),
    )


@notifications_bp.route("/whatsapp-link/disconnect", methods=["POST"])
@login_required
def whatsapp_disconnect():
    disconnect()
    flash("WhatsApp account disconnected.", "success")
    return redirect(url_for("notifications.whatsapp_link"))


@notifications_bp.route("/whatsapp-logs")
@login_required
def whatsapp_logs():
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()

    query = WhatsAppLog.query
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(WhatsAppLog.to_number.ilike(like), WhatsAppLog.message.ilike(like))
        )
    if status_filter:
        query = query.filter(WhatsAppLog.status == status_filter)
    logs = query.order_by(WhatsAppLog.sent_at.desc()).limit(300).all()

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        "total_sent": WhatsAppLog.query.filter_by(status="sent").count(),
        "queued": WhatsAppLog.query.filter_by(status="queued").count(),
        "duplicate_skipped": WhatsAppLog.query.filter_by(status="duplicate_skipped").count(),
        "failed": WhatsAppLog.query.filter_by(status="failed").count(),
        "today": WhatsAppLog.query.filter(WhatsAppLog.sent_at >= today_start).count(),
    }

    return render_template(
        "notifications/whatsapp_logs.html",
        logs=logs, stats=stats, search=search, status_filter=status_filter,
    )


# Same category set as your reference dashboard, so templates group the way
# you're used to. Add more here any time -- new categories just show up as
# a new group on the page once a template uses them.
TEMPLATE_CATEGORIES = [
    "Accounts & Billing",
    "DISH Application",
    "GIDC Application",
    "BAUDA Application",
    "TPO Application",
    "General",
]


@notifications_bp.route("/message-format", methods=["GET", "POST"])
@login_required
def message_format():
    if request.method == "POST":
        key = request.form.get("key", "").strip()
        title = request.form.get("title", "").strip() or key
        category = request.form.get("category", "General").strip() or "General"
        body = request.form.get("body", "")
        status = request.form.get("status", "active")

        existing = MessageTemplate.query.filter_by(key=key).first()
        if existing:
            existing.title = title
            existing.category = category
            existing.body = body
            existing.status = status
        else:
            db.session.add(MessageTemplate(
                key=key, title=title, category=category, body=body, status=status,
            ))
        db.session.commit()
        flash("Template saved.", "success")
        return redirect(url_for("notifications.message_format"))

    search = request.args.get("q", "").strip()
    category_filter = request.args.get("category", "").strip()
    editing_id = request.args.get("edit", type=int)
    editing = MessageTemplate.query.get(editing_id) if editing_id else None

    query = MessageTemplate.query
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(MessageTemplate.title.ilike(like), MessageTemplate.key.ilike(like))
        )
    if category_filter:
        query = query.filter_by(category=category_filter)
    templates = query.order_by(MessageTemplate.category, MessageTemplate.title).all()

    grouped = {}
    for t in templates:
        grouped.setdefault(t.category or "General", []).append(t)

    return render_template(
        "notifications/message_format.html",
        grouped=grouped,
        categories=TEMPLATE_CATEGORIES,
        search=search,
        category_filter=category_filter,
        editing=editing,
    )