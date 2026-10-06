FROM python:3.11-slim

# FFmpeg и системные библиотеки для MoviePy и Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p generated

EXPOSE 7860

CMD ["python", "app.py"]
