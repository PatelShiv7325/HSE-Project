"""Automatic update for the installed Global HSE SERVER app.

How it works
  1. You push code to GitHub  ->  the GitHub Action (.github/workflows/release.yml) runs the tests,
     builds the installer and publishes it as a new "Release".
  2. Each time the server app starts it asks GitHub "is there a newer release?" (one small request).
  3. If yes it asks the user, BACKS UP the database first, downloads the installer, checks its
     SHA-256 checksum, and runs it. The installer replaces the program files (your data in
     %LOCALAPPDATA%\\GlobalHSE\\data is never touched) and starts the app again.
Any problem (no internet, GitHub down, bad download) is logged and the app just starts normally.

Private repository?  Put a read-only GitHub token in  <data folder>\\update_token.txt
(or the HSE_UPDATE_TOKEN environment variable).  A public repository needs no token.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

GITHUB_REPO = os.environ.get("HSE_UPDATE_REPO") or "PatelShiv7325/HSE-Project"
RELEASE_API = "https://api.github.com/repos/{repo}/releases/latest"
INSTALLER_NAME = "GlobalHSE-Server-Setup.exe"      # must match OutputBaseFilename in installer_server.iss
ASK_BEFORE_UPDATING = True                         # False = update without asking
REMIND_AFTER_HOURS = 24                            # if the user says "No", ask again after this long
KEEP_BACKUPS = 5                                   # how many pre-update backups to keep
CHECK_TIMEOUT = 5                                  # seconds to wait for GitHub before giving up


# ---- versions ----------------------------------------------------------------------------
def parse_version(text):
    m = re.match(r"^\s*v?(\d+(?:\.\d+){0,3})", str(text or ""))
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _pad(v):
    return tuple(v) + (0,) * (4 - len(v))


def find_update(release, current):
    """Return info about the release if it is newer than `current` and fully uploaded, else None."""
    if release.get("draft") or release.get("prerelease"):
        return None
    new, cur = parse_version(release.get("tag_name")), parse_version(current)
    if not new or not cur or _pad(new) <= _pad(cur):
        return None
    assets = {a.get("name"): a for a in release.get("assets") or []}
    exe, sha = assets.get(INSTALLER_NAME), assets.get(INSTALLER_NAME + ".sha256")
    if not exe or not sha:
        return None                                # release exists but upload not finished yet
    return {"version": ".".join(str(p) for p in new), "installer": exe, "sha": sha}


# ---- talking to GitHub ---------------------------------------------------------------------
class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub redirects asset downloads to another host; never send our token there."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urlparse(newurl).netloc != urlparse(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


class Http:
    def __init__(self, token=None):
        self.token = token
        self.opener = urllib.request.build_opener(_StripAuthOnRedirect())

    def _req(self, url, accept):
        headers = {"User-Agent": "GlobalHSE-Updater", "Accept": accept,
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token and urlparse(url).netloc == "api.github.com":
            headers["Authorization"] = "Bearer " + self.token
        return urllib.request.Request(url, headers=headers)

    def asset_url(self, asset):
        # private repo: the API url (needs the token); public repo: the plain download link
        return asset["url"] if self.token else asset["browser_download_url"]

    def json(self, url):
        with self.opener.open(self._req(url, "application/vnd.github+json"), timeout=CHECK_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))

    def text(self, url):
        with self.opener.open(self._req(url, "application/octet-stream"), timeout=CHECK_TIMEOUT) as r:
            return r.read(4096).decode("utf-8", "replace")

    def download(self, url, dest, progress=None):
        with self.opener.open(self._req(url, "application/octet-stream"), timeout=60) as r, \
                open(dest, "wb") as f:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)


def load_token(data_dir):
    token = os.environ.get("HSE_UPDATE_TOKEN")
    path = os.path.join(data_dir, "update_token.txt")
    if not token and os.path.exists(path):
        token = Path(path).read_text(encoding="utf-8").strip()
    return token or None


# ---- integrity -------------------------------------------------------------------------------
def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def expected_sha(text):
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text or "")
    return m.group(1).lower() if m else None


# ---- safety backup ---------------------------------------------------------------------------
def backup_data(data_dir, old_version, new_version, keep=KEEP_BACKUPS):
    """Copy hse.db + secret.key to <data>\\update-backups\\pre-update-... Returns the folder."""
    root = os.path.join(data_dir, "update-backups")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = os.path.join(root, f"pre-update-{stamp}-{old_version}-to-{new_version}")
    os.makedirs(folder, exist_ok=True)
    db_path = os.path.join(data_dir, "hse.db")
    if os.path.exists(db_path):
        out = os.path.join(folder, "hse.db")
        try:                                       # consistent copy even while the DB is in use
            src = sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)
            dst = sqlite3.connect(out)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
        except Exception:                          # damaged DB: keep a raw copy instead
            shutil.copy2(db_path, out)
    key_path = os.path.join(data_dir, "secret.key")
    if os.path.exists(key_path):
        shutil.copy2(key_path, os.path.join(folder, "secret.key"))
    old = sorted(d for d in os.listdir(root) if d.startswith("pre-update-"))
    for name in old[:-keep] if keep else old:
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    return folder


# ---- "No thanks" memory ----------------------------------------------------------------------
def _state_file(app_root):
    return os.path.join(app_root, "update_state.json")


def _recently_declined(app_root, version):
    try:
        s = json.loads(Path(_state_file(app_root)).read_text(encoding="utf-8"))
        return s.get("declined") == version and time.time() - s.get("at", 0) < REMIND_AFTER_HOURS * 3600
    except Exception:
        return False


def _remember_declined(app_root, version):
    try:
        os.makedirs(app_root, exist_ok=True)
        Path(_state_file(app_root)).write_text(json.dumps({"declined": version, "at": time.time()}), encoding="utf-8")
    except OSError:
        pass


# ---- windows (tkinter ships with Python; any failure just means "no window") ---------------------
def ask_yes_no(title, text):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        answer = messagebox.askyesno(title, text, parent=root)
        root.destroy()
        return bool(answer)
    except Exception:
        return False


def show_info(title, text):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, text, parent=root)
        root.destroy()
    except Exception:
        print(f"[{title}] {text}")


def progress_window():
    """Returns (update(done, total), close())."""
    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        root.title("Global HSE Associates - Updating")
        root.resizable(False, False)
        root.attributes("-topmost", True)
        label = tk.Label(root, text="Downloading the update...", padx=24, pady=12)
        label.pack()
        bar = ttk.Progressbar(root, length=340, maximum=100)
        bar.pack(padx=24, pady=(0, 16))
        root.update()

        def update(done, total):
            pct = int(done * 100 / total) if total else 0
            label.config(text=f"Downloading the update... {pct}%  ({done // 1048576} MB)")
            bar["value"] = pct
            root.update()

        def close():
            try:
                root.destroy()
            except Exception:
                pass
        return update, close
    except Exception:
        return (lambda done, total: None), (lambda: None)


def launch_installer(path):
    """Run the installer with the Windows permission (UAC) prompt. Raises if it did not start."""
    if os.name != "nt":
        raise OSError("automatic update works on Windows only")
    import ctypes
    params = "/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS"
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", path, params, os.path.dirname(path), 1)
    if rc <= 32:                                   # e.g. the user clicked "No" on the permission prompt
        raise OSError(f"installer did not start (code {rc})")


# ---- the whole flow -----------------------------------------------------------------------------
def run_update_flow(data_dir, app_root, current=None, http=None, ask=ask_yes_no, notify=show_info,
                    progress=progress_window, launch=launch_installer, log=print):
    """Returns True when the installer has been started (the caller must exit right away)."""
    if os.environ.get("HSE_NO_UPDATE") == "1":
        return False
    if current is None:
        try:
            from app_version import __version__ as current
        except Exception:
            current = "0.0.0"
    http = http or Http(load_token(data_dir))

    try:
        info = find_update(http.json(RELEASE_API.format(repo=GITHUB_REPO)), current)
    except Exception as exc:                       # offline, rate-limited, bad token ... -> carry on
        log(f"[update] check skipped: {exc}")
        return False
    if not info:
        log(f"[update] version {current} is up to date")
        return False
    version = info["version"]
    log(f"[update] new version {version} available (running {current})")
    if _recently_declined(app_root, version):
        return False
    if ASK_BEFORE_UPDATING and not ask(
            "Global HSE Associates - Update available",
            f"A new version of Global HSE Associates ({version}) is available.\n"
            f"You are using version {current}.\n\nUpdate now?\n\n"
            "  - Your data is backed up first\n"
            "  - The app restarts by itself in about a minute\n"
            "  - Windows will ask for permission: click Yes"):
        _remember_declined(app_root, version)
        return False

    try:
        folder = backup_data(data_dir, current, version)
        log(f"[update] data backed up to {folder}")
    except Exception as exc:
        log(f"[update] backup failed, update cancelled: {exc}")
        notify("Update cancelled", "The safety backup of your data could not be made, so the update was "
                                   "not started. The app will open with the current version.")
        return False

    work = os.path.join(tempfile.gettempdir(), "globalhse-update")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    dest = os.path.join(work, INSTALLER_NAME)
    bar_update, bar_close = progress()
    try:
        wanted = expected_sha(http.text(http.asset_url(info["sha"])))
        if not wanted:
            raise ValueError("checksum file is unreadable")
        http.download(http.asset_url(info["installer"]), dest, bar_update)
        if sha256_of(dest) != wanted:
            raise ValueError("the download is damaged (checksum does not match)")
    except Exception as exc:
        bar_close()
        log(f"[update] download failed: {exc}")
        notify("Update failed", "The update could not be downloaded, so nothing was changed.\n"
                                "The app will open with the current version and try again next time.")
        return False
    bar_close()

    try:
        launch(dest)
    except Exception as exc:
        log(f"[update] installer not started: {exc}")
        notify("Update not started", "The update was cancelled. The app will open with the current version.")
        return False
    log("[update] installer started, closing the app so it can be replaced")
    return True