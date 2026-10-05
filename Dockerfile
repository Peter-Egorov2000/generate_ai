FROM python:3.12-slim

# Устанавливаем FFmpeg (нужен для MoviePy и imageio-ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Создаём non-root пользователя (требование Hugging Face Spaces)
RUN useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Даём права на папку generated, чтобы пользователь appuser мог в неё писать
RUN mkdir -p generated && chown -R appuser:appuser /app

# Переключаемся на non-root пользователя
USER appuser

# Hugging Face Spaces используют порт 7860
ENV PORT=7860
EXPOSE 7860

CMD ["python", "app.py"]