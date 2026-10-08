"""
Makes the 3 small edits the installed desktop app needs in your EXISTING files:
  1. config.py                 - database + uploads go to a data folder (HSE_DATA_DIR)
  2. app/admin/routes.py       - backups go to that same data folder
  3. app/inspections/routes.py - PDF export also works from the packaged .exe
Run once from the project folder:   python apply_desktop_patch.py
Safe to run twice. Each file is copied to <name>.before_desktop first.
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def patch(rel_path, edits, marker):
    path = os.path.join(ROOT, *rel_path.split("/"))
    if not os.path.exists(path):
        sys.exit(f"ERROR: {rel_path} not found - run this from the HSE_Project folder.")
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    if marker in text:
        print(f"  already patched: {rel_path}")
        return
    crlf = "\r\n" in text
    nl = "\r\n" if crlf else "\n"
    work = text.replace("\r\n", "\n")
    for old, new in edits:
        if work.count(old) != 1:
            sys.exit(f"ERROR: could not find the expected code in {rel_path}:\n{old}\n"
                     "The file was changed after this patch was written - use the manual steps instead.")
        work = work.replace(old, new)
    shutil.copyfile(path, path + ".before_desktop")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(work.replace("\n", nl))
    print(f"  patched: {rel_path}")


print("Applying desktop patch...")

# 1. config.py ---------------------------------------------------------------
patch("config.py", [
    ("basedir = os.path.abspath(os.path.dirname(__file__))\n",
     "basedir = os.path.abspath(os.path.dirname(__file__))\n"
     "# Folder for the database, uploads and backups. The installed desktop app sets HSE_DATA_DIR;\n"
     "# otherwise it is the project folder, exactly as before.\n"
     "DATA_DIR = os.environ.get('HSE_DATA_DIR') or basedir\n"),
    ("'sqlite:///' + os.path.join(basedir, 'hse.db')",
     "'sqlite:///' + os.path.join(DATA_DIR, 'hse.db')"),
    ("os.path.join(basedir, 'uploads')",
     "os.path.join(DATA_DIR, 'uploads')"),
    ("    SQLALCHEMY_TRACK_MODIFICATIONS = False\n",
     "    SQLALCHEMY_TRACK_MODIFICATIONS = False\n    DATA_DIR = DATA_DIR\n"),
], "HSE_DATA_DIR")

# 2. app/admin/routes.py -----------------------------------------------------
patch("app/admin/routes.py", [
    ('    path = os.path.abspath(os.path.join(current_app.root_path, "..", "backups"))\n',
     '    base = current_app.config.get("DATA_DIR") or os.path.join(current_app.root_path, "..")\n'
     '    path = os.path.abspath(os.path.join(base, "backups"))\n'),
], 'current_app.config.get("DATA_DIR")')

# 3. app/inspections/routes.py ----------------------------------------------
FROZEN_BLOCK = '''    if getattr(sys, "frozen", False):
        # Packaged desktop app: the PDF worker is this same .exe started with --pdf-worker.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "in.json")
            out_path = os.path.join(tmp, "out.pdf")
            with open(in_path, "w", encoding="utf-8") as f:
                json.dump({"html": html, "header_html": header_html,
                           "footer_html": footer_html, "fit_one_page": fit_one_page}, f)
            try:
                subprocess.run([sys.executable, "--pdf-worker", in_path, out_path],
                               capture_output=True, timeout=90,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                raise RuntimeError("PDF export timed out -- try again.")
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                with open(out_path, "rb") as f:
                    return f.read()
            error_text = ""
            if os.path.exists(out_path + ".err"):
                with open(out_path + ".err", encoding="utf-8") as f:
                    error_text = f.read()
            raise RuntimeError("PDF export failed: " + error_text.strip()[-500:])

'''
WORKER_LINE = '    worker_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pdf_worker.py")\n'
patch("app/inspections/routes.py", [
    (WORKER_LINE, FROZEN_BLOCK + WORKER_LINE),
], '"--pdf-worker"')

print("Done.")
