FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    TZ=KST-9 \
    REVIEW_OUTPUT_DIR=/app/output

COPY requirements.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gnupg wget xvfb fonts-noto-cjk \
    && mkdir -p /etc/apt/keyrings \
    && wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /etc/apt/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/edge stable main" > /etc/apt/sources.list.d/microsoft-edge.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends microsoft-edge-stable \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .

EXPOSE 8502

CMD ["sh", "-lc", "Xvfb :99 -screen 0 1365x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 & export DISPLAY=:99; sleep 1; exec gunicorn --bind=0.0.0.0:8502 --workers=1 --threads=4 --timeout=600 web_app:app"]
