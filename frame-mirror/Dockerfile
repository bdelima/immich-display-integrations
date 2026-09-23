FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ARG VERSION=unknown
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/bdelima/immich-display-integrations" \
      org.opencontainers.image.url="https://github.com/bdelima/immich-display-integrations" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
ENV APP_VERSION="${VERSION}"

# No exposed port - this has no inbound server, it just loops on its own
# schedule talking out to Immich and the TV.
CMD ["python", "app.py"]
