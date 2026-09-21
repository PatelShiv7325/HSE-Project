"""
Standalone helper script: renders an HTML string (piped in via stdin, as
UTF-8 bytes) to PDF bytes (written to stdout, as raw bytes) using
Playwright's headless Chromium.

WHY THIS IS A SEPARATE SCRIPT, RUN AS ITS OWN PROCESS
-------------------------------------------------------
_html_to_pdf_bytes() in app/inspections/routes.py runs this as a fresh
subprocess for every PDF, rather than calling Playwright's sync API
directly inside the Flask request. That's deliberate: Playwright's sync
API conflicts with Werkzeug's debug-mode auto-reloader on Windows -- the
reloader's file-watcher can restart the app mid-request, which yanks the
pipe out from under Playwright's Node.js driver process and crashes it
with "Error: EOF: end of file, write". Running in a completely separate,
short-lived OS process every time sidesteps that conflict entirely,
regardless of whether Flask's debug/reloader is on.
"""
import sys
from playwright.sync_api import sync_playwright


def main():
    # Read stdin as raw bytes and decode as UTF-8 explicitly -- Python's
    # sys.stdin.read() uses the platform's default text encoding, which on
    # Windows is often a legacy codepage (cp1252 etc.), not UTF-8, and would
    # corrupt certain characters in report data before they even reach the
    # browser.
    html = sys.stdin.buffer.read().decode("utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        pdf_bytes = page.pdf(format="A4", print_background=True)
        browser.close()

    sys.stdout.buffer.write(pdf_bytes)


if __name__ == "__main__":
    main()