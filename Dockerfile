FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY noty ./noty

# Same uid/gid as the Jotty container, so the mounted data and state directories are readable/writable.
RUN mkdir -p /app/state && chown 1000:1000 /app/state
USER 1000:1000

HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-m", "noty.healthcheck"]

CMD ["python", "-m", "noty"]
