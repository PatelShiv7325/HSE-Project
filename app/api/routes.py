from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models import WorkStage

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/tasks/<int:stage_id>/progress", methods=["POST"])
@login_required
def update_progress(stage_id):
    stage = WorkStage.query.get_or_404(stage_id)

    if not current_user.is_admin and stage.assigned_to_id != current_user.id:
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json() or {}
    status = data.get("status")
    if status:
        stage.status = status
        if status == "done":
            stage.completed_at = datetime.utcnow()

    db.session.commit()
    return jsonify({"id": stage.id, "status": stage.status})