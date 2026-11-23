FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=America/Fortaleza
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "main.py"]
