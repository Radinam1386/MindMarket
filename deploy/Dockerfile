# MarketMind — Liara deployment image
# Paper-trading system only (no real orders; the live ladder stays locked).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    MARKETMIND_SCHEDULER=1 \
    MARKETMIND_ROOT=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# deps first (layer cache); Iranian build hosts often reach the mirror faster
COPY deploy/requirements.txt /app/deploy/requirements.txt
RUN pip install --no-cache-dir -r /app/deploy/requirements.txt \
      -i https://mirror-pypi.runflare.com/simple \
 || pip install --no-cache-dir -r /app/deploy/requirements.txt

# app + seed data
COPY src /app/src
COPY pyproject.toml README.md /app/
COPY deploy/seed /app/deploy/seed
RUN pip install --no-cache-dir --no-deps --no-build-isolation .

RUN mkdir -p /app/data/secrets
EXPOSE 8787

CMD ["sh", "-c", "python -m marketmind.cli paper serve --host 0.0.0.0 --port ${PORT:-8787}"]
