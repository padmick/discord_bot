# Use a single stage build for simplicity
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry==1.8.3

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Regenerate lock file if needed and install dependencies
RUN poetry lock --no-update || poetry lock
RUN poetry install --only main --no-interaction --no-ansi

# Copy source code
COPY src/ src/

# Run the bot
CMD ["python", "src/bot.py"]
