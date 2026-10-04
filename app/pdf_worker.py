"""
Standalone helper script: renders an HTML string (piped in via stdin, as
UTF-8 bytes) to PDF bytes (written to stdout, as raw bytes) using
Playwright's headless Chromium.
"""
import sys
import json
import re
from playwright.sync_api import sync_playwright

PAGE_WIDTH_MM = 210
PAGE_HEIGHT_MM = 297
MARGIN_TOP_MM = 34  # header box must end exactly here -- see _pdf_header.html
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

        # Chromium prints the header/footer template before any web font
        # inside it has finished loading, so a custom @font-face in the
        # header (e.g. Bank Gothic) would render blank. Pre-loading the
        # same @font-face in the main page first puts it in Chromium's
        # cache, so it is ready the moment the header is drawn.
        if header_html:
            font_faces = re.findall(r"@font-face\s*\{[^}]*\}", header_html, flags=re.S)
            if font_faces:
                families = set(re.findall(r"font-family\s*:\s*['\"]?([^;'\"]+)['\"]?\s*;", " ".join(font_faces)))
                page.add_style_tag(content="\n".join(font_faces))
                probes = "".join(
                    f'<span style="font-family:\'{fam.strip()}\';position:absolute;opacity:0;pointer-events:none">A</span>'
                    for fam in families
                )
                page.evaluate(
                    "(h) => { const d = document.createElement('div'); d.innerHTML = h; "
                    "d.setAttribute('data-font-probe',''); document.body.prepend(d); }",
                    probes,
                )
                page.evaluate("document.fonts.ready")
                page.wait_for_timeout(150)
                # The probe has done its job (the font is now cached). Remove it
                # so it can never alter the report's layout -- e.g. a leftover
                # element after the last report would stop `:last-child` rules
                # from matching and add a blank page to bulk exports.
                page.evaluate("document.querySelectorAll('[data-font-probe]').forEach(e => e.remove())")

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