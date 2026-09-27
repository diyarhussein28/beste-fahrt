FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium
COPY shared/ shared/
COPY matcher/ matcher/
COPY collector/ collector/
COPY scripts/ scripts/
COPY config.yaml .
CMD ["python", "-m", "collector.monitor"]
