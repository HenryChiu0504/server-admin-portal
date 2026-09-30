FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Taipei
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
EXPOSE 13999
CMD ["uvicorn", "bot.app:app", "--host", "0.0.0.0", "--port", "13999"]
