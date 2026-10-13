FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TIKTOKEN_CACHE_DIR=/app/tokenizer-cache
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'
COPY . .
RUN useradd --create-home --uid 10001 insight
USER insight
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--log-config", "logging.json"]
