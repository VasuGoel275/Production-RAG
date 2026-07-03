#!/bin/bash
# Start FastAPI web server on the Render-exposed port
uvicorn main:app --host 0.0.0.0 --port $PORT
