FROM python:3.11-slim

WORKDIR /app

# Copy entire repo so both brain/ and orchestrator/ are available
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r orchestrator/requirements.txt

# brain/ is added to sys.path dynamically in app.py via __file__
CMD gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 2 --timeout 120 orchestrator.app:app
