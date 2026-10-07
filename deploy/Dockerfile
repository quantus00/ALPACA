FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-saas.txt .
RUN pip install --no-cache-dir -r requirements-saas.txt
COPY bot ./bot
COPY saas ./saas
RUN useradd --create-home app && mkdir -p /app/data && chown app /app/data
USER app
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--access-logfile", "-", "saas.wsgi:app"]
