# Use a single stage build for simplicity
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry==1.8.3

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install dependencies using Poetry
RUN poetry install --only=main --no-dev --no-interaction --no-ansi

# Copy source code
COPY src/ src/

# Run the bot
CMD ["python", "src/bot.py"]
