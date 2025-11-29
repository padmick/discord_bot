# Use a single stage build for simplicity
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and database setup
COPY src/ src/
COPY database_setup.sql setup_database.py ./

# Expose port for health checks
EXPOSE 8080

# Setup database if requested, then run the bot
CMD ["sh", "-c", "if [ \"$SETUP_DATABASE\" = \"true\" ]; then python setup_database.py; fi && python src/bot.py"]
