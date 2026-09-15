"""
One-time migration: adds new columns to the existing dish_case table for
the Stability Certificates page (uploaded structure/certificate documents
and a "review sent" timestamp).

Your database already has data in it, so we can't just call
db.create_all() again -- SQLAlchemy only creates NEW tables, it never
alters existing ones. This script runs raw ALTER TABLE statements instead,
so your existing DISH cases are kept.

HOW TO RUN (from the project root, same folder as app.py):
    python migrate_add_stability_certificates.py

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

    cur.execute("PRAGMA table_info(dish_case)")
    existing_columns = {row[1] for row in cur.fetchall()}

    new_columns = [
        ("stability_structure_filename", "VARCHAR(255)"),
        ("stability_certificate_filename", "VARCHAR(255)"),
        ("stability_review_sent_at", "DATETIME"),
    ]

    for name, col_type in new_columns:
        if name in existing_columns:
            print(f"'{name}' column already exists on dish_case -- nothing to do.")
        else:
            cur.execute(f"ALTER TABLE dish_case ADD COLUMN {name} {col_type}")
            conn.commit()
            print(f"Added '{name}' column to the dish_case table.")

    conn.close()


if __name__ == "__main__":
    main()