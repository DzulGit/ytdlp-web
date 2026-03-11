#!/bin/bash
echo "🚀 Starting YT-DLP Web UI..."
echo "   → http://localhost:8080"
echo ""
cd "$(dirname "$0")"
uvicorn main:app --host 0.0.0.0 --port 8080
