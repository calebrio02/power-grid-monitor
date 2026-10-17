FROM python:3.11-slim

WORKDIR /app
COPY agent.py /app/agent.py

ENV PYTHONUNBUFFERED=1
ENV PORT=9120

EXPOSE 9120

CMD ["python3", "/app/agent.py"]
