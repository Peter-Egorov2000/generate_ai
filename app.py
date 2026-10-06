# pip install gradio requests pillow edge-tts deep-translator imageio-ffmpeg moviepy
import gradio as gr
import requests
from PIL import Image
from io import BytesIO
import urllib.parse
import time
import os
import asyncio
import subprocess
import shutil
import json
import re
import edge_tts
import imageio_ffmpeg
from deep_translator import MyMemoryTranslator

from moviepy import VideoFileClip, concatenate_videoclips, AudioFileClip
try:
    from moviepy.audio.AudioClip import CompositeAudioClip
except Exception:
    CompositeAudioClip = None

# ========== КЛЮЧИ И АВТОРИЗАЦИЯ ==========
OWNER_USERNAME = os.environ.get("APP_USERNAME", "admin")
OWNER_PASSWORD = os.environ.get("APP_PASSWORD", "")

OWNER_KEYS = {
    "pollinations": os.environ.get("POLLINATIONS_API_KEY", ""),
    "agnes": os.environ.get("AGNES_API_KEY", ""),
    "imgbb": os.environ.get("IMGBB_API_KEY", ""),
}

GUEST_KEYS = {
    "pollinations": os.environ.get("POLLINATIONS_API_KEY_GUEST", OWNER_KEYS["pollinations"]),
    "agnes": os.environ.get("AGNES_API_KEY_GUEST", OWNER_KEYS["agnes"]),
    "imgbb": os.environ.get("IMGBB_API_KEY_GUEST", OWNER_KEYS["imgbb"]),
}

if not OWNER_PASSWORD:
    raise ValueError("APP_PASSWORD не задан! Добавьте секрет APP_PASSWORD в настройках Space.")

# ========== НАСТРОЙКИ ==========
OUTPUT_DIR = os.path.abspath("generated")
os.makedirs(OUTPUT_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(OUTPUT_DIR, "history.json")

AUTO_CLEAN_DAYS = 7
LOGO_PATH = "logo.png"

VOICES = {
    "Русский (Дмитрий)": "ru-RU-DmitryNeural",
    "Русский (Светлана)": "ru-RU-SvetlanaNeural",
    "English (Guy)": "en-US-GuyNeural",
    "English (Aria)": "en-US-AriaNeural",
}

RESOLUTIONS = {
    "768p (быстро)": (1152, 768),
    "1080p (медленнее)": (1920, 1080),
}

GENERATION_MODES = {
    "🎞️ Цельный клип (до 18 сек)": "single",
    "🔗 Последовательные клипы (оживление кадров)": "sequential",
}

MODEL_CHOICES = [
    "⚡ turbo (быстро)",
    "🎨 flux (баланс)",
    "💎 flux-pro (качество)",
    "🚀 zimage (быстро + качество)",
    "📷 gptimage (фотореализм)",
]

TEMP_FILES = []


def get_pollinations_headers(keys):
    return {"Authorization": f"Bearer {keys['pollinations']}"}


def get_agnes_headers(keys):
    return {"Authorization": f"Bearer {keys['agnes']}", "Content-Type": "application/json"}


def parse_model_name(model_label: str) -> str:
    if not model_label:
        return "flux"
    m = re.match(r"^\S+\s+([a-zA-Z0-9\-_]+)", model_label.strip())
    if m:
        return m.group(1)
    return model_label.strip()


# ========== ОЧИСТКА ВРЕМЕННЫХ ФАЙЛОВ ==========
def cleanup_temp_files():
    global TEMP_FILES
    for f in TEMP_FILES:
        try:
            if os.path.exists(f):
                os.remove(f)
                print(f"🗑️ Удалён: {os.path.basename(f)}")
        except Exception as e:
            print(f"⚠️ Не удалось удалить {f}: {e}")
    TEMP_FILES = []


def cleanup_old_files(days=AUTO_CLEAN_DAYS):
    if not os.path.exists(OUTPUT_DIR):
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for fname in os.listdir(OUTPUT_DIR):
        fpath = os.path.join(OUTPUT_DIR, fname)
        if os.path.isfile(fpath) and not fname.endswith(".json"):
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    removed += 1
            except Exception:
                pass

    history = load_history()
    for key in ("images", "videos"):
        history[key] = [e for e in history.get(key, []) if os.path.exists(e.get("filepath", ""))]
    save_history(history)
    print(f"🧹 Автоочистка: удалено {removed} файлов старше {days} дней")
    return removed


def cleanup_on_load():
    cleanup_temp_files()
    return get_history_gallery("image"), get_history_gallery("video")


# ========== ИСТОРИЯ ==========
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Ошибка загрузки истории: {e}")
    return {"images": [], "videos": []}


def save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения истории: {e}")


def add_to_history(item_type, filepath, prompt, translated_prompt=""):
    history = load_history()
    entry = {
        "filepath": filepath,
        "filename": os.path.basename(filepath),
        "prompt": prompt,
        "translated_prompt": translated_prompt,
        "timestamp": int(time.time()),
        "date": time.strftime("%d.%m.%Y %H:%M:%S"),
    }
    if item_type == "image":
        history["images"].insert(0, entry)
        history["images"] = history["images"][:100]
    elif item_type == "video":
        history["videos"].insert(0, entry)
        history["videos"] = history["videos"][:50]
    save_history(history)


def remove_from_history(filepath):
    history = load_history()
    for key in ("images", "videos"):
        history[key] = [e for e in history.get(key, []) if e.get("filepath") != filepath]
    save_history(history)


def get_history_gallery(item_type):
    history = load_history()
    items = history.get(item_type + "s", [])
    gallery_items = []
    for item in items:
        path = item.get("filepath")
        if path and os.path.exists(path):
            caption = f"{item['date']}\n{item['prompt'][:60]}..."
            gallery_items.append((path, caption))
    return gallery_items


def get_history_details(item_type, index):
    history = load_history()
    items = history.get(item_type + "s", [])
    valid_items = [i for i in items if os.path.exists(i.get("filepath", ""))]
    if index is None or index < 0 or index >= len(valid_items):
        return None, "Ничего не выбрано"
    item = valid_items[index]
    info = (
        f"**Дата:** {item['date']}\n\n"
        f"**Промпт:** {item['prompt']}\n\n"
        f"**Перевод:** {item.get('translated_prompt', '—')}\n\n"
        f"**Файл:** {item['filename']}"
    )
    return item["filepath"], info


# ========== ЗАГРУЗКА ИЗОБРАЖЕНИЯ ==========
def upload_image_to_hosting(image_path, keys):
    for name, fn in [("ImgBB", upload_to_imgbb), ("Catbox", upload_to_catbox)]:
        try:
            print(f"📤 Пробую загрузить на {name}...")
            if name == "ImgBB":
                url = fn(image_path, keys)
            else:
                url = fn(image_path)
            if url:
                print(f"✅ Загружено на {name}: {url}")
                return url
            print(f"⚠️ {name}: не вернул URL")
        except Exception as e:
            print(f"⚠️ {name} не сработал: {e}")
    print("❌ Все хостинги недоступны.")
    return None


def upload_to_imgbb(image_path, keys):
    if not keys.get("imgbb"):
        return None
    with open(image_path, "rb") as f:
        r = requests.post(
            "https://api.imgbb.com/1/upload",
            params={"key": keys["imgbb"]},
            files={"image": f},
            timeout=60
        )
    if r.status_code == 200:
        return r.json().get("data", {}).get("url")
    return None


def upload_to_catbox(image_path):
    with open(image_path, "rb") as f:
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": f},
            timeout=60
        )
    if r.status_code == 200 and r.text.strip().startswith("http"):
        return r.text.strip()
    return None


# ========== ПЕРЕВОД ==========
def translate_to_english(text: str) -> str:
    if not text or not text.strip():
        return text
    try:
        return MyMemoryTranslator(source="ru-RU", target="en-GB").translate(text.strip()) or text
    except Exception as e:
        print(f"⚠️ Ошибка перевода: {e}")
        return text


# ========== ИЗВЛЕЧЕНИЕ ПОСЛЕДНЕГО КАДРА ==========
def extract_last_frame(video_path, output_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-sseof", "-0.1",
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path
    ]
    try:
        result = subprocess.run(
            cmd, stdin=subprocess.DEVNULL,
            capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=60
        )
        if result.returncode != 0:
            print(f"❌ extract_last_frame: {(result.stderr or '')[-300:]}")
            return False
        ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if ok:
            print(f"   ✅ Последний кадр сохранён: {os.path.basename(output_path)}")
        return ok
    except Exception as e:
        print(f"❌ extract_last_frame: {e}")
        return False


# ========== СКЛЕЙКА ВИДЕО ==========
def concatenate_videos_with_progress(clip_paths, output_path, audio_path=None,
                                     width=1152, height=768, progress=None):
    if not clip_paths:
        print("❌ Нет клипов для склейки")
        return False

    abs_output = os.path.abspath(output_path)
    clips = []
    external_audio = None
    final = None

    try:
        for i, path in enumerate(clip_paths):
            if progress:
                try:
                    progress(0.85 + (i / len(clip_paths)) * 0.05,
                             desc=f"Загрузка клипа {i+1}/{len(clip_paths)}...")
                except Exception:
                    pass
            print(f"📥 Загрузка клипа {i+1}/{len(clip_paths)}: {os.path.basename(path)}")

            clip = VideoFileClip(path)

            try:
                clip = clip.resized(new_size=(int(width), int(height)))
            except Exception as e:
                print(f"   ⚠️ resized не удался: {e}")

            clips.append(clip)

        if not clips:
            print("❌ Не удалось загрузить клипы")
            return False

        if progress:
            try:
                progress(0.92, desc="Склейка клипов...")
            except Exception:
                pass
        print(f"🔧 Склейка {len(clips)} клипов...")

        if len(clips) == 1:
            final = clips[0]
        else:
            final = concatenate_videoclips(clips, method="compose")

        if audio_path and os.path.exists(audio_path):
            print(f"🎵 Добавляю внешнюю озвучку: {os.path.basename(audio_path)}")
            try:
                external_audio = AudioFileClip(audio_path)
                if final.audio is not None and CompositeAudioClip is not None:
                    final = final.with_audio(
                        CompositeAudioClip([final.audio, external_audio])
                    )
                else:
                    final = final.with_audio(external_audio)
            except Exception as e:
                print(f"⚠️ Не удалось добавить озвучку: {e}")

        if progress:
            try:
                progress(0.95, desc="Сохранение видео...")
            except Exception:
                pass
        print(f"💾 Сохранение → {abs_output}")

        final.write_videofile(
            abs_output,
            codec="libx264",
            audio_codec="aac",
            fps=24,
            preset="fast",
            threads=4,
            logger=None,
        )

        if os.path.exists(abs_output) and os.path.getsize(abs_output) > 1024:
            size_mb = os.path.getsize(abs_output) / (1024 * 1024)
            print(f"✅ Готово: {abs_output} ({size_mb:.2f} MB)")
            return True
        else:
            print("❌ Файл не создан или слишком мал")
            return False

    except Exception as e:
        print(f"❌ Ошибка MoviePy: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        try:
            if final is not None:
                final.close()
        except Exception:
            pass
        try:
            if external_audio is not None:
                external_audio.close()
        except Exception:
            pass
        for c in clips:
            try:
                c.close()
            except Exception:
                pass


# ========== ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ ==========
def generate_image(prompt, width, height, model, seed, nologo, enhance,
                   do_translate, secret_mode, session_keys, progress=gr.Progress()):
    if not prompt or not prompt.strip():
        raise gr.Error("Введи промпт!")

    model = parse_model_name(model)

    if secret_mode:
        cleanup_temp_files()

    progress(0.05, desc="Обработка промпта...")
    final_prompt = prompt.strip()
    translated_text = ""
    if do_translate:
        progress(0.1, desc="Перевожу на английский...")
        final_prompt = translate_to_english(prompt)
        translated_text = final_prompt

    encoded = urllib.parse.quote(final_prompt)
    seed = int(seed) if seed and seed > 0 else int(time.time() * 1000) % 1_000_000

    url = (f"https://image.pollinations.ai/prompt/{encoded}"
           f"?width={int(width)}&height={int(height)}&seed={seed}"
           f"&model={model}&nologo={'true' if nologo else 'false'}"
           f"&enhance={'true' if enhance else 'false'}")

    progress(0.4, desc=f"Генерирую ({model})...")
    try:
        r = requests.get(url, headers=get_pollinations_headers(session_keys), timeout=180)
        r.raise_for_status()
    except requests.RequestException as e:
        raise gr.Error(f"Ошибка сети: {e}")

    if not r.headers.get("Content-Type", "").startswith("image/"):
        raise gr.Error(f"Сервер вернул не картинку: {r.text[:200]}")

    progress(0.9, desc="Сохраняю...")
    img = Image.open(BytesIO(r.content)).convert("RGB")
    filename = os.path.join(OUTPUT_DIR, f"img_{seed}.png")
    img.save(filename)
    add_to_history("image", filename, prompt, translated_text)

    if secret_mode:
        TEMP_FILES.append(filename)
    return img, filename, str(seed), translated_text


def batch_generate(prompts_text, width, height, model, enhance, do_translate,
                   session_keys, progress=gr.Progress()):
    if not prompts_text or not prompts_text.strip():
        raise gr.Error("Введи промпты!")
    prompts = [p.strip() for p in prompts_text.replace("\n", ";").split(";") if p.strip()]
    if not prompts:
        raise gr.Error("Пустой список промптов")

    model = parse_model_name(model)

    results = []
    for i, p in enumerate(prompts):
        progress(i / len(prompts), desc=f"[{i+1}/{len(prompts)}] {p[:40]}...")
        try:
            img, path, seed, _ = generate_image(p, width, height, model, 0, True, enhance,
                                                do_translate, False, session_keys)
            results.append((img, f"seed={seed} | {p}"))
        except Exception as e:
            print(f"❌ '{p}': {e}")
        time.sleep(1)
    progress(1.0, desc="Готово!")
    return results


# ========== ГЕНЕРАЦИЯ ВИДЕО (AGNES) ==========

def generate_video_agnes(prompt, image_url=None, width=1152, height=768,
                         num_frames=121, frame_rate=24, keys=None):
    if keys is None:
        keys = GUEST_KEYS
    mode = "image-to-video" if image_url else "text-to-video"
    print(f"🎬 Agnes AI ({mode}, {width}x{height}, {num_frames} кадров @ {frame_rate}fps): {prompt[:50]}...")

    payload = {
        "model": "agnes-video-v2.0",
        "prompt": prompt,
        "height": height,
        "width": width,
        "num_frames": num_frames,
        "frame_rate": frame_rate,
    }
    if image_url:
        payload["image"] = image_url

    resp = None
    for attempt in range(1, 4):
        try:
            print(f"📡 Отправка задачи (попытка {attempt}/3, timeout=300 сек)...")
            resp = requests.post(
                "https://apihub.agnes-ai.com/v1/videos",
                headers=get_agnes_headers(keys), json=payload, timeout=300)
            resp.raise_for_status()
            break
        except requests.exceptions.Timeout:
            print(f"⚠️ Таймаут на попытке {attempt}. Жду 10 сек...")
            time.sleep(10)
            continue
        except requests.exceptions.RequestException as e:
            print(f"❌ Ошибка сети (попытка {attempt}): {e}")
            try:
                print(f"📡 Ответ: {e.response.text[:300]}")
            except Exception:
                pass
            if attempt < 3:
                time.sleep(5)
                continue
            return None

    if resp is None:
        return None

    try:
        data = resp.json()
    except Exception:
        return None

    video_id = data.get("video_id")
    if not video_id:
        print(f"❌ Agnes: нет video_id")
        return None

    print(f"⏳ Agnes: задача создана")

    for attempt in range(120):
        time.sleep(5)
        try:
            poll = requests.get(
                f"https://apihub.agnes-ai.com/agnesapi?video_id={video_id}",
                headers=get_agnes_headers(keys), timeout=30)
        except requests.exceptions.RequestException:
            continue
        if poll.status_code != 200:
            continue
        try:
            data = poll.json()
        except Exception:
            continue

        status = data.get("status", "")
        print(f"⏳ Agnes: {status} ({attempt+1}/120)")

        if status in ("completed", "succeeded", "success"):
            video_url = data.get("video_url") or data.get("url")
            if not video_url and isinstance(data.get("content"), dict):
                video_url = data["content"].get("video_url")
            if video_url:
                try:
                    return requests.get(video_url, timeout=180).content
                except requests.exceptions.RequestException:
                    return None
            return None
        if status in ("failed", "error"):
            print(f"❌ Agnes: провалено")
            return None

    print("❌ Agnes: таймаут")
    return None


async def generate_speech(text, voice="ru-RU-DmitryNeural", rate="+0%"):
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    audio_data = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
    return audio_data


def _nearest_8n_plus_1(target):
    candidates = [8 * n + 1 for n in range(1, 56)]
    candidates = [c for c in candidates if c <= 441]
    return min(candidates, key=lambda c: abs(c - target))


def generate_video(prompt, duration, resolution_choice, generation_mode_choice,
                   input_image, add_tts, voice_choice, do_translate, secret_mode,
                   session_keys, progress=gr.Progress()):
    if not prompt.strip():
        raise gr.Error("Введи промпт для видео!")

    if secret_mode:
        cleanup_temp_files()

    width, height = RESOLUTIONS.get(resolution_choice, (1152, 768))
    generation_mode = GENERATION_MODES.get(generation_mode_choice, "single")
    frame_rate = 24

    progress(0.05, desc="Обработка промпта...")
    video_prompt = prompt.strip()
    translated_text = ""
    if do_translate:
        progress(0.08, desc="Перевожу на английский...")
        video_prompt = translate_to_english(prompt)
        translated_text = video_prompt

    image_url = None
    if input_image is not None:
        progress(0.1, desc="Загружаю фото на хостинг...")
        image_url = upload_image_to_hosting(input_image, session_keys)
        if not image_url:
            raise gr.Error("Не удалось загрузить фото на хостинг.")

    target_frames = round(duration * frame_rate)
    max_frames_single = 441

    if generation_mode == "single" and target_frames <= max_frames_single:
        num_frames = _nearest_8n_plus_1(target_frames)
        clips_needed = 1
        print(f"🎞️ Режим: цельный клип ({num_frames} кадров ≈ {num_frames/frame_rate:.1f} сек)")
    else:
        if generation_mode == "single" and target_frames > max_frames_single:
            print(f"⚠️ Длительность {duration}с превышает лимит одного клипа (~18с). Переключаюсь на последовательный режим.")
        clip_seconds = 5
        clips_needed = max(1, round(duration / clip_seconds))
        num_frames = 121
        print(f"🔗 Режим: последовательные клипы ({clips_needed} × 5 сек)")

    tmp_dir = os.path.abspath(os.path.join(OUTPUT_DIR, f"tmp_{int(time.time())}"))
    os.makedirs(tmp_dir, exist_ok=True)

    clip_paths = []
    previous_clip_path = None

    for i in range(clips_needed):
        progress(0.15 + (i / clips_needed) * 0.5,
                 desc=f"Клип {i+1}/{clips_needed} ({resolution_choice})...")

        if clips_needed > 1:
            scene_prompt = f"{video_prompt}, scene {i+1}, cinematic"
        else:
            scene_prompt = video_prompt

        clip_image = None
        if i == 0:
            clip_image = image_url
        else:
            if previous_clip_path and os.path.exists(previous_clip_path):
                last_frame_path = os.path.join(tmp_dir, f"last_frame_{i:02d}.png")
                if extract_last_frame(previous_clip_path, last_frame_path):
                    uploaded_url = upload_image_to_hosting(last_frame_path, session_keys)
                    if uploaded_url:
                        clip_image = uploaded_url
                        print(f"   🖼️ Использую последний кадр клипа {i} как начальный")
                    else:
                        print(f"   ⚠️ Не удалось загрузить последний кадр, будет text-to-video")
                else:
                    print(f"   ⚠️ Не удалось извлечь последний кадр")

        clip_data = generate_video_agnes(scene_prompt, clip_image, width, height,
                                         num_frames, frame_rate, session_keys)
        if not clip_data:
            raise gr.Error(f"Не удалось сгенерировать клип {i+1}.")

        clip_path = os.path.join(tmp_dir, f"clip_{i:02d}.mp4")
        with open(clip_path, "wb") as f:
            f.write(clip_data)
        clip_paths.append(clip_path)
        previous_clip_path = clip_path
        time.sleep(2)

    audio_path = None
    audio_msg = "Без озвучки"
    if add_tts:
        progress(0.7, desc="Генерация озвучки...")
        try:
            voice = VOICES.get(voice_choice, "ru-RU-DmitryNeural")
            audio_data = asyncio.run(generate_speech(prompt, voice))
            audio_path = os.path.join(tmp_dir, "voice.mp3")
            with open(audio_path, "wb") as f:
                f.write(audio_data)
            audio_msg = f"✅ Озвучка: {voice_choice} ({len(audio_data)//1024} KB)"
        except Exception as e:
            audio_msg = f"❌ Ошибка озвучки: {e}"

    progress(0.85, desc="Склейка клипов...")
    timestamp = int(time.time())
    final_filename = os.path.abspath(os.path.join(OUTPUT_DIR, f"video_{timestamp}.mp4"))
    audio_filename_out = None

    if not concatenate_videos_with_progress(clip_paths, final_filename, audio_path,
                                             width, height, progress):
        raise gr.Error("MoviePy не смог склеить клипы.")

    if audio_path:
        audio_filename_out = os.path.abspath(os.path.join(OUTPUT_DIR, f"audio_{timestamp}.mp3"))
        shutil.copy(audio_path, audio_filename_out)

    add_to_history("video", final_filename, prompt, translated_text)

    if secret_mode:
        TEMP_FILES.append(final_filename)
        if audio_filename_out:
            TEMP_FILES.append(audio_filename_out)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    progress(1.0, desc="Готово!")
    return final_filename, audio_filename_out, audio_msg, translated_text


# ========== УДАЛЕНИЕ ИЗ ИСТОРИИ ==========
def delete_from_history(item_type, index):
    history = load_history()
    items = [i for i in history.get(item_type + "s", []) if os.path.exists(i.get("filepath", ""))]
    if index is None or index < 0 or index >= len(items):
        return "⚠️ Ничего не выбрано", get_history_gallery("image"), get_history_gallery("video")
    item = items[index]
    filepath = item.get("filepath")
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            print(f"🗑️ Удалён: {os.path.basename(filepath)}")
    except Exception as e:
        print(f"⚠️ Не удалось удалить файл: {e}")
    remove_from_history(filepath)
    return f"✅ Удалено: {item['filename']}", get_history_gallery("image"), get_history_gallery("video")


# ========== ЗАПУСК АВТООЧИСТКИ ==========
cleanup_old_files(AUTO_CLEAN_DAYS)


# ========== ИНТЕРФЕЙС ==========
with gr.Blocks(title="Генерация бесплатно!") as demo:
    # Состояние сессии: какие ключи сейчас активны
    session_keys = gr.State(value=GUEST_KEYS)

    # ---------- ЭКРАН ВХОДА ----------
    with gr.Column(visible=True) as login_screen:
        with gr.Row():
            gr.Markdown("""
            # 🎨 Генерация бесплатно!
            ### ИИ-генератор картинок и видео
            Войдите как владелец или продолжите как гость.
            """)
        with gr.Column(scale=1):
            login_username = gr.Textbox(label="Имя пользователя", placeholder="admin")
            login_password = gr.Textbox(label="Пароль", type="password", placeholder="••••••")
            with gr.Row():
                login_btn = gr.Button("🔓 Войти", variant="primary", size="lg")
                guest_btn = gr.Button("👤 Войти как гость", variant="secondary", size="lg")
            login_message = gr.Markdown("")

    # ---------- ОСНОВНОЙ ЭКРАН ----------
    with gr.Column(visible=False) as main_screen:
        with gr.Row():
            if os.path.exists(LOGO_PATH):
                gr.Image(value=LOGO_PATH, show_label=False, height=80, width=80,
                         show_download_button=False, show_fullscreen_button=False,
                         container=False, scale=0)
            with gr.Column(scale=1):
                gr.Markdown("""
                # 🎨 Генерация бесплатно!
                ### ИИ-генератор картинок и видео
                **Картинки:** Pollinations · **Видео:** Agnes AI · **Склейка:** MoviePy · **Озвучка:** EdgeTTS
                """)

        with gr.Tabs():
            # ===== ВКЛАДКА 1 =====
            with gr.Tab("🖼️ Одна картинка"):
                with gr.Row():
                    with gr.Column(scale=2):
                        prompt = gr.Textbox(label="Промпт (можно на русском)",
                                            placeholder="кот-космонавт в стиле киберпанк, неон", lines=3)
                        do_translate = gr.Checkbox(value=True, label="🌐 Переводить на английский")
                        translated_out = gr.Textbox(label="📝 Промпт на английском", interactive=False, lines=2)
                        with gr.Row():
                            model = gr.Dropdown(
                                choices=MODEL_CHOICES,
                                value="🎨 flux (баланс)",
                                label="Модель",
                                info="turbo — быстрее · flux-pro — качественнее · zimage — компромисс · gptimage — фотореализм",
                            )
                            nologo = gr.Checkbox(value=True, label="Без логотипа")
                            enhance = gr.Checkbox(value=True, label="✨ Улучшить промпт")
                        with gr.Row():
                            width = gr.Slider(256, 1536, value=1024, step=64, label="Ширина")
                            height = gr.Slider(256, 1536, value=1024, step=64, label="Высота")
                        seed = gr.Number(value=0, label="Seed (0 = случайный)", precision=0)
                        secret_img = gr.Checkbox(value=False, label="🔒 Секретно (удалить после новой задачи)")
                        btn = gr.Button("✨ Сгенерировать", variant="primary", size="lg")
                        used_seed = gr.Textbox(label="Использованный seed", interactive=False)
                    with gr.Column(scale=3):
                        output_img = gr.Image(label="Результат", type="pil", height=600)
                        download_img = gr.File(label="⬇️ Скачать картинку", interactive=False)
                btn.click(fn=generate_image,
                          inputs=[prompt, width, height, model, seed, nologo, enhance,
                                  do_translate, secret_img, session_keys],
                          outputs=[output_img, download_img, used_seed, translated_out],
                          concurrency_limit=2)
                prompt.submit(fn=generate_image,
                              inputs=[prompt, width, height, model, seed, nologo, enhance,
                                      do_translate, secret_img, session_keys],
                              outputs=[output_img, download_img, used_seed, translated_out],
                              concurrency_limit=2)

            # ===== ВКЛАДКА 2 =====
            with gr.Tab("📦 Пачка промптов"):
                gr.Markdown("Разделяй промпты через `;` или с новой строки.")
                with gr.Row():
                    with gr.Column(scale=2):
                        batch_prompts = gr.Textbox(label="Промпты",
                                                   placeholder="киберпанк город ночью;\nробот-самурай;\nкрасный дракон над горами", lines=8)
                        b_do_translate = gr.Checkbox(value=True, label="🌐 Переводить на английский")
                        with gr.Row():
                            b_model = gr.Dropdown(
                                choices=MODEL_CHOICES,
                                value="🚀 zimage (быстро + качество)",
                                label="Модель",
                                info="Для пачки лучше zimage — быстро и качественно",
                            )
                            b_enhance = gr.Checkbox(value=True, label="✨ Улучшить")
                        with gr.Row():
                            b_width = gr.Slider(256, 1024, value=768, step=64, label="Ширина")
                            b_height = gr.Slider(256, 1024, value=768, step=64, label="Высота")
                        b_btn = gr.Button("🚀 Сгенерировать всё", variant="primary", size="lg")
                    with gr.Column(scale=3):
                        gallery = gr.Gallery(label="Результаты", columns=2, height=600, show_label=True)
                b_btn.click(fn=batch_generate,
                            inputs=[batch_prompts, b_width, b_height, b_model, b_enhance,
                                    b_do_translate, session_keys],
                            outputs=[gallery], concurrency_limit=1)

            # ===== ВКЛАДКА 3 =====
            with gr.Tab("🎬 Создать видео"):
                gr.Markdown("""
                ### Генерация видео через Agnes AI
                - **Цельный клип** — один запрос, до ~18 секунд (лимит API: 441 кадр при 24 fps)
                - **Последовательные клипы** — клипы по 5 сек, каждый следующий начинается с **последнего кадра** предыдущего
                - **Фото → Видео** — загрузи картинку, и она оживёт
                - **Очередь:** одновременно генерируется только 1 видео. Остальные ждут.
                """)
                with gr.Row():
                    with gr.Column(scale=2):
                        video_prompt = gr.Textbox(label="Промпт для видео (можно на русском)",
                                                  placeholder="кот танцует на пляже, волны разбиваются о берег", lines=3)
                        input_image = gr.Image(
                            label="📷 Начальный кадр (необязательно)",
                            type="filepath", height=200,
                        )
                        v_do_translate = gr.Checkbox(value=True, label="🌐 Переводить промпт на английский")
                        v_translated_out = gr.Textbox(label="📝 Промпт на английском", interactive=False, lines=2)
                        video_duration = gr.Slider(minimum=5, maximum=60, value=10, step=5,
                                                   label="Длительность (секунд)",
                                                   info="Кратно 5 сек: 5, 10, 15, 20...")
                        generation_mode = gr.Radio(
                            choices=list(GENERATION_MODES.keys()),
                            value="🎞️ Цельный клип (до 18 сек)",
                            label="🎬 Режим генерации",
                            info="Цельный — один вызов API. Последовательный — клипы по 5 сек с оживлением кадров."
                        )
                        resolution_choice = gr.Dropdown(
                            choices=list(RESOLUTIONS.keys()),
                            value="768p (быстро)",
                            label="📺 Разрешение видео",
                            info="1080p — качественнее, но медленнее",
                        )
                        with gr.Row():
                            add_tts = gr.Checkbox(value=False, label="🗣️ Озвучить промпт (TTS)")
                            voice_choice = gr.Dropdown(choices=list(VOICES.keys()),
                                                       value="Русский (Дмитрий)", label="Голос")
                        secret_vid = gr.Checkbox(value=False, label="🔒 Секретно (удалить после новой задачи)")
                        video_btn = gr.Button("🎬 Сгенерировать видео", variant="primary", size="lg")
                        audio_status = gr.Textbox(label="Статус озвучки", interactive=False)
                    with gr.Column(scale=3):
                        video_output = gr.Video(label="Результат", height=450)
                        download_video = gr.File(label="⬇️ Скачать видео (MP4)", interactive=False)
                        audio_output = gr.Audio(label="⬇️ Скачать озвучку отдельно (MP3)", type="filepath")
                video_btn.click(
                    fn=generate_video,
                    inputs=[video_prompt, video_duration, resolution_choice, generation_mode,
                            input_image, add_tts, voice_choice, v_do_translate, secret_vid,
                            session_keys],
                    outputs=[video_output, audio_output, audio_status, v_translated_out],
                    concurrency_limit=1,
                )

            # ===== ВКЛАДКА 4 =====
            with gr.Tab("📜 История"):
                gr.Markdown("### Ваши прошлые генерации. Клик по элементу — большая версия + промпт.")
                with gr.Row():
                    refresh_btn = gr.Button("🔄 Обновить историю", variant="secondary", size="lg")
                    clean_btn = gr.Button(f"🧹 Удалить старше {AUTO_CLEAN_DAYS} дней", variant="secondary", size="lg")
                    clean_status = gr.Textbox(label="Статус", interactive=False, scale=2)

                with gr.Tabs():
                    with gr.Tab("🖼️ Картинки"):
                        with gr.Row():
                            with gr.Column(scale=2):
                                history_images_gallery = gr.Gallery(
                                    label="Кликни для просмотра", columns=3, height=500)
                            with gr.Column(scale=1):
                                preview_img = gr.Image(label="Просмотр", height=400)
                                info_img = gr.Markdown("Ничего не выбрано")
                                del_img_btn = gr.Button("🗑️ Удалить выбранное", variant="stop")

                    with gr.Tab("🎬 Видео"):
                        with gr.Row():
                            with gr.Column(scale=2):
                                history_videos_gallery = gr.Gallery(
                                    label="Кликни для просмотра", columns=2, height=500)
                            with gr.Column(scale=1):
                                preview_vid = gr.Video(label="Просмотр", height=400)
                                info_vid = gr.Markdown("Ничего не выбрано")
                                del_vid_btn = gr.Button("🗑️ Удалить выбранное", variant="stop")

                def refresh_all():
                    return get_history_gallery("image"), get_history_gallery("video")

                def do_clean():
                    n = cleanup_old_files(AUTO_CLEAN_DAYS)
                    return f"🧹 Удалено {n} файлов", *refresh_all()

                refresh_btn.click(fn=refresh_all,
                                  outputs=[history_images_gallery, history_videos_gallery])
                clean_btn.click(fn=do_clean,
                                outputs=[clean_status, history_images_gallery, history_videos_gallery])

                def on_img_select(evt: gr.SelectData):
                    path, info = get_history_details("image", evt.index)
                    return path, info
                history_images_gallery.select(fn=on_img_select,
                                              outputs=[preview_img, info_img])

                def on_vid_select(evt: gr.SelectData):
                    path, info = get_history_details("video", evt.index)
                    return path, info
                history_videos_gallery.select(fn=on_vid_select,
                                              outputs=[preview_vid, info_vid])

                selected_img_idx = gr.State(-1)
                selected_vid_idx = gr.State(-1)

                def save_img_idx(evt: gr.SelectData):
                    return evt.index
                history_images_gallery.select(fn=save_img_idx, outputs=[selected_img_idx])
                history_videos_gallery.select(fn=save_img_idx, outputs=[selected_vid_idx])

                del_img_btn.click(
                    fn=lambda i: delete_from_history("image", i),
                    inputs=[selected_img_idx],
                    outputs=[clean_status, history_images_gallery, history_videos_gallery],
                )
                del_vid_btn.click(
                    fn=lambda i: delete_from_history("video", i),
                    inputs=[selected_vid_idx],
                    outputs=[clean_status, history_images_gallery, history_videos_gallery],
                )

                demo.load(fn=cleanup_on_load,
                          outputs=[history_images_gallery, history_videos_gallery])

        gr.Markdown("""
        ---
        💡 **Советы:**
        - **Модели картинок:** ⚡ turbo — быстро · 🎨 flux — баланс · 💎 flux-pro — качество · 🚀 zimage — быстро+качество · 📷 gptimage — фотореализм
        - **✨ Улучшить промпт** — LLM дополнит описание деталями и стилем
        - **Цельный клип** лучше для длительности ≤ 18 сек — быстрее и без склейки
        - **Последовательные клипы** — для длинных видео с плавным переходом между сценами
        - Для видео описывай **движение**: `дрон летит над горами`, `волны разбиваются`
        """)

        gr.Markdown("""
        ---
        <div style="text-align: center; padding: 20px 0; color: #666;">
            <p style="font-size: 16px; margin: 6px 0;"><b>Создано Egorov Company (Пётр Егоров)</b></p>
            <p style="font-size: 15px; margin: 6px 0;">
                📞 Телефон для связи: <a href="tel:+79911548118" style="color: #4a90e2; text-decoration: none;">8 991 154 81 18</a>
            </p>
            <p style="font-size: 13px; margin: 12px 0 0 0; color: #999;">
                © 2026 Генерация бесплатно! Все права защищены.
            </p>
        </div>
        """)

    # ---------- ОБРАБОТЧИКИ ВХОДА ----------
    def do_login(username, password):
        if username.strip() == OWNER_USERNAME and password == OWNER_PASSWORD:
            return (
                gr.update(visible=False),   # login_screen → скрыть
                gr.update(visible=True),    # main_screen → показать
                OWNER_KEYS,                 # session_keys
                "✅ Добро пожаловать, владелец!",
            )
        return (
            gr.update(visible=True),
            gr.update(visible=False),
            GUEST_KEYS,
            "❌ Неверный логин или пароль. Попробуйте снова или войдите как гость.",
        )

    def do_guest():
        return (
            gr.update(visible=False),
            gr.update(visible=True),
            GUEST_KEYS,
            "👤 Вы вошли как гость. Используются гостевые ключи.",
        )

    login_btn.click(
        fn=do_login,
        inputs=[login_username, login_password],
        outputs=[login_screen, main_screen, session_keys, login_message],
    )
    guest_btn.click(
        fn=do_guest,
        inputs=[],
        outputs=[login_screen, main_screen, session_keys, login_message],
    )


# ========== ЗАПУСК ==========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.queue(max_size=30).launch(
        server_name="0.0.0.0",
        server_port=port,
        # Больше никаких ssr_mode и auth
    )
