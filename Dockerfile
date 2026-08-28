FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent_service ./agent_service
COPY streamlit_app.py .
COPY ui_debug.py .
