# Fraudbusters Investigator Workspace: container for deployment (Render, Hugging Face Spaces, any Docker host).
#   docker build -t fraudbusters .
#   docker run -p 8600:8600 fraudbusters      ->  http://localhost:8600
FROM python:3.12-slim

# DejaVu fonts so the STR PDF can print the rupee sign on Linux
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY src ./src
COPY app ./app
COPY data ./data
COPY models ./models

ENV PYTHONUNBUFFERED=1 PORT=8600
EXPOSE 8600
# Render (and most hosts) pass the port in $PORT
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT}"]
