# Use an official Python runtime as a parent image
FROM python:3.11-slim-buster

# Set the working directory in the container
WORKDIR /app


COPY backend/. /app

# Install any needed packages from the requirements.txt file.
RUN pip install --no-cache-dir -r requirements.txt

# Make port 8080 available to the world outside this container
EXPOSE 8080

# Run the run.sh script when the container launches
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "3600", "main:app"]