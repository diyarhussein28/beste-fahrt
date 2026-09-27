FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY matcher/routing.py matcher/routing.py
COPY matcher/__init__.py matcher/__init__.py
COPY returns/ returns/
COPY config.yaml .
CMD ["python", "-m", "returns.service"]
