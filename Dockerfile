FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BLAK_HOST=0.0.0.0

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY game_engine.py ./
COPY server/ ./server/
COPY sandbox/ ./sandbox/
COPY data/ ./data/

EXPOSE 8765

CMD ["python", "-u", "-m", "server.main"]
