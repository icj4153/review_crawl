FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    TZ=KST-9 \
    REVIEW_OUTPUT_DIR=/app/output

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .

EXPOSE 8502

CMD ["python", "web_app.py"]
