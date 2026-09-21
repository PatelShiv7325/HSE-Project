from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models import WorkStage, Lead, User

workflow_bp = Blueprint("workflow", __name__, url_prefix="/workflow")


@workflow_bp.route("/todo")
@login_required
def todo():
    show = request.args.get("show", "pending")
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = WorkStage.query
    query = query.filter(WorkStage.status != "done") if show == "pending" \
        else query.filter(WorkStage.status == "done")

    if not current_user.is_admin:
        query = query.filter(WorkStage.assigned_to_id == current_user.id)

    if search:
        query = query.join(Lead, WorkStage.lead_id == Lead.id).filter(
            db.or_(
                WorkStage.stage_name.ilike(f"%{search}%"),
                Lead.company_name.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(WorkStage.due_date)

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    stages = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "workflow/todo.html",
        stages=stages,
        show=show,
        search=search,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total=total,
        start=start,
        end=end,
        pending_count=WorkStage.query.filter(WorkStage.status != "done").count(),
        completed_count=WorkStage.query.filter(WorkStage.status == "done").count(),
    )


@workflow_bp.route("/todo/<int:stage_id>/start", methods=["POST"])
@login_required
def start_work(stage_id):
    stage = WorkStage.query.get_or_404(stage_id)
    stage.status = "in_progress"
    db.session.commit()
    flash("Work started.", "success")
    return redirect(request.referrer or url_for("workflow.todo"))


@workflow_bp.route("/todo/<int:stage_id>/complete", methods=["POST"])
@login_required
def complete_work(stage_id):
    stage = WorkStage.query.get_or_404(stage_id)
    stage.status = "done"
    stage.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Marked complete.", "success")
    return redirect(request.referrer or url_for("workflow.todo"))
