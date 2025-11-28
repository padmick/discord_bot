# Build stage
FROM python:3.10-slim as builder

WORKDIR /app

# Install poetry
RUN pip install poetry==1.8.3

# Copy configuration files
COPY pyproject.toml poetry.lock ./

# Export dependencies to requirements.txt
RUN poetry export -f requirements.txt -o requirements.txt --without-hashes

# Final stage
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Copy requirements from builder
COPY --from=builder /app/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/

# Run the bot
CMD ["python", "src/bot.py"]
