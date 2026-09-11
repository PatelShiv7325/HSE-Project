"""
Central place for anything that sends a WhatsApp message.

Nothing else in the app should call the Evolution API directly. Instead,
call send_whatsapp_message(...) from here. It:
  1. Sends the message through your Evolution API instance.
  2. Logs the attempt to WhatsAppLog -- automatically capturing which
     function/file/line made the call (trigger_source) and who triggered it
     (the logged-in user, or "SYSTEM CRON / CLI" if there's no logged-in
     user -- e.g. a scheduled job).

That means every future automated trigger (document-upload reminders,
assignment notifications, etc.) gets full traceability for free just by
calling this function -- nothing needs to be wired up per call site.
"""
import inspect
import os
from datetime import datetime

import requests
from flask import has_request_context
from flask_login import current_user

from app import db
from app.models import WhatsAppLog, ConfigSetting


def _get_setting(key, default=""):
    row = ConfigSetting.query.filter_by(key=key).first()
    return row.value if row and row.value else default


def get_evolution_settings():
    return {
        "url": _get_setting("evolution_api_url").rstrip("/"),
        "api_key": _get_setting("evolution_api_key"),
        "instance": _get_setting("whatsapp_instance_name"),
    }


def save_evolution_settings(url, api_key, instance):
    _upsert_setting("evolution_api_url", "Evolution API URL", url)
    _upsert_setting("evolution_api_key", "Evolution API Key (Master Key)", api_key)
    _upsert_setting("whatsapp_instance_name", "WhatsApp Instance Name", instance)
    db.session.commit()


def _upsert_setting(key, title, value):
    row = ConfigSetting.query.filter_by(key=key).first()
    if not row:
        row = ConfigSetting(key=key, title=title, field_type="text")
        db.session.add(row)
    row.value = value


def check_connection_status():
    """
    Calls Evolution API's connectionState endpoint.
    Returns {"connected": bool, "number": str or None}.
    Never raises -- any network/config problem just reads as "not connected".
    """
    settings = get_evolution_settings()
    if not settings["url"] or not settings["instance"]:
        return {"connected": False, "number": None}
    try:
        resp = requests.get(
            f"{settings['url']}/instance/connectionState/{settings['instance']}",
            headers={"apikey": settings["api_key"]},
            timeout=8,
        )
        if resp.ok:
            data = resp.json()
            instance_data = data.get("instance", data)
            state = instance_data.get("state", "")
            number = instance_data.get("owner") or instance_data.get("number")
            return {"connected": state == "open", "number": number}
    except requests.RequestException:
        pass
    return {"connected": False, "number": None}


def disconnect():
    """Logs the connected WhatsApp session out of Evolution API."""
    settings = get_evolution_settings()
    if not settings["url"] or not settings["instance"]:
        return False
    try:
        resp = requests.delete(
            f"{settings['url']}/instance/logout/{settings['instance']}",
            headers={"apikey": settings["api_key"]},
            timeout=10,
        )
        return resp.ok
    except requests.RequestException:
        return False


def send_whatsapp_message(to_number, message, lead=None):
    """
    Sends `message` to `to_number` via Evolution API and logs the result.
    `lead` is optional -- pass a Lead instance to link the log row to it.
    Returns True if Evolution API accepted the send, False otherwise.
    """
    settings = get_evolution_settings()

    # Identify exactly which function/file/line called this, so the Logs
    # page can show a real trigger source without any manual bookkeeping.
    caller = inspect.stack()[1]
    trigger_source = f"{caller.function}() @ {os.path.basename(caller.filename)}:{caller.lineno}"

    if has_request_context() and current_user.is_authenticated:
        triggered_by = current_user.name
    else:
        triggered_by = "SYSTEM CRON / CLI"

    status = "failed"
    try:
        if not settings["url"] or not settings["instance"]:
            raise RuntimeError("Evolution API is not configured.")
        resp = requests.post(
            f"{settings['url']}/message/sendText/{settings['instance']}",
            headers={"apikey": settings["api_key"]},
            json={"number": to_number, "text": message},
            timeout=15,
        )
        status = "sent" if resp.ok else "failed"
    except requests.RequestException:
        status = "failed"
    except RuntimeError:
        status = "failed"

    db.session.add(WhatsAppLog(
        to_number=to_number,
        message=message,
        status=status,
        lead_id=lead.id if lead else None,
        trigger_source=trigger_source,
        triggered_by=triggered_by,
        sent_at=datetime.utcnow(),
    ))
    db.session.commit()
    return status == "sent"