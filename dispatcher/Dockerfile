FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY matcher/ matcher/
COPY returns/ returns/
COPY collector/geo.py collector/geo.py
COPY collector/__init__.py collector/__init__.py
COPY dispatcher/ dispatcher/
COPY config.yaml .
CMD ["python", "-m", "dispatcher.bot"]
