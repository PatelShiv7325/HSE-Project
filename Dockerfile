# The Playwright image already contains Chromium and every system library it needs to print PDFs.
# Keep the tag (v1.47.0) in step with  playwright==1.47.0  in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Render sets $PORT at runtime; gunicorn binds to it.
# The login lock-out is stored in the database, so 2 workers share it correctly.
CMD gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 wsgi:app
