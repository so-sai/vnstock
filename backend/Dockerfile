FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/backend/data /app/backend/src/portfolio

EXPOSE 1234

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "1234", "--workers", "2"]
