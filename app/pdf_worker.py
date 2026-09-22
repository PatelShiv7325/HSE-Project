"""
Standalone helper script: renders an HTML string (piped in via stdin, as
UTF-8 bytes) to PDF bytes (written to stdout, as raw bytes) using
Playwright's headless Chromium.
"""
import sys
import json
from playwright.sync_api import sync_playwright

PAGE_WIDTH_MM = 210
PAGE_HEIGHT_MM = 297
MARGIN_TOP_MM = 34
MARGIN_BOTTOM_MM = 20
MARGIN_LEFT_MM = 5
MARGIN_RIGHT_MM = 5
MIN_FIT_SCALE = 0.4  # don't shrink text past this even if content still overflows


def mm_to_px(mm):
    return mm / 25.4 * 96


def main():
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    html = payload["html"]
    header_html = payload.get("header_html")
    footer_html = payload.get("footer_html")
    fit_one_page = payload.get("fit_one_page", False)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Render at the same width the printed content area will actually
        # have (page width minus left/right margins), so scrollHeight below
        # matches what will really overflow the page.
        content_width_px = int(mm_to_px(PAGE_WIDTH_MM - MARGIN_LEFT_MM - MARGIN_RIGHT_MM))
        page.set_viewport_size({"width": content_width_px, "height": 1000})
        page.set_content(html, wait_until="load")

        pdf_kwargs = {"format": "A4", "print_background": True}
        if header_html or footer_html:
            pdf_kwargs["display_header_footer"] = True
            pdf_kwargs["header_template"] = header_html or "<div></div>"
            pdf_kwargs["footer_template"] = footer_html or "<div></div>"
            pdf_kwargs["margin"] = {
                "top": f"{MARGIN_TOP_MM}mm", "bottom": f"{MARGIN_BOTTOM_MM}mm",
                "left": f"{MARGIN_LEFT_MM}mm", "right": f"{MARGIN_RIGHT_MM}mm",
            }

        if fit_one_page:
            content_height_px = page.evaluate("document.body.scrollHeight")
            available_height_px = mm_to_px(PAGE_HEIGHT_MM - MARGIN_TOP_MM - MARGIN_BOTTOM_MM)
            if content_height_px > available_height_px:
                scale = max(MIN_FIT_SCALE, available_height_px / content_height_px)
                pdf_kwargs["scale"] = round(scale, 3)

        pdf_bytes = page.pdf(**pdf_kwargs)
        browser.close()

    sys.stdout.buffer.write(pdf_bytes)


if __name__ == "__main__":
    main()