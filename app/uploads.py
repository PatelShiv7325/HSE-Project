"""Private file uploads.

Files are stored in UPLOAD_ROOT (a folder OUTSIDE app/static, so the web server never
serves them directly) and are only delivered through the login-protected route
/files/<kind>/<filename>.  On Render, set UPLOAD_ROOT to a Persistent Disk path
(e.g. /var/data/uploads) so files survive deploys.
"""
import os
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, redirect, request, send_from_directory, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}          # no SVG: SVG can carry scripts
DOC_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "png", "jpg", "jpeg"}
DRAWING_EXT = DOC_EXT | {"dwg", "dxf", "zip"}

ALLOWED_EXT = {
    "config": DOC_EXT,
    "engineers": IMAGE_EXT,
    "company": IMAGE_EXT,
    "drafting": DRAWING_EXT,
    "stability": DRAWING_EXT,
}

files_bp = Blueprint("files", __name__, url_prefix="/files")


class UploadError(ValueError):
    """Raised for a rejected upload; the app-wide handler (see app/__init__.py) flashes it."""


def _looks_like_image(head, ext):
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return head[:6] in (b"GIF87a", b"GIF89a")
    if ext == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return True


def upload_dir(kind):
    path = os.path.join(current_app.config["UPLOAD_ROOT"], kind)
    os.makedirs(path, exist_ok=True)
    return path


def save_upload(file_obj, kind, prefix=""):
    """Validate + store an uploaded file. Returns the stored filename, or None if nothing was chosen."""
    if not file_obj or not file_obj.filename:
        return None
    original = file_obj.filename
    stem, dot, ext = original.rpartition(".")
    ext = ext.lower() if dot else ""
    if ext not in ALLOWED_EXT[kind]:
        raise UploadError("File type '.%s' is not allowed here. Allowed: %s."
                          % (ext or "none", ", ".join(sorted(ALLOWED_EXT[kind]))))
    if kind in ("engineers", "company") or ext in IMAGE_EXT:
        head = file_obj.stream.read(16)
        file_obj.stream.seek(0)
        if not _looks_like_image(head, ext):
            raise UploadError("That file is not a real .%s image." % ext)
    base = secure_filename(stem or "file") or "file"
    stored = "%s%s_%s.%s" % (prefix, uuid4().hex, base[:60], ext)
    file_obj.save(os.path.join(upload_dir(kind), stored))
    return stored


def find_upload(kind, filename):
    """Full path of a stored file, or None. Also checks the OLD app/static/uploads
    location so files uploaded before this change keep working."""
    if not filename or kind not in ALLOWED_EXT:
        return None
    for base in (os.path.join(current_app.config["UPLOAD_ROOT"], kind),
                 os.path.join(current_app.root_path, "static", "uploads", kind)):
        path = os.path.abspath(os.path.join(base, filename))
        if path.startswith(os.path.abspath(base) + os.sep) and os.path.isfile(path):
            return path
    return None


@files_bp.route("/<kind>/<path:filename>")
@login_required
def serve(kind, filename):
    path = find_upload(kind, filename)
    if not path:
        abort(404)
    return send_from_directory(os.path.dirname(path), os.path.basename(path))


def register(app):
    """Hook everything into the app (called from create_app)."""
    app.register_blueprint(files_bp)

    @app.errorhandler(UploadError)
    def _bad_upload(err):
        flash(str(err), "danger")
        return redirect(request.referrer or url_for("index"))

    @app.before_request
    def _block_legacy_public_uploads():
        # Old files are still reachable through /files/..., but never directly via /static/.
        if request.path.startswith("/static/uploads/"):
            abort(404)
