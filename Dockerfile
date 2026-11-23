FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BLAK_HOST=0.0.0.0 \
    BLAK_STORAGE_DIR=/app/storage

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY game_engine.py ./
COPY server/ ./server/
COPY sandbox/ ./sandbox/
COPY data/ ./data/
COPY entrypoint.sh ./
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

EXPOSE 8765

CMD ["/app/entrypoint.sh"]
