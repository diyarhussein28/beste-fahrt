FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY admin/ admin/
COPY config.yaml .
EXPOSE 8080
CMD ["uvicorn", "admin.app:app", "--host", "0.0.0.0", "--port", "8080"]
