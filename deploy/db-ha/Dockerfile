FROM python:3.12-slim
RUN pip install --no-cache-dir 'psycopg[binary]==3.2.13'
WORKDIR /app
COPY router.py layout.py .
USER nobody
CMD ["python", "-u", "router.py"]
