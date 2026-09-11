"""
Central place for anything that sends an email.

Nothing else in the app should touch smtplib directly. Instead, call
send_email(...) from here. It:
  1. Sends the message through the SMTP server configured on the
     Email Settings page (stored in ConfigSetting, same as the WhatsApp
     integration -- no code change needed to switch providers).
  2. Logs the attempt to EmailLog -- automatically capturing which
     function/file/line made the call (trigger_source) and who triggered
     it (the logged-in user, or "SYSTEM CRON / CLI" if there's no
     logged-in user -- e.g. a scheduled job).

That means every future automated email (new-user welcome, password
reset, reminders, etc.) gets full traceability for free just by calling
this function -- nothing needs to be wired up per call site.
"""
import inspect
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from flask import has_request_context
from flask_login import current_user

from app import db
from app.models import EmailLog, ConfigSetting


def _get_setting(key, default=""):
    row = ConfigSetting.query.filter_by(key=key).first()
    return row.value if row and row.value else default


def get_smtp_settings():
    return {
        "host": _get_setting("smtp_host"),
        "port": _get_setting("smtp_port", "587"),
        "username": _get_setting("smtp_username"),
        "password": _get_setting("smtp_password"),
        "use_tls": _get_setting("smtp_use_tls", "true") == "true",
        "from_email": _get_setting("smtp_from_email"),
        "from_name": _get_setting("smtp_from_name", "Global HSE Associates"),
    }


def save_smtp_settings(host, port, username, password, use_tls, from_email, from_name):
    _upsert_setting("smtp_host", "SMTP Host", host)
    _upsert_setting("smtp_port", "SMTP Port", port)
    _upsert_setting("smtp_username", "SMTP Username", username)
    # Only overwrite the stored password if a new one was actually typed --
    # the settings form re-renders with this field blank for security, so
    # an empty submit means "keep the existing password", not "clear it".
    if password:
        _upsert_setting("smtp_password", "SMTP Password", password)
    _upsert_setting("smtp_use_tls", "SMTP Use TLS", "true" if use_tls else "false")
    _upsert_setting("smtp_from_email", "From Email", from_email)
    _upsert_setting("smtp_from_name", "From Name", from_name)
    db.session.commit()


def _upsert_setting(key, title, value):
    row = ConfigSetting.query.filter_by(key=key).first()
    if not row:
        row = ConfigSetting(key=key, title=title, field_type="text")
        db.session.add(row)
    row.value = value


def send_email(to_email, subject, body_text, body_html=None):
    """
    Sends `subject`/`body_text` (with an optional richer `body_html`
    alternative) to `to_email` over SMTP and logs the result to EmailLog.
    Returns True if the SMTP server accepted the message, False otherwise.
    Never raises -- any config/network/auth problem is caught, logged with
    status "failed" and a short error, and simply returns False so a
    broken mail server never crashes the page that triggered the send
    (e.g. creating a user).
    """
    settings = get_smtp_settings()

    caller = inspect.stack()[1]
    trigger_source = f"{caller.function}() @ {os.path.basename(caller.filename)}:{caller.lineno}"

    if has_request_context() and current_user.is_authenticated:
        triggered_by = current_user.name
    else:
        triggered_by = "SYSTEM CRON / CLI"

    status = "failed"
    error = None
    try:
        if not (settings["host"] and settings["username"] and settings["password"] and settings["from_email"]):
            raise RuntimeError("SMTP is not configured. Go to Email Settings and fill in your mail server details.")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{settings['from_name']} <{settings['from_email']}>"
        msg["To"] = to_email
        msg.set_content(body_text)
        if body_html:
            msg.add_alternative(body_html, subtype="html")

        port = int(settings["port"] or 587)
        if settings["use_tls"]:
            with smtplib.SMTP(settings["host"], port, timeout=15) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(settings["username"], settings["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(settings["host"], port, timeout=15, context=ssl.create_default_context()) as server:
                server.login(settings["username"], settings["password"])
                server.send_message(msg)
        status = "sent"
    except Exception as exc:
        status = "failed"
        error = str(exc)[:255]

    db.session.add(EmailLog(
        to_email=to_email,
        subject=subject,
        status=status,
        error=error,
        trigger_source=trigger_source,
        triggered_by=triggered_by,
        sent_at=datetime.utcnow(),
    ))
    db.session.commit()
    return status == "sent"


def send_new_user_email(user, plain_password, login_url):
    """
    Sends the "you've been added" welcome email to a newly created user,
    with their login email and temporary password. Called right after a
    User row is created -- see app/admin/routes.py: create_user().
    """
    subject = f"You've been added to {get_smtp_settings()['from_name']}"
    body_text = (
        f"Hi {user.name},\n\n"
        f"An account has been created for you.\n\n"
        f"Login email: {user.email}\n"
        f"Temporary password: {plain_password}\n\n"
        f"Log in here: {login_url}\n\n"
        f"Please change your password after logging in.\n"
    )
    body_html = f"""
    <div style="font-family:Arial,sans-serif; font-size:14px; color:#222;">
      <p>Hi {user.name},</p>
      <p>An account has been created for you.</p>
      <table style="margin:16px 0; border-collapse:collapse;">
        <tr><td style="padding:4px 12px 4px 0; color:#666;">Login email</td><td><strong>{user.email}</strong></td></tr>
        <tr><td style="padding:4px 12px 4px 0; color:#666;">Temporary password</td><td><strong>{plain_password}</strong></td></tr>
      </table>
      <p><a href="{login_url}" style="background:#0d6efd; color:#fff; padding:8px 16px; border-radius:4px; text-decoration:none;">Log in</a></p>
      <p style="color:#666; font-size:12.5px;">Please change your password after logging in.</p>
    </div>
    """
    return send_email(user.email, subject, body_text, body_html)