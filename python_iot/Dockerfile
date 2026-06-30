FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY ai_client.py .
COPY alert_config.py .
COPY io_catalog.py .
COPY rule_catalog.py .
COPY process_config_catalog.py .
COPY antifreeze_catalog.py .
CMD ["python", "app.py"]
