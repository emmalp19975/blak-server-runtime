#!/bin/sh
set -e

STORAGE_DIR="${BLAK_STORAGE_DIR:-/app/storage}"

if [ -d "$STORAGE_DIR" ]; then
    mkdir -p "$STORAGE_DIR/training_data" "$STORAGE_DIR/profiles" "$STORAGE_DIR/logs"

    if [ ! -f "$STORAGE_DIR/scoreboard.json" ] && [ -f "/app/data/scoreboard.json" ]; then
        cp "/app/data/scoreboard.json" "$STORAGE_DIR/scoreboard.json"
    fi

    if [ -d "/app/data/profiles" ] && [ ! -L "/app/data/profiles" ]; then
        for f in /app/data/profiles/*.json; do
            [ -e "$f" ] || continue
            base=$(basename "$f")
            if [ ! -f "$STORAGE_DIR/profiles/$base" ]; then
                cp "$f" "$STORAGE_DIR/profiles/$base"
            fi
        done
        rm -rf /app/data/profiles
        ln -s "$STORAGE_DIR/profiles" /app/data/profiles
    fi

    rm -rf /app/data/training_data
    ln -s "$STORAGE_DIR/training_data" /app/data/training_data

    rm -f /app/data/scoreboard.json
    ln -s "$STORAGE_DIR/scoreboard.json" /app/data/scoreboard.json

    rm -rf /app/logs
    ln -s "$STORAGE_DIR/logs" /app/logs
fi

exec python -u -m server.main
