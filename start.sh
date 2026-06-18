#!/usr/bin/env bash
# start.sh — Launch VEmbed Video Combiner and open the browser.
# Usage: ./start.sh    (double-click in a file manager or run from terminal)

set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Check if server is already running
if curl -s --max-time 1 http://localhost:5000 >/dev/null 2>&1; then
  echo "Server already running at http://localhost:5000"
else
  echo "Starting VEmbed Video Combiner..."
  source venv/bin/activate
  nohup python app.py > server.log 2>&1 &
  echo "Server starting (PID $!). Waiting..."
  sleep 3
fi

# Open browser (Linux / WSL)
URL="http://localhost:5000"
if   command -v wslview  >/dev/null 2>&1; then wslview "$URL"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
elif command -v open     >/dev/null 2>&1; then open "$URL"   # macOS
else echo "Open $URL in your browser."
fi

echo "Done. Server log: $DIR/server.log"
