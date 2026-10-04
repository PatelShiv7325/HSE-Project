"""
Daily job: emails a reminder for every inspection report whose Reminder Date
(Certification Date + 5 months) has arrived, i.e. one month before the
Next Examination Due date (Certification Date + 6 months).

Run once a day (cron / Render cron job / Windows Task Scheduler):
    python send_exam_reminders.py

Each report is mailed only once per Reminder Date (flag stored in
report.data["reminder_sent_on"]). Renewed reports are skipped. The mail goes
to the user who created the report, using the SMTP settings on the Email
Settings page via email_service.send_email().
"""
from datetime import date

from app import create_app, db
from app.models import InspectionReport, User
from app.inspections.routes import FORM_LABELS, _parse_date
from email_service import send_email


def main():
    app = create_app()
    with app.app_context():
        today = date.today()
        reports = InspectionReport.query.all()
        renewed_ids = {r.renewed_from_id for r in reports if r.renewed_from_id}
        sent = 0

        for r in reports:
            data = dict(r.data or {})
            if r.id in renewed_ids or data.get("reminder_sent_on"):
                continue
            try:
                reminder = _parse_date(data.get("reminder_date"))
                due = _parse_date(data.get("next_exam_date"))
            except (ValueError, TypeError):
                continue
            if not reminder or reminder > today:
                continue

            user = db.session.get(User, r.created_by_id) if r.created_by_id else None
            if not user or not user.email:
                continue

            label = FORM_LABELS.get(r.form_type, r.form_type)
            company = r.occupier_name or "-"
            due_txt = due.strftime("%d-%m-%Y") if due else "-"
            subject = f"Reminder: {label} examination due on {due_txt} ({company})"
            body = (
                f"Hello {user.name},\n\n"
                f"This is a reminder that the {label} examination is due soon.\n\n"
                f"Report No.   : {r.report_no}\n"
                f"Company      : {company}\n"
                f"Certified on : {data.get('certification_date', '-')}\n"
                f"Next exam due: {due_txt}\n\n"
                f"Please arrange the re-examination before the due date.\n"
            )
            if send_email(user.email, subject, body):
                data["reminder_sent_on"] = today.isoformat()
                r.data = data          # reassign so the JSON column is saved
                db.session.commit()
                sent += 1

        print(f"Reminder emails sent: {sent}")


if __name__ == "__main__":
    main()