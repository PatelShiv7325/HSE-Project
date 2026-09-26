FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
# System libraries required by WeasyPrint (PDF generation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Render sets $PORT at runtime; gunicorn binds to it
CMD gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 wsgi:app
