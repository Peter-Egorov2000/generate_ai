# Используем лёгкий образ Python
FROM python:3.11-slim

# Устанавливаем FFmpeg и другие системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем и устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код приложения
COPY . .

# Создаём папку для генерируемых файлов и даём права
RUN mkdir -p generated && chmod 777 generated

# Сообщаем, что приложение будет слушать порт 7860
EXPOSE 7860

# Команда запуска. Northflank сам подставит нужный порт через переменную PORT
CMD ["python", "app.py"]