FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY ops/ ops/
COPY config.yaml .
CMD ["python", "-m", "ops.main"]
