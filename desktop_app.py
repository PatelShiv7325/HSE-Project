"""
Global HSE Associates - SERVER desktop app.

* Starts the Flask app in the background (port 5052, reachable from other PCs
  on the same network) and shows it in its own window (no address bar).
* Closing the window stops the server.
* Also acts as the PDF worker when started with  --pdf-worker  (used when the
  app is packaged as an .exe).

Put this file next to app.py (REPLACES the earlier desktop_app.py).

Options:  --show-ip   show the address other PCs must type into the Client app
          --lan       (running from source only) also listen for other PCs
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)          # True inside the packaged .exe

# ---- PDF worker mode (the .exe calls itself to render PDFs) ------------------
if "--pdf-worker" in sys.argv:
    _i = sys.argv.index("--pdf-worker")
    from app.pdf_worker import run_files
    sys.exit(run_files(sys.argv[_i + 1], sys.argv[_i + 2]))

import time
import socket
import secrets
import threading
import subprocess
import webbrowser
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
if not FROZEN:
    sys.path.insert(0, BASE_DIR)
    os.chdir(BASE_DIR)

PORT = 5052        # was 5002 - the other (reference) HSE app also uses 5002, so they clashed
PING_PATH = "/__globalhse_ping__"      # lets us tell OUR server apart from any other program on the port
PING_TEXT = b"GLOBALHSE-MAIN-APP"
LOCAL_URL = f"http://127.0.0.1:{PORT}/"

APP_ROOT = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "GlobalHSE")
# Installed .exe -> data lives in %LOCALAPPDATA%\GlobalHSE\data (Program Files is read-only).
# Running from source -> data stays in the project folder, exactly as before.
DATA_DIR = os.environ.get("HSE_DATA_DIR") or (os.path.join(APP_ROOT, "data") if FROZEN else BASE_DIR)
PROFILE_DIR = os.path.join(APP_ROOT, "window-profile")
LOG_FILE = os.path.join(APP_ROOT, "desktop.log")
for _d in (DATA_DIR, PROFILE_DIR):
    os.makedirs(_d, exist_ok=True)
os.environ["HSE_DATA_DIR"] = DATA_DIR            # read by config.py

_log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = _log                   # windowed .exe has no console


# ---- small helpers -----------------------------------------------------------
def message(title, text):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, text, parent=root)
        root.destroy()
    except Exception as exc:                      # no tkinter -> at least keep it in the log
        print(f"[{title}] {text}  ({exc})")


def port_in_use(port=PORT):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def with_ping(wsgi):
    """Wrap the Flask app so  /__globalhse_ping__  answers with our identity text."""
    def wrapped(environ, start_response):
        if environ.get("PATH_INFO") == PING_PATH:
            start_response("200 OK", [("Content-Type", "text/plain"),
                                      ("Content-Length", str(len(PING_TEXT)))])
            return [PING_TEXT]
        return wsgi(environ, start_response)
    return wrapped


def is_our_server(host="127.0.0.1", port=PORT):
    """True only if the program answering on this port is the Global HSE (main) server."""
    import http.client
    try:
        conn = http.client.HTTPConnection(host, port, timeout=2)
        conn.request("GET", PING_PATH)
        return conn.getresponse().read(64).strip() == PING_TEXT
    except Exception:
        return False


def lan_ips():
    ips = []
    try:                                          # address of the network card in use (no data is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips or ["(could not detect - run ipconfig)"]


def show_address():
    ips = "\n".join(f"    {ip}" for ip in lan_ips())
    message(
        "Global HSE Associates - Server address",
        "Other computers on this network install the CLIENT app and type this address:\n\n"
        f"{ips}\n\n"
        "This PC must stay switched on, with this app open, while others use it.",
    )


def find_browser():
    candidates = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            candidates.append(os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"))
            candidates.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    return next((c for c in candidates if os.path.exists(c)), None)


def ensure_secret_key():
    """Installed app: make one random SECRET_KEY per installation and keep it."""
    if os.environ.get("SECRET_KEY") or not FROZEN:
        return
    path = os.path.join(DATA_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        os.environ["SECRET_KEY"] = f.read().strip()


def ensure_seed(flask_app):
    """Same as seed.py: default roles/departments, and an admin user if there is none.
    Returns (email, password) when a new admin was created, else None."""
    from app import db
    from app.models import User, Role, Department
    created = None
    with flask_app.app_context():
        for name in ["Sales Executive", "Site Visit Engineer", "Documentation Officer",
                     "Drafting Engineer", "Application Officer"]:
            if not Role.query.filter_by(name=name).first():
                db.session.add(Role(name=name))
        if not Department.query.filter_by(name="DISH").first():
            db.session.add(Department(name="DISH"))
        db.session.commit()
        if not User.query.filter_by(is_admin=True).first():
            email = "admin@globalhse.com"
            password = os.environ.get("ADMIN_PASSWORD") or secrets.token_hex(5)
            admin = User(name="Admin", email=email, is_admin=True)
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()
            created = (email, password)
    return created


# ---- main --------------------------------------------------------------------
def main():
    if "--show-ip" in sys.argv:
        show_address()
        return

    server = None
    new_admin = None
    if not port_in_use():
        ensure_secret_key()
        import config                              # noqa: F401  (config is loaded by name -> keep PyInstaller aware)
        from werkzeug.serving import make_server
        from app import create_app
        flask_app = create_app()
        new_admin = ensure_seed(flask_app)
        flask_app.wsgi_app = with_ping(flask_app.wsgi_app)
        host = "0.0.0.0" if (FROZEN or "--lan" in sys.argv) else "127.0.0.1"
        server = make_server(host, PORT, flask_app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        for _ in range(100):
            if port_in_use():
                break
            time.sleep(0.1)
    elif not is_our_server():
        # Something else (for example the other HSE app) is already using this port.
        message("Global HSE Associates",
                f"Port {PORT} is being used by another program, so this app cannot start.\n\n"
                "Close the other program (or restart the PC) and open this app again.")
        return

    if new_admin:
        message("Global HSE Associates - first login",
                f"A new admin account was created.\n\nEmail:  {new_admin[0]}\nPassword:  {new_admin[1]}\n\n"
                "Write this down and change the password after you log in.")
    if FROZEN and server is not None:
        flag = os.path.join(APP_ROOT, "address_shown")
        if not os.path.exists(flag):
            show_address()
            open(flag, "w").close()

    browser = find_browser()
    if browser:
        proc = subprocess.Popen([
            browser, f"--app={LOCAL_URL}", f"--user-data-dir={PROFILE_DIR}",
            "--window-size=1400,900", "--no-first-run", "--no-default-browser-check",
        ])
        proc.wait()                                 # until the window is closed
    else:
        webbrowser.open(LOCAL_URL)
        while server is not None:
            time.sleep(60)

    if server is not None:
        server.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        message("Global HSE Associates", f"The app could not start.\nDetails are in:\n{LOG_FILE}")
        raise