# pip install streamlit requests pillow edge-tts deep-translator imageio-ffmpeg moviepy boto3
import streamlit as st
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
import base64
import math
import edge_tts
import imageio_ffmpeg
from deep_translator import MyMemoryTranslator
from moviepy import VideoFileClip, concatenate_videoclips, AudioFileClip
try:
    from moviepy.audio.AudioClip import CompositeAudioClip
except Exception:
    CompositeAudioClip = None

# ========== КОНФИГУРАЦИЯ СТРАНИЦЫ ==========
st.set_page_config(
    page_title="Генерация бесплатно!",
    page_icon="🎨",
    layout="wide",
)

# ========== КЛЮЧИ ==========
def _secret(key, default=""):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


OWNER_USERNAME = _secret("APP_USERNAME", "admin")
OWNER_PASSWORD = _secret("APP_PASSWORD", "")

OWNER_KEYS = {
    "pollinations": _secret("POLLINATIONS_API_KEY"),
    "agnes": _secret("AGNES_API_KEY"),
    "imgbb": _secret("IMGBB_API_KEY"),
}

GUEST_KEYS = {
    "pollinations": _secret("POLLINATIONS_API_KEY_GUEST", OWNER_KEYS["pollinations"]),
    "agnes": _secret("AGNES_API_KEY_GUEST", OWNER_KEYS["agnes"]),
    "imgbb": _secret("IMGBB_API_KEY_GUEST", OWNER_KEYS["imgbb"]),
}

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

# ========== ПРОВАЙДЕРЫ ИЗОБРАЖЕНИЙ ==========
IMAGE_PROVIDERS = {
    "Pollinations": "pollinations",
    "Agnes AI": "agnes",
}

# Доступные бесплатные модели Pollinations (без платных)
POLLINATIONS_FREE_MODELS = [
    "⚡ turbo (быстро)",
    "🖼️ stable-diffusion",
    "🔄 kontext",
    "🌱 seedream",
]

# Модели Agnes AI
AGNES_MODELS = [
    "agnes-image-2.5-flash",
    "agnes-image-2.0-flash",
]




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


# ========== ОЧИСТКА ==========
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
    return removed


# ========== ИСТОРИЯ ==========
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"images": [], "videos": []}


def save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


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


def get_history_items(item_type):
    history = load_history()
    items = history.get(item_type + "s", [])
    return [i for i in items if os.path.exists(i.get("filepath", ""))]


# ========== ЗАГРУЗКА ИЗОБРАЖЕНИЙ НА ХОСТИНГ ==========
def upload_to_imgbb(image_path, keys):
    if not keys.get("imgbb"):
        return None
    try:
        with open(image_path, "rb") as f:
            r = requests.post(
                "https://api.imgbb.com/1/upload",
                params={"key": keys["imgbb"]},
                files={"image": f},
                timeout=60
            )
        if r.status_code == 200:
            return r.json().get("data", {}).get("url")
    except Exception as e:
        print(f"⚠️ ImgBB: {e}")
    return None


def upload_to_catbox(image_path):
    try:
        with open(image_path, "rb") as f:
            r = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": f},
                timeout=60
            )
        if r.status_code == 200 and r.text.strip().startswith("http"):
            return r.text.strip()
    except Exception as e:
        print(f"⚠️ Catbox: {e}")
    return None


def upload_image_to_hosting(image_path, keys):
    for name, fn in [("ImgBB", lambda p: upload_to_imgbb(p, keys)), ("Catbox", upload_to_catbox)]:
        try:
            url = fn(image_path)
            if url:
                return url
        except Exception as e:
            print(f"⚠️ {name}: {e}")
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
        "-vframes", "1", "-q:v", "2",
        output_path
    ]
    try:
        result = subprocess.run(
            cmd, stdin=subprocess.DEVNULL,
            capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=60
        )
        return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        return False


# ========== СКЛЕЙКА ВИДЕО ==========
def concatenate_videos_with_progress(clip_paths, output_path, audio_path=None,
                                     width=1152, height=768, status_cb=None):
    if not clip_paths:
        return False
    abs_output = os.path.abspath(output_path)
    clips = []
    external_audio = None
    final = None
    try:
        for i, path in enumerate(clip_paths):
            if status_cb:
                status_cb(f"Загрузка клипа {i+1}/{len(clip_paths)}...")
            clip = VideoFileClip(path)
            try:
                clip = clip.resized(new_size=(int(width), int(height)))
            except Exception:
                pass
            clips.append(clip)

        if len(clips) == 1:
            final = clips[0]
        else:
            final = concatenate_videoclips(clips, method="compose")

        if audio_path and os.path.exists(audio_path):
            try:
                external_audio = AudioFileClip(audio_path)
                if final.audio is not None and CompositeAudioClip is not None:
                    final = final.with_audio(CompositeAudioClip([final.audio, external_audio]))
                else:
                    final = final.with_audio(external_audio)
            except Exception as e:
                print(f"⚠️ Озвучка: {e}")

        if status_cb:
            status_cb("Сохранение видео...")

        final.write_videofile(
            abs_output,
            codec="libx264", audio_codec="aac",
            fps=24, preset="ultrafast", threads=4, logger=None,
        )
        return os.path.exists(abs_output) and os.path.getsize(abs_output) > 1024
    except Exception as e:
        print(f"❌ MoviePy: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        for obj in (final, external_audio, *clips):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


# ========== ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ — ДИСПЕТЧЕР ==========
def generate_image_core(prompt, width, height, model, seed, nologo, enhance,
                        do_translate, keys, status_cb=None, provider="pollinations"):
    """Диспетчер генерации изображений по провайдеру."""
    if not prompt or not prompt.strip():
        raise ValueError("Введи промпт!")

    final_prompt = prompt.strip()
    translated_text = ""
    if do_translate:
        if status_cb:
            status_cb("Перевожу на английский...")
        final_prompt = translate_to_english(prompt)
        translated_text = final_prompt

    if status_cb:
        status_cb(f"Генерирую через {provider}...")

    # --- Pollinations ---
    if provider == "pollinations":
        model_name = parse_model_name(model)
        encoded = urllib.parse.quote(final_prompt)
        seed_val = int(seed) if seed and seed > 0 else int(time.time() * 1000) % 1_000_000
        url = (f"https://image.pollinations.ai/prompt/{encoded}"
               f"?width={int(width)}&height={int(height)}&seed={seed_val}"
               f"&model={model_name}&nologo={'true' if nologo else 'false'}"
               f"&enhance={'true' if enhance else 'false'}")

        r = requests.get(url, headers=get_pollinations_headers(keys), timeout=180)
        if r.status_code == 402:
            raise ValueError("❌ Лимит Pollinations исчерпан. Смените модель или провайдера.")
        r.raise_for_status()
        if not r.headers.get("Content-Type", "").startswith("image/"):
            raise ValueError(f"Pollinations вернул не картинку: {r.text[:200]}")
        img = Image.open(BytesIO(r.content)).convert("RGB")
        seed_used = str(seed_val)

    # --- Agnes AI ---
    elif provider == "agnes":
        img = generate_image_agnes(final_prompt, width, height, model, seed, keys)
        seed_used = "—"


    # Сохраняем результат
    seed_for_file = seed_used if seed_used != "—" else int(time.time() * 1000) % 1_000_000
    filename = os.path.join(OUTPUT_DIR, f"img_{seed_for_file}.png")
    img.save(filename)
    add_to_history("image", filename, prompt, translated_text)

    return img, filename, str(seed_for_file), translated_text


# ========== ГЕНЕРАЦИЯ ЧЕРЕЗ AGNES AI ==========
def generate_image_agnes(prompt, width, height, model, seed=None, keys=None):
    """Генерация изображения через Agnes AI API."""
    if keys is None:
        keys = GUEST_KEYS

    agnes_key = keys.get("agnes", "")
    if not agnes_key:
        raise ValueError("AGNES_API_KEY не задан")

    # Agnes принимает размер в формате "1024x768" или тир "1K", "2K"
    if width >= 2048 or height >= 2048:
        size = "2K"
    elif width >= 1536 or height >= 1536:
        size = "1K"
    else:
        size = f"{width}x{height}"

    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "extra_body": {"response_format": "url"},
    }
    # Добавляем соотношение сторон, если размер задан тиром
    if size in ("1K", "2K", "3K", "4K"):
        gcd = math.gcd(width, height)
        payload["ratio"] = f"{width // gcd}:{height // gcd}"

    resp = requests.post(
        "https://apihub.agnes-ai.com/v1/images/generations",
        headers={
            "Authorization": f"Bearer {agnes_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()

    # Agnes возвращает URL изображения
    image_url = data.get("data", [{}])[0].get("url")
    if not image_url:
        raise ValueError(f"Agnes не вернул URL: {data}")

    # Скачиваем изображение
    img_resp = requests.get(image_url, timeout=120)
    img_resp.raise_for_status()
    return Image.open(BytesIO(img_resp.content)).convert("RGB")




# ========== ГЕНЕРАЦИЯ ВИДЕО (AGNES) ==========
def generate_video_agnes(prompt, image_url=None, width=1152, height=768,
                         num_frames=121, frame_rate=24, keys=None):
    keys = keys or GUEST_KEYS
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
            resp = requests.post(
                "https://apihub.agnes-ai.com/v1/videos",
                headers=get_agnes_headers(keys), json=payload, timeout=300)
            resp.raise_for_status()
            break
        except requests.exceptions.Timeout:
            time.sleep(10)
            continue
        except requests.exceptions.RequestException as e:
            print(f"❌ Agnes ({attempt}): {e}")
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
        return None

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
            return None
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


def generate_video_core(prompt, duration, resolution_choice, generation_mode_choice,
                        input_image_path, add_tts, voice_choice, do_translate,
                        keys, status_cb=None):
    if not prompt.strip():
        raise ValueError("Введи промпт для видео!")

    width, height = RESOLUTIONS.get(resolution_choice, (1152, 768))
    generation_mode = GENERATION_MODES.get(generation_mode_choice, "single")
    frame_rate = 24

    video_prompt = prompt.strip()
    translated_text = ""
    if do_translate:
        if status_cb:
            status_cb("Перевожу на английский...")
        video_prompt = translate_to_english(prompt)
        translated_text = video_prompt

    image_url = None
    if input_image_path:
        if status_cb:
            status_cb("Загружаю фото на хостинг...")
        image_url = upload_image_to_hosting(input_image_path, keys)

    target_frames = round(duration * frame_rate)
    max_frames_single = 441

    if generation_mode == "single" and target_frames <= max_frames_single:
        num_frames = _nearest_8n_plus_1(target_frames)
        clips_needed = 1
    else:
        clip_seconds = 5
        clips_needed = max(1, round(duration / clip_seconds))
        num_frames = 121

    tmp_dir = os.path.abspath(os.path.join(OUTPUT_DIR, f"tmp_{int(time.time())}"))
    os.makedirs(tmp_dir, exist_ok=True)

    clip_paths = []
    previous_clip_path = None

    for i in range(clips_needed):
        if status_cb:
            status_cb(f"Клип {i+1}/{clips_needed}...")

        scene_prompt = f"{video_prompt}, scene {i+1}, cinematic" if clips_needed > 1 else video_prompt

        clip_image = None
        if i == 0:
            clip_image = image_url
        else:
            if previous_clip_path and os.path.exists(previous_clip_path):
                last_frame_path = os.path.join(tmp_dir, f"last_frame_{i:02d}.png")
                if extract_last_frame(previous_clip_path, last_frame_path):
                    uploaded_url = upload_image_to_hosting(last_frame_path, keys)
                    if uploaded_url:
                        clip_image = uploaded_url

        clip_data = generate_video_agnes(scene_prompt, clip_image, width, height,
                                         num_frames, frame_rate, keys)
        if not clip_data:
            raise ValueError(f"Не удалось сгенерировать клип {i+1}.")

        clip_path = os.path.join(tmp_dir, f"clip_{i:02d}.mp4")
        with open(clip_path, "wb") as f:
            f.write(clip_data)
        clip_paths.append(clip_path)
        previous_clip_path = clip_path
        time.sleep(2)

    audio_path = None
    audio_msg = "Без озвучки"
    if add_tts:
        if status_cb:
            status_cb("Генерация озвучки...")
        try:
            voice = VOICES.get(voice_choice, "ru-RU-DmitryNeural")
            audio_data = asyncio.run(generate_speech(prompt, voice))
            audio_path = os.path.join(tmp_dir, "voice.mp3")
            with open(audio_path, "wb") as f:
                f.write(audio_data)
            audio_msg = f"✅ Озвучка: {voice_choice}"
        except Exception as e:
            audio_msg = f"❌ Ошибка озвучки: {e}"

    if status_cb:
        status_cb("Склейка клипов...")

    timestamp = int(time.time())
    final_filename = os.path.abspath(os.path.join(OUTPUT_DIR, f"video_{timestamp}.mp4"))
    audio_filename_out = None

    if not concatenate_videos_with_progress(clip_paths, final_filename, audio_path,
                                             width, height, status_cb):
        raise ValueError("MoviePy не смог склеить клипы.")

    if audio_path:
        audio_filename_out = os.path.abspath(os.path.join(OUTPUT_DIR, f"audio_{timestamp}.mp3"))
        shutil.copy(audio_path, audio_filename_out)

    add_to_history("video", final_filename, prompt, translated_text)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return final_filename, audio_filename_out, audio_msg, translated_text


# ========== ИНИЦИАЛИЗАЦИЯ SESSION STATE ==========
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "is_owner" not in st.session_state:
    st.session_state.is_owner = False
if "session_keys" not in st.session_state:
    st.session_state.session_keys = GUEST_KEYS
if "img_result" not in st.session_state:
    st.session_state.img_result = None
if "vid_result" not in st.session_state:
    st.session_state.vid_result = None
if "batch_results" not in st.session_state:
    st.session_state.batch_results = []


# ========== ЭКРАН ВХОДА ==========
def render_login():
    st.title("🎨 Генерация бесплатно!")
    st.subheader("ИИ-генератор картинок и видео")
    st.write("Войдите как владелец или продолжите как гость.")

    col1, col2 = st.columns(2)
    with col1:
        username = st.text_input("Имя пользователя", placeholder="admin")
    with col2:
        password = st.text_input("Пароль", type="password", placeholder="••••••")

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("🔓 Войти", type="primary", width="stretch"):
            if username.strip() == OWNER_USERNAME and password == OWNER_PASSWORD:
                st.session_state.logged_in = True
                st.session_state.is_owner = True
                st.session_state.session_keys = OWNER_KEYS
                st.rerun()
            else:
                st.error("❌ Неверный логин или пароль")
    with bcol2:
        if st.button("👤 Войти как гость", width="stretch"):
            st.session_state.logged_in = True
            st.session_state.is_owner = False
            st.session_state.session_keys = GUEST_KEYS
            st.rerun()


# ========== ГЛАВНЫЙ ЭКРАН ==========
def render_main():
    keys = st.session_state.session_keys

    # Заголовок
    with st.container():
        if os.path.exists(LOGO_PATH):
            hcol1, hcol2 = st.columns([1, 8])
            with hcol1:
                st.image(LOGO_PATH, width=80)
            with hcol2:
                st.title("🎨 Генерация бесплатно!")
        else:
            st.title("🎨 Генерация бесплатно!")
        st.caption("**Картинки:** Pollinations · Agnes AI · InferencePort · **Видео:** Agnes AI · **Склейка:** MoviePy · **Озвучка:** EdgeTTS")
        role = "владелец" if st.session_state.is_owner else "гость"
        st.info(f"👤 Вы вошли как **{role}**")

    tab1, tab2, tab3, tab4 = st.tabs([
        "🖼️ Одна картинка",
        "📦 Пачка промптов",
        "🎬 Создать видео",
        "📜 История",
    ])

    # ========== ВКЛАДКА 1: ОДНА КАРТИНКА ==========
    with tab1:
        col_left, col_right = st.columns([2, 3])

        with col_left:
            prompt = st.text_area("Промпт (можно на русском)",
                                  placeholder="кот-космонавт в стиле киберпанк, неон", height=100)
            do_translate = st.checkbox("🌐 Переводить на английский", value=True, key="img_translate")

            # --- Выбор провайдера ---
            provider = st.selectbox(
                "🏭 Провайдер изображений",
                list(IMAGE_PROVIDERS.keys()),
                index=0,
                key="img_provider",
                help="InferencePort — без цензуры, Agnes AI — качество, Pollinations — скорость",
            )

            # --- Модель (динамически меняется по провайдеру) ---
            if provider == "Pollinations":
                default_model = POLLINATIONS_FREE_MODELS[0]
                model_choices = POLLINATIONS_FREE_MODELS
            elif provider == "Agnes AI":
                default_model = AGNES_MODELS[0]
                model_choices = AGNES_MODELS
            model = st.selectbox(
                "Модель",
                model_choices,
                index=0,
                key=f"img_model_{provider}",
            )

            # --- Доп. опции только для Pollinations ---
            if provider == "Pollinations":
                c1, c2 = st.columns(2)
                with c1:
                    nologo = st.checkbox("Без логотипа", value=True, key="img_nologo")
                with c2:
                    enhance = st.checkbox("✨ Улучшить", value=True, key="img_enhance")
            else:
                nologo = True
                enhance = False

            cw, ch = st.columns(2)
            with cw:
                width = st.slider("Ширина", 256, 1536, 1024, 64, key="img_width")
            with ch:
                height = st.slider("Высота", 256, 1536, 1024, 64, key="img_height")

            seed = st.number_input("Seed (0 = случайный)", min_value=0, value=0, step=1, key="img_seed")

            if st.button("✨ Сгенерировать", type="primary", width="stretch"):
                try:
                    progress = st.progress(0, text="Старт...")

                    def status(text):
                        progress.progress(0.5, text=text)

                    provider_key = IMAGE_PROVIDERS[provider]
                    img, path, seed_used, translated = generate_image_core(
                        prompt, width, height, model, seed, nologo, enhance,
                        do_translate, keys, status, provider_key
                    )
                    progress.progress(1.0, text="Готово!")
                    st.session_state.img_result = {
                        "img": img, "path": path, "seed": seed_used, "translated": translated
                    }
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ {e}")

        with col_right:
            res = st.session_state.img_result
            if res:
                st.image(res["img"], caption=f"seed={res['seed']}", width="stretch")
                with open(res["path"], "rb") as f:
                    st.download_button("⬇️ Скачать картинку", f,
                                       file_name=os.path.basename(res["path"]),
                                       mime="image/png",
                                       width="stretch")
                if res.get("translated"):
                    st.text_area("📝 Промпт на английском", res["translated"], height=80,
                                 disabled=True, key="img_translated_view")

    # ========== ВКЛАДКА 2: ПАЧКА ПРОМПТОВ ==========
    with tab2:
        st.markdown("Разделяй промпты через `;` или с новой строки.")
        col_left, col_right = st.columns([2, 3])

        with col_left:
            batch_prompts = st.text_area(
                "Промпты",
                placeholder="киберпанк город ночью;\nробот-самурай;\nкрасный дракон над горами",
                height=200, key="batch_prompts")
            b_do_translate = st.checkbox("🌐 Переводить на английский", value=True, key="batch_translate")

            b_provider = st.selectbox(
                "🏭 Провайдер",
                list(IMAGE_PROVIDERS.keys()),
                index=0,
                key="batch_provider",
            )

            if b_provider == "Pollinations":
                b_model_choices = POLLINATIONS_FREE_MODELS
            elif b_provider == "Agnes AI":
                b_model_choices = AGNES_MODELS

            b_model = st.selectbox("Модель", b_model_choices, index=0, key="batch_model")

            if b_provider == "Pollinations":
                b_enhance = st.checkbox("✨ Улучшить", value=True, key="batch_enhance")
            else:
                b_enhance = False

            bw, bh = st.columns(2)
            with bw:
                b_width = st.slider("Ширина", 256, 1024, 768, 64, key="batch_width")
            with bh:
                b_height = st.slider("Высота", 256, 1024, 768, 64, key="batch_height")

            if st.button("🚀 Сгенерировать всё", type="primary", width="stretch"):
                if not batch_prompts.strip():
                    st.error("Введи промпты!")
                else:
                    prompts = [p.strip() for p in batch_prompts.replace("\n", ";").split(";") if p.strip()]
                    results = []
                    progress = st.progress(0, text="Старт...")
                    provider_key = IMAGE_PROVIDERS[b_provider]
                    for i, p in enumerate(prompts):
                        progress.progress(i / len(prompts), text=f"[{i+1}/{len(prompts)}] {p[:40]}...")
                        try:
                            img, path, seed_used, _ = generate_image_core(
                                p, b_width, b_height, b_model, 0, True, b_enhance,
                                b_do_translate, keys, provider=provider_key
                            )
                            results.append((img, path, seed_used, p))
                        except Exception as e:
                            st.warning(f"❌ '{p}': {e}")
                        time.sleep(1)
                    progress.progress(1.0, text="Готово!")
                    st.session_state.batch_results = results
                    st.rerun()

        with col_right:
            results = st.session_state.get("batch_results", [])
            if results:
                cols = st.columns(2)
                for idx, (img, path, seed_used, p) in enumerate(results):
                    with cols[idx % 2]:
                        st.image(img, caption=f"seed={seed_used} | {p[:40]}...", width="stretch")

    # ========== ВКЛАДКА 3: СОЗДАТЬ ВИДЕО ==========
    with tab3:
        st.markdown("""
        ### Генерация видео через Agnes AI
        - **Цельный клип** — один запрос, до ~18 секунд
        - **Последовательные клипы** — клипы по 5 сек, каждый следующий начинается с последнего кадра предыдущего
        - **Фото → Видео** — загрузи картинку, и она оживёт
        """)
        col_left, col_right = st.columns([2, 3])

        with col_left:
            video_prompt = st.text_area("Промпт для видео",
                                        placeholder="кот танцует на пляже, волны разбиваются о берег",
                                        height=100, key="vid_prompt")
            input_image = st.file_uploader("📷 Начальный кадр (необязательно)",
                                           type=["png", "jpg", "jpeg"], key="vid_upload")

            v_do_translate = st.checkbox("🌐 Переводить промпт на английский", value=True, key="vid_translate")

            video_duration = st.slider("Длительность (секунд)", 5, 60, 10, 5, key="vid_duration")
            generation_mode = st.radio("🎬 Режим генерации",
                                       list(GENERATION_MODES.keys()),
                                       index=0, key="vid_mode")
            resolution_choice = st.selectbox("📺 Разрешение видео",
                                             list(RESOLUTIONS.keys()),
                                             index=0, key="vid_res")

            vc1, vc2 = st.columns(2)
            with vc1:
                add_tts = st.checkbox("🗣️ Озвучить (TTS)", value=False, key="vid_tts")
            with vc2:
                voice_choice = st.selectbox("Голос", list(VOICES.keys()), index=0, key="vid_voice")

            if st.button("🎬 Сгенерировать видео", type="primary", width="stretch"):
                if not video_prompt.strip():
                    st.error("Введи промпт для видео!")
                else:
                    input_path = None
                    if input_image is not None:
                        input_path = os.path.join(OUTPUT_DIR, f"upload_{int(time.time())}_{input_image.name}")
                        with open(input_path, "wb") as f:
                            f.write(input_image.getbuffer())

                    try:
                        progress = st.progress(0, text="Старт...")

                        def status(text):
                            progress.progress(0.5, text=text)

                        final_video, final_audio, audio_msg, translated = generate_video_core(
                            video_prompt, video_duration, resolution_choice, generation_mode,
                            input_path, add_tts, voice_choice, v_do_translate, keys, status
                        )
                        progress.progress(1.0, text="Готово!")
                        st.session_state.vid_result = {
                            "video": final_video, "audio": final_audio,
                            "audio_msg": audio_msg, "translated": translated
                        }
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")

        with col_right:
            res = st.session_state.vid_result
            if res:
                st.video(res["video"])
                with open(res["video"], "rb") as f:
                    st.download_button("⬇️ Скачать видео (MP4)", f,
                                       file_name=os.path.basename(res["video"]),
                                       mime="video/mp4", width="stretch")
                if res.get("audio"):
                    st.audio(res["audio"])
                    with open(res["audio"], "rb") as f:
                        st.download_button("⬇️ Скачать озвучку (MP3)", f,
                                           file_name=os.path.basename(res["audio"]),
                                           mime="audio/mpeg", width="stretch")
                if res.get("audio_msg"):
                    st.info(res["audio_msg"])
                if res.get("translated"):
                    st.text_area("📝 Промпт на английском", res["translated"],
                                 height=80, disabled=True, key="vid_translated_view")

    # ========== ВКЛАДКА 4: ИСТОРИЯ ==========
    with tab4:
        st.markdown("### Прошлые генерации")

        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("🔄 Обновить", width="stretch"):
                st.rerun()
        with c2:
            if st.button(f"🧹 Удалить старше {AUTO_CLEAN_DAYS} дней", width="stretch"):
                n = cleanup_old_files(AUTO_CLEAN_DAYS)
                st.success(f"🧹 Удалено {n} файлов")
                time.sleep(1)
                st.rerun()

        hist_tab1, hist_tab2 = st.tabs(["🖼️ Картинки", "🎬 Видео"])

        with hist_tab1:
            items = get_history_items("image")
            if not items:
                st.info("История пуста")
            else:
                cols = st.columns(3)
                for i, item in enumerate(items):
                    with cols[i % 3]:
                        try:
                            st.image(item["filepath"], caption=f"{item['date']}\n{item['prompt'][:50]}...",
                                     width="stretch")
                            if st.button("🗑️ Удалить", key=f"del_img_{i}"):
                                try:
                                    os.remove(item["filepath"])
                                except Exception:
                                    pass
                                remove_from_history(item["filepath"])
                                st.rerun()
                        except Exception:
                            pass

        with hist_tab2:
            items = get_history_items("video")
            if not items:
                st.info("История пуста")
            else:
                cols = st.columns(2)
                for i, item in enumerate(items):
                    with cols[i % 2]:
                        try:
                            st.video(item["filepath"])
                            st.caption(f"{item['date']} · {item['prompt'][:60]}...")
                            if st.button("🗑️ Удалить", key=f"del_vid_{i}"):
                                try:
                                    os.remove(item["filepath"])
                                except Exception:
                                    pass
                                remove_from_history(item["filepath"])
                                st.rerun()
                        except Exception:
                            pass

    # Подвал
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; padding: 20px 0; color: #666;">
        <p style="font-size: 16px; margin: 6px 0;"><b>Создано Egorov Company (Пётр Егоров)</b></p>
        <p style="font-size: 15px; margin: 6px 0;">
            📞 <a href="tel:+79911548118" style="color: #4a90e2; text-decoration: none;">8 991 154 81 18</a>
        </p>
        <p style="font-size: 13px; margin: 12px 0 0 0; color: #999;">
            © 2026 Генерация бесплатно! Все права защищены.
        </p>
    </div>
    """, unsafe_allow_html=True)


# ========== ЗАПУСК ==========
if not st.session_state.logged_in:
    render_login()
else:
    render_main()
