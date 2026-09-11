"""
One-time migration: adds the new engineer profile columns to the existing
`engineer` table (designation, license_number, license_expiry_date,
photo_filename).

Your database already has data in it, so we can't just call db.create_all()
again -- SQLAlchemy only creates NEW tables, it never alters existing ones.
This script runs raw ALTER TABLE statements instead, so your existing data
is kept.

HOW TO RUN (from the project root, same folder as app.py):
    python migrate_add_engineer_fields.py

Safe to run more than once -- it checks whether each column already exists
before trying to add it.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "hse.db")

# column_name -> SQL type used in the ALTER TABLE statement
COLUMNS_TO_ADD = {
    "designation": "VARCHAR(80)",
    "license_number": "VARCHAR(80)",
    "license_expiry_date": "DATE",
    "photo_filename": "VARCHAR(255)",
}


def main():
    if not os.path.exists(DB_PATH):
        print(f"Could not find {DB_PATH}. Run this from your project root folder.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(engineer)")
    existing_columns = {row[1] for row in cur.fetchall()}

    for column_name, column_type in COLUMNS_TO_ADD.items():
        if column_name in existing_columns:
            print(f"'{column_name}' column already exists on the engineer table -- nothing to do.")
        else:
            cur.execute(f"ALTER TABLE engineer ADD COLUMN {column_name} {column_type}")
            conn.commit()
            print(f"Added '{column_name}' column to the engineer table.")

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()