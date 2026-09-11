import json
import urllib.parse
import urllib.request
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models import WhatsAppLog, Lead, MessageTemplate, ConfigSetting, EmailLog
from datetime import datetime
from email_service import get_smtp_settings, save_smtp_settings, send_email

notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


# Small helpers to read/write single settings rows in the generic
# ConfigSetting table (key/value), instead of adding new model fields.
def _get_setting(key, default=""):
    row = ConfigSetting.query.filter_by(key=key).first()
    return row.value if row and row.value is not None else default


def _set_setting(key, value, title=""):
    row = ConfigSetting.query.filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.session.add(ConfigSetting(key=key, title=title or key, value=value))


def _check_evolution_connection(api_url, api_key, instance_name):
    """
    Best-effort live check against the Evolution API's connection-state
    endpoint. Returns (connected: bool, connected_number: str | None).
    Never raises -- any network/format problem is treated as "not connected"
    so a bad or unreachable server never crashes this page.

    NOTE: Evolution API's exact response shape can differ by version. This
    reads response["instance"]["state"] == "open" as connected, and
    response["instance"]["owner"] as the linked number, which matches the
    most common Evolution API releases -- adjust the two lookups below if
    your server's JSON shape differs.
    """
    if not (api_url and api_key and instance_name):
        return False, None
    try:
        url = f"{api_url.rstrip('/')}/instance/connectionState/{instance_name}"
        req = urllib.request.Request(url, headers={"apikey": api_key})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        instance = data.get("instance", {})
        connected = instance.get("state") == "open"
        connected_number = instance.get("owner")
        return connected, connected_number
    except Exception:
        return False, None


@notifications_bp.route("/whatsapp-link", methods=["GET"])
@login_required
def whatsapp_link():
    return render_template(
        "notifications/whatsapp_link.html",
        api_url=_get_setting("whatsapp_api_url"),
        api_key=_get_setting("whatsapp_api_key"),
        instance_name=_get_setting("whatsapp_instance_name"),
        connected=_get_setting("whatsapp_connected") == "true",
        connected_number=_get_setting("whatsapp_connected_number"),
    )


# "Save Configuration" button -- stores the 3 settings, then immediately
# attempts a live connection test so the Connection Manager panel reflects
# the real current state right after saving.
@notifications_bp.route("/whatsapp-link/save", methods=["POST"])
@login_required
def whatsapp_link_save():
    api_url = request.form.get("api_url", "").strip()
    api_key = request.form.get("api_key", "").strip()
    instance_name = request.form.get("instance_name", "").strip()

    _set_setting("whatsapp_api_url", api_url, "Evolution API URL")
    _set_setting("whatsapp_api_key", api_key, "Evolution API Key")
    _set_setting("whatsapp_instance_name", instance_name, "WhatsApp Instance Name")

    connected, connected_number = _check_evolution_connection(api_url, api_key, instance_name)
    _set_setting("whatsapp_connected", "true" if connected else "false")
    _set_setting("whatsapp_connected_number", connected_number or "")
    db.session.commit()

    flash("Configuration saved and WhatsApp linked!" if connected
          else "Configuration saved, but couldn't confirm a live WhatsApp connection.",
          "success" if connected else "danger")
    return redirect(url_for("notifications.whatsapp_link"))


# "Disconnect WhatsApp Account" -- best-effort logout call to Evolution API,
# then always clears the locally stored connected state either way.
@notifications_bp.route("/whatsapp-link/disconnect", methods=["POST"])
@login_required
def whatsapp_link_disconnect():
    api_url = _get_setting("whatsapp_api_url")
    api_key = _get_setting("whatsapp_api_key")
    instance_name = _get_setting("whatsapp_instance_name")

    if api_url and api_key and instance_name:
        try:
            url = f"{api_url.rstrip('/')}/instance/logout/{instance_name}"
            req = urllib.request.Request(url, headers={"apikey": api_key}, method="DELETE")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # server may already be disconnected/unreachable -- clear our state regardless

    _set_setting("whatsapp_connected", "false")
    _set_setting("whatsapp_connected_number", "")
    db.session.commit()
    flash("WhatsApp account disconnected.", "success")
    return redirect(url_for("notifications.whatsapp_link"))


@notifications_bp.route("/whatsapp-logs")
@login_required
def whatsapp_logs():
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")
    trigger_filter = request.args.get("trigger", "")
    date_filter = request.args.get("date", "")

    query = WhatsAppLog.query.outerjoin(Lead, WhatsAppLog.lead_id == Lead.id)

    if search:
        query = query.filter(
            db.or_(
                WhatsAppLog.to_number.ilike(f"%{search}%"),
                WhatsAppLog.message.ilike(f"%{search}%"),
                Lead.company_name.ilike(f"%{search}%"),
            )
        )
    if status_filter:
        query = query.filter(WhatsAppLog.status == status_filter)
    if trigger_filter:
        query = query.filter(WhatsAppLog.trigger_source.ilike(f"{trigger_filter}%"))
    if date_filter:
        try:
            day = datetime.strptime(date_filter, "%Y-%m-%d").date()
            query = query.filter(db.func.date(WhatsAppLog.sent_at) == day.isoformat())
        except ValueError:
            pass

    logs = query.order_by(WhatsAppLog.sent_at.desc()).limit(200).all()

    today = datetime.utcnow().date()
    trigger_sources = sorted({
        (l.trigger_source or "").split(" @ ")[0]
        for l in WhatsAppLog.query.filter(WhatsAppLog.trigger_source.isnot(None)).all()
        if l.trigger_source
    })

    return render_template(
        "notifications/whatsapp_logs.html",
        logs=logs,
        search=search,
        status_filter=status_filter,
        trigger_filter=trigger_filter,
        date_filter=date_filter,
        trigger_sources=trigger_sources,
        total_sent=WhatsAppLog.query.filter_by(status="sent").count(),
        total_queued=WhatsAppLog.query.filter_by(status="pending").count(),
        total_duplicate=WhatsAppLog.query.filter_by(status="duplicate_skipped").count(),
        total_failed=WhatsAppLog.query.filter_by(status="failed").count(),
        total_today=WhatsAppLog.query.filter(db.func.date(WhatsAppLog.sent_at) == today.isoformat()).count(),
    )


@notifications_bp.route("/message-format", methods=["GET", "POST"])
@login_required
def message_format():
    if request.method == "POST":
        key = request.form.get("key", "").strip()
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip() or "General"
        body = request.form.get("body", "")
        status = request.form.get("status") or "active"

        existing = MessageTemplate.query.filter_by(key=key).first()
        if existing:
            existing.title = title or existing.title
            existing.category = category
            existing.body = body
            existing.status = status
        else:
            db.session.add(MessageTemplate(key=key, title=title or key, category=category, body=body, status=status))
        db.session.commit()
        flash("Template saved.", "success")
        return redirect(url_for("notifications.message_format"))

    search = request.args.get("q", "").strip()
    category_filter = request.args.get("category", "")

    query = MessageTemplate.query
    if search:
        query = query.filter(
            db.or_(
                MessageTemplate.title.ilike(f"%{search}%"),
                MessageTemplate.key.ilike(f"%{search}%"),
            )
        )
    if category_filter:
        query = query.filter(MessageTemplate.category == category_filter)

    templates = query.order_by(MessageTemplate.category, MessageTemplate.title).all()

    grouped = {}
    for t in templates:
        grouped.setdefault(t.category or "General", []).append(t)

    categories = sorted({t.category or "General" for t in MessageTemplate.query.all()})

    return render_template(
        "notifications/message_format.html",
        grouped=grouped,
        categories=categories,
        search=search,
        category_filter=category_filter,
    )

@notifications_bp.route("/email-settings", methods=["GET"])
@login_required
def email_settings():
    settings = get_smtp_settings()
    recent_logs = EmailLog.query.order_by(EmailLog.sent_at.desc()).limit(15).all()
    return render_template(
        "notifications/email_settings.html",
        settings=settings,
        recent_logs=recent_logs,
    )


@notifications_bp.route("/email-settings/save", methods=["POST"])
@login_required
def email_settings_save():
    save_smtp_settings(
        host=request.form.get("host", "").strip(),
        port=request.form.get("port", "").strip() or "587",
        username=request.form.get("username", "").strip(),
        password=request.form.get("password", "").strip(),
        use_tls=bool(request.form.get("use_tls")),
        from_email=request.form.get("from_email", "").strip(),
        from_name=request.form.get("from_name", "").strip() or "Global HSE Associates",
    )
    flash("Email settings saved.", "success")
    return redirect(url_for("notifications.email_settings"))


# "Send Test Email" button on the settings page -- sends a short test
# message to the currently logged-in admin's own email address so they
# can confirm the SMTP settings actually work before relying on them.
@notifications_bp.route("/email-settings/test", methods=["POST"])
@login_required
def email_settings_test():
    ok = send_email(
        current_user.email,
        "Test email from Global HSE Associates",
        "This is a test email to confirm your SMTP settings are working.",
    )
    flash("Test email sent -- check your inbox." if ok
          else "Couldn't send the test email -- check the log below for the error.",
          "success" if ok else "danger")
    return redirect(url_for("notifications.email_settings"))