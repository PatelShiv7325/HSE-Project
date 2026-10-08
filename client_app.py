"""
Global HSE Associates - CLIENT desktop app.

Just a window: it opens the HSE server running on another PC of the network.
The first time (or with --change-ip) it asks for the server PC's address.
Needs nothing but Edge/Chrome. Put this file next to app.py.
"""
import os
import re
import sys
import json
import socket
import shutil
import subprocess

PORT = 5052        # must match the server (was 5002, which clashed with the other HSE app)
PING_PATH = "/__globalhse_ping__"
PING_TEXT = b"GLOBALHSE-MAIN-APP"
APP_ROOT = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "GlobalHSE-Client")
CONFIG_FILE = os.path.join(APP_ROOT, "client-config-v2.json")
PROFILE_DIR = os.path.join(APP_ROOT, "window-profile")
LOG_FILE = os.path.join(APP_ROOT, "client.log")
os.makedirs(PROFILE_DIR, exist_ok=True)
_log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = _log


def normalize(text):
    """'192.168.1.5', '192.168.1.5:5052', 'http://pc-name:5052/' -> (host, port)"""
    text = re.sub(r"^https?://", "", (text or "").strip()).split("/")[0]
    if not text:
        return None, None
    if ":" in text:
        host, _, port = text.rpartition(":")
        return (host, int(port)) if port.isdigit() and host else (None, None)
    return text, PORT


def reachable(host, port, timeout=2.5):
    """True only if OUR Global HSE server answers there (not just any program on that port)."""
    import http.client
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", PING_PATH)
        return conn.getresponse().read(64).strip() == PING_TEXT
    except Exception:
        return False


def ask(prompt, initial=""):
    import tkinter as tk
    from tkinter import simpledialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    answer = simpledialog.askstring("Global HSE Associates - Server address", prompt,
                                    initialvalue=initial, parent=root)
    root.destroy()
    return answer


def find_browser():
    candidates = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            candidates.append(os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"))
            candidates.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
    for name in ("msedge", "chrome", "google-chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    return next((c for c in candidates if os.path.exists(c)), None)


def main():
    saved = ""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f).get("server", "")
        except (OSError, ValueError):
            pass

    host, port = normalize(saved)
    prompt = "Type the address of the HSE server PC (example: 192.168.1.20):"
    if host is None or "--change-ip" in sys.argv:
        text = ask(prompt, saved)
        if not text:
            return
        host, port = normalize(text)

    while host is None or not reachable(host, port):
        text = ask(f"Could not find the Global HSE server at {host or 'that address'}.\n"
                   "Check that the server PC is on and its HSE app is open,\n"
                   "or type a different address:", host or "")
        if not text:
            return
        host, port = normalize(text)

    os.makedirs(APP_ROOT, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump({"server": f"{host}:{port}"}, f)

    url = f"http://{host}:{port}/"
    browser = find_browser()
    if browser:
        subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={PROFILE_DIR}",
                          "--window-size=1400,900", "--no-first-run", "--no-default-browser-check"]).wait()
    else:
        import webbrowser
        webbrowser.open(url)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise