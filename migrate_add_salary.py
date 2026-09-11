"""
One-time migration: adds new columns to the existing tables.

Your database already has data in it (leads, payments, etc.), so we can't just
call db.create_all() again -- SQLAlchemy only creates NEW tables, it never
alters existing ones. This script runs raw ALTER TABLE statements instead, so
your existing data is kept.

HOW TO RUN (from the project root, same folder as app.py):
    python migrate_add_salary.py

Safe to run more than once -- it checks whether each column already exists
before trying to add it.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "hse.db")


def main():
    if not os.path.exists(DB_PATH):
        print(f"Could not find {DB_PATH}. Run this from your project root folder.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(user)")
    existing_columns = {row[1] for row in cur.fetchall()}

    if "salary" in existing_columns:
        print("'salary' column already exists on the user table -- nothing to do.")
    else:
        cur.execute("ALTER TABLE user ADD COLUMN salary NUMERIC(12, 2) DEFAULT 0")
        conn.commit()
        print("Added 'salary' column to the user table.")

    cur.execute("PRAGMA table_info(lead)")
    lead_columns = {row[1] for row in cur.fetchall()}

    if "company_address" in lead_columns:
        print("'company_address' column already exists on the lead table -- nothing to do.")
    else:
        cur.execute("ALTER TABLE lead ADD COLUMN company_address VARCHAR(255)")
        conn.commit()
        print("Added 'company_address' column to the lead table.")

    cur.execute("PRAGMA table_info(work_stage)")
    ws_columns = {row[1] for row in cur.fetchall()}

    if "designer_id" not in ws_columns:
        cur.execute("ALTER TABLE work_stage ADD COLUMN designer_id INTEGER")
        conn.commit()
        print("Added 'designer_id' column to the work_stage table.")

    if "map_reference" not in ws_columns:
        cur.execute("ALTER TABLE work_stage ADD COLUMN map_reference VARCHAR(150)")
        conn.commit()
        print("Added 'map_reference' column to the work_stage table.")

    cur.execute("PRAGMA table_info(payment)")
    payment_columns = {row[1] for row in cur.fetchall()}

    if "paid_percentage" not in payment_columns:
        cur.execute("ALTER TABLE payment ADD COLUMN paid_percentage INTEGER DEFAULT 0")
        conn.commit()
        print("Added 'paid_percentage' column to the payment table.")

    cur.execute("UPDATE payment SET status = 'estimate_generated' WHERE status IS NULL OR status = ''")
    conn.commit()

    cur.execute("PRAGMA table_info(whats_app_log)")
    wa_columns = {row[1] for row in cur.fetchall()}

    if "lead_id" not in wa_columns:
        cur.execute("ALTER TABLE whats_app_log ADD COLUMN lead_id INTEGER")
        conn.commit()
        print("Added 'lead_id' column to the whats_app_log table.")

    if "trigger_source" not in wa_columns:
        cur.execute("ALTER TABLE whats_app_log ADD COLUMN trigger_source VARCHAR(255)")
        conn.commit()
        print("Added 'trigger_source' column to the whats_app_log table.")

    if "triggered_by" not in wa_columns:
        cur.execute("ALTER TABLE whats_app_log ADD COLUMN triggered_by VARCHAR(120)")
        conn.commit()
        print("Added 'triggered_by' column to the whats_app_log table.")

    if "attachment_filename" not in wa_columns:
        cur.execute("ALTER TABLE whats_app_log ADD COLUMN attachment_filename VARCHAR(255)")
        conn.commit()
        print("Added 'attachment_filename' column to the whats_app_log table.")

    conn.close()


if __name__ == "__main__":
    main()