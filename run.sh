#!/bin/bash
# Run the marking API. Kills anything already on the port, then starts the server.
cd "$(dirname "$0")"

PORT=8001

# Kill any process already using this port (fixes "Address already in use")
PID=$(lsof -ti :$PORT 2>/dev/null)
if [ -n "$PID" ]; then
  echo "Killing process $PID on port $PORT..."
  kill -9 $PID 2>/dev/null
  sleep 1
fi

echo "Starting server on http://localhost:$PORT"
uvicorn marking.main:app --reload --port $PORT
