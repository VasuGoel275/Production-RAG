#!/bin/bash
# 1. Start FastAPI Backend in the background (listening locally)
uvicorn main:app --host 127.0.0.1 --port 8000 &

# 2. Start Celery Worker in the background
celery -A worker.celery_app worker --loglevel=info -Q light-pdf,heavy-pdf &

# 3. Start Streamlit Frontend in the foreground (exposing public $PORT)
streamlit run app.py --server.port $PORT --server.address 0.0.0.0
