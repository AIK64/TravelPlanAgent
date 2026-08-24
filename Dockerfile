FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY pyproject.toml README.md ./
COPY src ./src
COPY infrastructure ./infrastructure
RUN pip install --upgrade pip && pip install ".[production,llm-deepseek,checkpoint-sqlite]"
RUN mkdir -p /app/.data && chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "travel_agent.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
