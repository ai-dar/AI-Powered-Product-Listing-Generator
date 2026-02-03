FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY backend/requierments.txt .
RUN pip install --no-cache-dir -r requierments.txt

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

WORKDIR /app/backend

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
