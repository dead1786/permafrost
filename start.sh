#!/bin/bash
echo "============================================"
echo "  Permafrost (PF) - AI Brain Framework"
echo "============================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 not found. Please install Python 3.10+ first."
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip"
    echo "  Mac: brew install python3"
    exit 1
fi

# Install dependencies
echo "[1/3] Installing dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt" -q

# Create data directory
mkdir -p ~/.permafrost

# Start brain in background if config exists
if [ -f ~/.permafrost/config.json ]; then
    echo "[2/3] Starting brain services..."
    python3 "$SCRIPT_DIR/launcher.py" &
fi

# Open browser after a delay
(sleep 3 && {
    if command -v xdg-open &> /dev/null; then
        xdg-open http://localhost:8503
    elif command -v open &> /dev/null; then
        open http://localhost:8503
    fi
}) &

# Launch console (foreground)
echo "[3/3] Starting web console..."
cd "$SCRIPT_DIR/console"
python3 -m streamlit run app.py --server.port 8503 --server.headless true
