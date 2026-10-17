FROM python:3.11-slim

WORKDIR /app

COPY server.py /app/server.py
COPY static /app/static

ENV PYTHONUNBUFFERED=1
ENV PORT=8055
ENV DATA_DIR=/app/data
ENV STATIC_DIR=/app/static
ENV B650_URL=http://127.0.0.1:9120
ENV B450_URL=http://127.0.0.1:9120

EXPOSE 8055

CMD ["python3", "/app/server.py"]
