FROM python:3.11-slim

WORKDIR /app

# Copy entire repo so both brain/ and orchestrator/ are available
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r orchestrator/requirements.txt

# start.sh launches cron_worker in background then gunicorn in foreground
CMD ["bash", "orchestrator/start.sh"]
