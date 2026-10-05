FROM python:3.12-slim

# Устанавливаем системные зависимости, включая FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем и устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальной код
COPY . .

# Открываем порт, который будет использовать Render
EXPOSE 10000

# Команда для запуска приложения
CMD ["python", "app.py"]