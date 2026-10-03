FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --uid 10001 --create-home app && mkdir /data /app/data && chown app:app /data /app/data
COPY --chown=app:app . .
RUN python manage.py collectstatic --noinput
USER app
ENV DATABASE_PATH=/data/desk.sqlite3
EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
