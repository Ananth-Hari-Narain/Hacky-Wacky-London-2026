#!/bin/bash
cd "$(dirname "$0")"

PORT=8000

PID=$(lsof -ti :$PORT 2>/dev/null)
if [ -n "$PID" ]; then
  echo "Killing process $PID on port $PORT..."
  kill -9 $PID 2>/dev/null
  sleep 1
fi

echo "Reprocessing all data from scratch..."
python -m scripts.reprocess_all
echo ""

echo "Starting server on http://localhost:$PORT"
uvicorn api.main:app --reload --port $PORT
