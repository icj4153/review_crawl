FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    TZ=KST-9 \
    REVIEW_OUTPUT_DIR=/app/output

COPY requirements.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .

EXPOSE 8502

CMD ["sh", "-lc", "Xvfb :99 -screen 0 1365x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 & export DISPLAY=:99; sleep 1; exec gunicorn --bind=0.0.0.0:8502 --workers=1 --threads=4 --timeout=600 web_app:app"]
