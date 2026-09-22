FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# No exposed port - this has no inbound server, it just loops on its own
# schedule talking out to Immich and the TV.
CMD ["python", "app.py"]
