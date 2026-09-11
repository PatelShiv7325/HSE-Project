from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from app import db
from app.models import Payment, Lead

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")


def _payments_view(base_query, title):
    status_filter = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = base_query.join(Lead, Payment.lead_id == Lead.id)
    if status_filter:
        query = query.filter(Payment.status == status_filter)
    if search:
        query = query.filter(
            db.or_(
                Lead.company_name.ilike(f"%{search}%"),
                Lead.client_name.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(Payment.id.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    payments = query.offset((page - 1) * per_page).limit(per_page).all()

    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(page * per_page, total)

    return render_template(
        "accounts/payments.html",
        payments=payments,
        title=title,
        status_filter=status_filter,
        search=search,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total=total,
        start=start,
        end=end,
    )


@accounts_bp.route("/payments")
@login_required
def payments():
    return _payments_view(Payment.query, "Payments")


@accounts_bp.route("/pending-payments")
@login_required
def pending_payments():
    return _payments_view(Payment.query.filter(Payment.status != "complete"), "Pending Payments")


@accounts_bp.route("/payments/<int:payment_id>/update", methods=["POST"])
@login_required
def update_payment(payment_id):
    p = Payment.query.get_or_404(payment_id)
    p.status = request.form.get("status") or p.status
    pct = request.form.get("paid_percentage")
    if pct:
        p.paid_percentage = min(100, max(0, int(pct)))
    db.session.commit()
    flash("Payment updated.", "success")
    return redirect(request.referrer or url_for("accounts.payments"))