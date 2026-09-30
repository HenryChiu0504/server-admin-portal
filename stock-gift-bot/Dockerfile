FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Taipei
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
EXPOSE 13999
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:13999/health', timeout=4)"
CMD ["uvicorn", "bot.app:app", "--host", "0.0.0.0", "--port", "13999"]
