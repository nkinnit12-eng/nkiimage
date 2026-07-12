

import streamlit as st
import google.generativeai as genai
import csv
import requests
import os
import numpy as np
from moviepy import (VideoFileClip, AudioFileClip, ImageClip, TextClip, CompositeVideoClip, ColorClip, concatenate_videoclips)
import base64
import re
import edge_tts
import asyncio
from PIL import Image, ImageDraw, ImageFont
from io import StringIO, BytesIO
import contextlib
import zipfile
import time
import random
import subprocess
import json
import hashlib
from datetime import datetime
# YouTube upload dependencies (google-auth + googleapiclient)
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.auth.transport.requests import Request
    import pickle
    _YT_LIBS_OK = True
except ImportError:
    _YT_LIBS_OK = False





def check_password():
    if st.session_state.get("authenticated"):
        return True
    
    pwd = st.text_input("Password", type="password")
    if pwd:
        if hashlib.sha256(pwd.encode()).hexdigest() == st.secrets["PASSWORD_HASH"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False

if not check_password():
    st.stop()

# For Arabic text rendering
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    st.error("مكتبات العربية غير مثبتة. الرجاء تشغيل: pip install python-bidi arabic_reshaper")
    st.stop()

# --- HELPER FUNCTIONS (FONT/POLICY FIXES) ---
def download_default_font(font_path="arabic_font.ttf"):
    if os.path.exists(font_path) and os.path.getsize(font_path) > 10000:
        return True
    st.warning(f"لم يتم العثور على ملف الخط '{font_path}'. جاري محاولة تحميل خط عربي افتراضي...")
    font_url = "https://fonts.google.com/download?family=Amiri"
    try:
        response = requests.get(font_url, stream=True, timeout=30)
        response.raise_for_status()
        with zipfile.ZipFile(BytesIO(response.content)) as z:
            font_file_name = next((name for name in z.namelist() if 'Amiri-Regular.ttf' in name), None)
            if font_file_name:
                with z.open(font_file_name) as source, open(font_path, 'wb') as target:
                    target.write(source.read())
                st.success(f"تم تحميل وتثبيت الخط الافتراضي بنجاح في '{font_path}'.")
                return True
            else: return False
    except Exception as e:
        st.error(f"فشل تحميل الخط الافتراضي: {e}")
        return False

def fix_imagemagick_policy():
    permissive_policy = """<policymap><policy domain="cache" name="shared-secret" value="passphrase" stealth="true"/></policymap>"""
    policy_file_path = "/etc/ImageMagick-6/policy.xml"
    if not os.path.exists(policy_file_path): return
    try:
        with open(policy_file_path) as f:
            if 'rights="none" pattern="LABEL"' not in f.read(): return
    except: pass
    try:
        subprocess.run(["sudo", "cp", policy_file_path, f"{policy_file_path}.bak"], check=True, capture_output=True)
        with open("/tmp/policy.xml", "w") as f: f.write(permissive_policy)
        subprocess.run(["sudo", "mv", "/tmp/policy.xml", policy_file_path], check=True, capture_output=True)
    except Exception: pass

# --- CONFIGURATION ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# <-- IMPORTANT: REPLACE WITH YOUR ACTUAL KEY
genai.configure(api_key=GEMINI_API_KEY)
try:
    #model = genai.GenerativeModel('models/gemini-2.5-flash')
    #gemini-3-flash-preview  gemini-2.5-pro

    model = genai.GenerativeModel('models/gemini-3.1-flash-lite-preview')
    
except Exception as e:
    st.error(f"خطأ في تهيئة نموذج Gemini: {e}")
    st.stop()

FONT_PATH = 'arabic_font.ttf'
IMAGE_DIR = 'صور'
AUDIO_DIR = 'صوتيات'

ar_male = { "AE": "ar-AE-HamdanNeural", "BH": "ar-BH-AliNeural", "MA": "ar-MA-JamalNeural", "SA": "ar-SA-HamedNeural", "QA": "ar-QA-MoazNeural", "KW": "ar-KW-FahedNeural" }
ar_Female = { "AE": "ar-AE-FatimaNeural", "BH": "ar-BH-LailaNeural", "MA": "ar-MA-MounaNeural", "SA": "ar-SA-ZariyahNeural", "QA": "ar-QA-AmalNeural", "KW": "ar-KW-NouraNeural" }

# --- CORE FUNCTIONS ---
def fetch_facts_from_gemini_ar(topic_ar, num_facts):
    gemini_prompt = f"""
أنشئ عدد {num_facts} من الحقائق الفريدة والمثيرة للاهتمام باللغة العربية حول الموضوع التالي: "{topic_ar}".
التعليمات:
- استخدم "هل تعلم" فقط في بداية الحقيقة الأولى فقط.
- في بقية الحقائق، لا تستخدم "هل تعلم"، بل ابدأ مباشرة بالمعلومة.
- بعد كل حقيقة، أضف وصفًا نصيًا باللغة الإنجليزية يمكن استخدامه لتوليد صورة تمثل هذه الحقيقة.
و يجب أن تتضمن الصورة دائمًا عدم احتواءها على أي نص أو أي رسوم بيانية
**التنسيق المطلوب لكل حقيقة بالضبط:**
حقيقة: [اكتب الحقيقة هنا باللغة العربية]
وصف الصورة: [اكتب وصف الصورة هنا باللغة الإنجليزية] 
"""
    try:
        print(f"INFO: يتم الآن طلب {num_facts} حقائق حول '{topic_ar}' من Gemini...")
        response = model.generate_content(gemini_prompt)
        generated_text = response.text.strip()
        pattern = re.compile(r'حقيقة:\s*(.*?)\n\s*وصف الصورة:\s*(.*?)(?:\n\n|\n*$)', re.DOTALL | re.IGNORECASE)
        matches = pattern.findall(generated_text)
        if not matches:
            print("WARNING: لم يتم العثور على حقائق مطابقة للنمط المتوقع.")
            return []
        print(f"SUCCESS: تم تحليل {len(matches)} حقائق بنجاح.")
        return [(fact.strip(), prompt.strip()) for fact, prompt in matches]
    except Exception as e:
        print(f"ERROR: خطأ في Gemini API: {e}")
        return []

def download_image(prompt, filename, width=1080, height=1920, seed=None,
                   model="@cf/black-forest-labs/flux-2-klein-4b", quality="standard", style=""):
    MAX_RETRIES = 3

    for attempt in range(MAX_RETRIES):
        print(f"INFO: محاولة {attempt + 1}/{MAX_RETRIES} لتحميل الصورة: {prompt[:40]}...")
        try:
            random_seed = random.randint(1, 9999999)
            params = {
                "target": "cloudflare",
                "prompt": prompt,
                "model": model,
                "width": width,
                "height": height,
                "seed": random_seed,
            }
            WEBSITEURLAPI = os.environ["WEBSITEURLAPI"]
            response = requests.get(
                WEBSITEURLAPI,
                params=params,
                timeout=90,
            )
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type:
                data = response.json()
                if "error" in data:
                    raise ValueError(data["error"])
                # Try to extract image from JSON just in case
                img_b64 = (data.get("b64_json") or
                           (data.get("data") or [{}])[0].get("b64_json"))
                if img_b64:
                    with open(filename, "wb") as f:
                        f.write(base64.b64decode(img_b64))
                else:
                    raise ValueError(f"Unexpected JSON response: {list(data.keys())}")
            else:
                # Direct image bytes returned
                with open(filename, "wb") as f:
                    f.write(response.content)

            print(f"SUCCESS: تم تحميل الصورة: {os.path.basename(filename)}")
            return True

        except Exception as e:
            print(f"WARNING: فشلت المحاولة {attempt + 1}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(5)
            else:
                print(f"ERROR: فشلت جميع محاولات التحميل لـ '{prompt}'.")

    # Fallback: generate placeholder image
    try:
        print(f"WARNING: إنشاء صورة بديلة لـ '{prompt}'")
        img = Image.new("RGB", (width, height), color="darkgrey")
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(FONT_PATH, 40)
        text = f"فشل تحميل الصورة:\n{prompt[:100]}"
        text_bbox = d.textbbox((0, 0), text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        d.text(
            ((width - text_width) / 2, (height - text_height) / 2),
            text, fill=(255, 255, 255), font=font, align="center"
        )
        img.save(filename)
        return True
    except Exception:
        return False


def generate_images(facts, image_dir=IMAGE_DIR, width=1080, height=1920,
                    model="@cf/black-forest-labs/flux-2-klein-4b", quality="standard", style=""):
    if not os.path.exists(image_dir):
        os.makedirs(image_dir)
    image_files = []
    print(f"INFO: بدء توليد {len(facts)} صور...")
    for i, (fact_ar, image_prompt) in enumerate(facts, start=1):
        filename = os.path.join(image_dir, f"image_{random.randint(1000, 9999)}_{i}.png")
        if download_image(image_prompt, filename, width, height,
                          model=model, quality=quality, style=style):
            image_files.append((filename, fact_ar, image_prompt))
    return image_files

def create_text_clip_ar(text_ar, duration, video_width, initial_font_size=60, top_margin=50):
    reshaped_text = arabic_reshaper.reshape(text_ar)
    bidi_text = get_display(reshaped_text)
    padding_vertical, padding_horizontal, border_radius = 10, 15, 30
    max_text_width = int(video_width * 0.85)
    try:
        temp_text_clip = TextClip(
            bidi_text, font=FONT_PATH, size=(max_text_width, None),
            color='black', method='caption', align='East', fontsize=initial_font_size
        )
    except Exception as e:
        print(f"ERROR: خطأ عند إنشاء مقطع نص مؤقت لـ '{text_ar}': {e}")
        return ColorClip(size=(1,1), color=(0,0,0,0), duration=duration)

    text_width, text_height = temp_text_clip.size
    bg_width, bg_height = text_width + 2 * padding_horizontal, text_height + 2 * padding_vertical
    bg_image = Image.new('RGBA', (int(bg_width), int(bg_height)), (255, 255, 255, 0))
    draw = ImageDraw.Draw(bg_image)
    draw.rounded_rectangle([(0, 0), (bg_width, bg_height)], fill=(255, 255, 255, 200), radius=border_radius)
    bg_clip = ImageClip(np.array(bg_image)).with_duration(duration)
    text_clip_final = TextClip(
        bidi_text, font=FONT_PATH, size=(max_text_width, None),
        color='black', method='caption', align='East', fontsize=initial_font_size
    ).with_duration(duration).with_position('center')
    composite_text_bg = CompositeVideoClip([bg_clip, text_clip_final], size=(int(bg_width), int(bg_height)))
    return composite_text_bg.with_position(('center', top_margin))

def resize_image_to_fill(image_path, duration, video_width, video_height):
    try:
        with Image.open(image_path) as img:
            img_aspect, video_aspect = img.width / img.height, video_width / video_height
            if img_aspect > video_aspect:
                new_height, new_width = video_height, int(video_height * img_aspect)
            else:
                new_width, new_height = video_width, int(video_width / img_aspect)
            resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            left, top = (new_width - video_width) / 2, (new_height - video_height) / 2
            cropped_img = resized_img.crop((left, top, left + video_width, top + video_height))
            return ImageClip(np.array(cropped_img)).with_duration(duration)
    except Exception as e:
        print(f"ERROR: خطأ في تغيير حجم الصورة {image_path}: {e}")
        return ColorClip(size=(video_width, video_height), color=(0, 0, 0), duration=duration)

def custom_zoom(clip, zoom_factor=1.15, duration=5, video_width=1080, video_height=1920):
    from moviepy.video.VideoClip import VideoClip
    def make_frame(t):
        zoom = 1 + (zoom_factor - 1) * (t / duration)
        new_size = (int(video_width * zoom), int(video_height * zoom))
        frame = clip.get_frame(0)
        img = Image.fromarray(frame).resize(new_size, Image.Resampling.LANCZOS)
        left, top = (new_size[0] - video_width) // 2, (new_size[1] - video_height) // 2
        return np.array(img.crop((left, top, left + video_width, top + video_height)))
    return VideoClip(make_frame, duration=duration)

def split_arabic_text_for_subtitles(text_ar):
    return [s.strip() for s in re.split(r'[.؟،!]\s+|\n+', text_ar) if s.strip()]

async def generate_audio_files_ar(facts_ar, audio_dir=AUDIO_DIR, voice='ar-SA-HamedNeural'):
    print(f"INFO: سيتم استخدام الصوت: {voice}")
    if not os.path.exists(audio_dir): os.makedirs(audio_dir)
    for i, (fact_text_ar, _) in enumerate(facts_ar, start=1):
        output_file = os.path.join(audio_dir, f"fact_{i}.mp3")
        print(f"INFO: جاري إنشاء الملف الصوتي لـ: {fact_text_ar[:30]}...")
        try:
            await edge_tts.Communicate(fact_text_ar, voice).save(output_file)
            print(f"SUCCESS: تم إنشاء: {os.path.basename(output_file)}")
        except Exception as e:
            print(f"ERROR: فشل إنشاء الملف الصوتي. إنشاء ملف صامت بدلاً منه. الخطأ: {e}")
            AudioFileClip.silence(duration=1, fps=22050).write_audiofile(output_file)

def generate_audio_sync_ar(facts_ar, audio_dir=AUDIO_DIR, voice='ar-SA-HamedNeural'):
    if os.path.exists(audio_dir):
        for f in os.listdir(audio_dir): os.remove(os.path.join(audio_dir, f))
    else: os.makedirs(audio_dir)
    asyncio.run(generate_audio_files_ar(facts_ar, audio_dir, voice))

def load_subscribe_clip(video_width, video_height, subscribe_path="subscribe.mp4"):
    """Load and resize subscribe.mp4 to match the video dimensions."""
    if not os.path.exists(subscribe_path):
        print(f"WARNING: ملف subscribe.mp4 غير موجود في '{subscribe_path}'. سيتم تخطي مقطع الاشتراك.")
        return None
    try:
        clip = VideoFileClip(subscribe_path)
        if clip.size != (video_width, video_height):
            clip = clip.resized((video_width, video_height))
        print(f"INFO: تم تحميل مقطع الاشتراك: {subscribe_path} ({clip.duration:.1f} ثانية)")
        return clip
    except Exception as e:
        print(f"WARNING: فشل تحميل subscribe.mp4: {e}")
        return None


async def _generate_intro_audio(text_ar, output_path, voice):
    await edge_tts.Communicate(text_ar, voice).save(output_path)


def generate_intro_scene(animal_name, num_facts, video_width, video_height,
                         audio_dir=AUDIO_DIR, voice='ar-SA-HamedNeural',
                         image_model="@cf/black-forest-labs/flux-2-klein-4b"):
    """Generate the intro image + audio scene."""
    print("INFO: إنشاء مشهد المقدمة...")

    intro_text = f"إليك {num_facts} حقيقة مذهلة عن {animal_name}"
    intro_audio_path = os.path.join(audio_dir, "intro.mp3")
    try:
        asyncio.run(_generate_intro_audio(intro_text, intro_audio_path, voice))
        print(f"SUCCESS: تم إنشاء صوت المقدمة: {intro_audio_path}")
    except Exception as e:
        print(f"ERROR: فشل إنشاء صوت المقدمة: {e}")
        return None

    audio_clip = AudioFileClip(intro_audio_path)
    audio_duration = audio_clip.duration if audio_clip.duration and audio_clip.duration > 0 else 4

    if not os.path.exists(IMAGE_DIR):
        os.makedirs(IMAGE_DIR)
    safe_name = "".join(c for c in animal_name if c.isalnum() or c in "_-").strip() or "animal"
    intro_img_path = os.path.join(IMAGE_DIR, f"intro_{safe_name}.png")
    intro_prompt = f"A stunning wildlife portrait of a {animal_name}, professional nature photography, ultra detailed, no text, no watermark"
    download_image(intro_prompt, intro_img_path, width=video_width, height=video_height, model=image_model)

    img_clip = resize_image_to_fill(intro_img_path, audio_duration, video_width, video_height)
    img_clip = custom_zoom(img_clip, duration=audio_duration, video_width=video_width, video_height=video_height)
    img_clip = img_clip.with_audio(audio_clip)

    intro_scene = CompositeVideoClip([img_clip], size=(video_width, video_height)).with_duration(audio_duration)
    print("SUCCESS: تم إنشاء مشهد المقدمة.")
    return intro_scene


def create_video_from_data_ar(image_files_with_facts, output_filename, video_width, video_height,
                               audio_dir=AUDIO_DIR, show_text_on_video=True,
                               animal_name="", num_facts=10, voice='ar-SA-HamedNeural',
                               image_model="@cf/black-forest-labs/flux-2-klein-4b"):
    raw_clips = []
    scenes_added = 0
    subscribe_clip = load_subscribe_clip(video_width, video_height)

    # --- Prepend intro scene ---
    intro_scene = generate_intro_scene(
        animal_name, num_facts, video_width, video_height,
        audio_dir=audio_dir, voice=voice, image_model=image_model
    )
    if intro_scene is not None:
        raw_clips.append(intro_scene)
        print("INFO: تم إضافة مشهد المقدمة في بداية الفيديو.")

    print(f"\nINFO: بدء تجميع الفيديو من {len(image_files_with_facts)} مشاهد.")
    for i, (image_file_path, fact_text_ar, _) in enumerate(image_files_with_facts, start=1):
        print(f"--- [معالجة المشهد {i}/{len(image_files_with_facts)}] ---")
        audio_file_path = os.path.join(audio_dir, f"fact_{i}.mp3")
        if not os.path.exists(audio_file_path):
            print(f"WARNING: الملف الصوتي {audio_file_path} غير موجود. تخطي المشهد.")
            continue

        print(f"  - تحميل الصوت: {os.path.basename(audio_file_path)}")
        audio_clip = AudioFileClip(audio_file_path)
        audio_duration = audio_clip.duration if audio_clip.duration and audio_clip.duration > 0 else 3
        if audio_duration == 3:
            audio_clip = AudioFileClip.silence(duration=audio_duration, fps=22050)
            print(f"WARNING: مدة الصوت غير صالحة، تم تعيينها إلى 3 ثوانٍ.")

        print(f"  - تغيير حجم الصورة وتكبيرها: {os.path.basename(image_file_path)}")
        img_clip = resize_image_to_fill(image_file_path, audio_duration, video_width, video_height)
        img_clip = custom_zoom(img_clip, duration=audio_duration, video_width=video_width, video_height=video_height)
        img_clip = img_clip.with_audio(audio_clip)

        text_overlay_clips = []
        if show_text_on_video and fact_text_ar:
            print("  - إنشاء تراكبات النص...")
            text_segments = split_arabic_text_for_subtitles(fact_text_ar) or [fact_text_ar]
            total_words = sum(len(s.split()) for s in text_segments)
            current_text_time = 0
            for seg_idx, segment in enumerate(text_segments, 1):
                if not segment.strip(): continue
                print(f"    - إنشاء مقطع نصي للجزء {seg_idx}: '{segment[:20]}...'")
                segment_duration = audio_duration * (len(segment.split()) / total_words) if total_words > 0 else audio_duration / len(text_segments)
                text_c = create_text_clip_ar(segment, segment_duration, video_width).with_start(current_text_time)
                text_overlay_clips.append(text_c)
                current_text_time += segment_duration

        print(f"  - تركيب المشهد {i}")
        scene_composite = CompositeVideoClip([img_clip] + text_overlay_clips, size=(video_width, video_height)).with_duration(audio_duration)
        raw_clips.append(scene_composite)
        scenes_added += 1

        # Insert subscribe.mp4 after every 2nd scene (scenes 2, 4, 6, ...)
        if scenes_added == 2 and subscribe_clip is not None:
            print(f"  ++ إدراج مقطع الاشتراك بعد المشهد {i}")
            raw_clips.append(subscribe_clip)

    if not raw_clips:
        print("ERROR: لم يتم إنشاء أي مقاطع خام.")
        return False, None
    
    print("\nINFO: تم تركيب جميع المشاهد. الآن يتم تجميع الفيديو النهائي...")
    final_video_clip = concatenate_videoclips(raw_clips)
    try:
        final_video_clip.write_videofile(output_filename, fps=30, codec='libx264', audio_codec='aac', logger='bar')
        print(f"SUCCESS: تم إنشاء الفيديو بنجاح: {output_filename}")
        return True, output_filename
    except Exception as e:
        print(f"ERROR: فشل في كتابة ملف الفيديو: {e}")
        return False, None

# --- STREAMLIT UI AND APP LOGIC ---
class StreamlitLogger:
    def __init__(self, placeholder):
        self.placeholder = placeholder
        self.log_messages = []
        self.MAX_LOG_LINES = 200
    def write(self, text):
        if text.strip():
            self.log_messages.extend(l.strip() for l in text.split('\n') if l.strip())
        self.log_messages = self.log_messages[-self.MAX_LOG_LINES:]
        log_string = "\n".join(self.log_messages)
        try:
            self.placeholder.text_area("سجل العمليات:", value=log_string, height=300, key="main_log_widget", disabled=True)
        except Exception:
            self.placeholder.code(log_string, language=None)
    def flush(self): pass

# ─── Password authentication ──────────────────────────────────────────────────
_HISTORY_FILE = "video_history.json"

def _check_password():
    """
    Returns True if the user is authenticated.
    Password is stored (hashed) in .streamlit/secrets.toml:
        [auth]
        password_hash = "<sha256 of your password>"
    Generate the hash once with:
        python -c "import hashlib; print(hashlib.sha256(b'YOUR_PASSWORD').hexdigest())"
    """
    # Load expected hash from secrets
    try:
        expected_hash = st.secrets["auth"]["password_hash"]
    except Exception:
        st.error("⚠️ لم يتم تعيين كلمة المرور في secrets.toml — راجع التعليمات.")
        st.stop()

    if st.session_state.get("authenticated"):
        return True

    st.markdown("## 🔐 تسجيل الدخول")
    pwd = st.text_input("كلمة المرور", type="password", key="login_pwd")
    if st.button("دخول"):
        entered_hash = hashlib.sha256(pwd.encode()).hexdigest()
        if entered_hash == expected_hash:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("❌ كلمة المرور غير صحيحة")
    st.stop()


# ─── History helpers ───────────────────────────────────────────────────────────
def _load_history():
    if os.path.exists(_HISTORY_FILE):
        try:
            with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_history_entry(animal, num_facts, video_path, yt_url=None):
    history = _load_history()
    entry = {
        "date":       datetime.now().strftime("%Y-%m-%d %H:%M"),
        "animal":     animal,
        "num_facts":  num_facts,
        "video_path": video_path,
        "filename":   os.path.basename(video_path) if video_path else "",
        "yt_url":     yt_url or "",
        "size_mb":    round(os.path.getsize(video_path) / (1024*1024), 1) if video_path and os.path.exists(video_path) else 0,
    }
    history.insert(0, entry)   # newest first
    with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _delete_history_entry(index):
    history = _load_history()
    if 0 <= index < len(history):
        history.pop(index)
    with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ─── YouTube Shorts upload ────────────────────────────────────────────────────
_YT_SCOPES      = ["https://www.googleapis.com/auth/youtube.upload"]
_YT_TOKEN_FILE  = "yt_token.pickle"

def _get_youtube_client(client_secrets_path):
    """Authenticate and return a YouTube API client. Caches token to disk."""
    creds = None
    if os.path.exists(_YT_TOKEN_FILE):
        with open(_YT_TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, _YT_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(_YT_TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)


def upload_to_youtube_shorts(video_path, animal_name, title_template, description="", tags=None):
    """
    Upload a video as a YouTube Short.
    title_template should contain {animal} placeholder, e.g.:
        "{animal} حقائق مذهلة لن تصدقها 🐾 #shorts"
    Returns (success: bool, video_id: str | error_msg: str)
    """
    if not _YT_LIBS_OK:
        return False, "مكتبات YouTube غير مثبتة. شغّل: pip install google-auth google-auth-oauthlib google-api-python-client"

    client_secrets = st.session_state.get("yt_client_secrets_path")
    if not client_secrets or not os.path.exists(client_secrets):
        return False, "لم يتم تحميل ملف client_secrets.json لـ YouTube."

    try:
        youtube = _get_youtube_client(client_secrets)
        title = title_template.replace("{animal}", animal_name)[:100]   # YT title max 100 chars

        body = {
            "snippet": {
                "title":       title,
                "description": description or f"حقائق مذهلة عن {animal_name} #shorts",
                "tags":        tags or [animal_name, "حقائق", "shorts", "حيوانات"],
                "categoryId":  "15",   # Pets & Animals
            },
            "status": {
                "privacyStatus":           "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=5 * 1024 * 1024)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()

        video_id = response.get("id", "")
        return True, video_id

    except Exception as e:
        return False, str(e)


# ─── Automation scheduler (module-level thread state) ─────────────────────────
import threading as _threading
_auto_lock   = _threading.Lock()
_auto_status = {}

def _run_automation(animals, interval_seconds, params):
    global _auto_status
    for idx, animal in enumerate(animals):
        with _auto_lock:
            if not _auto_status.get('running'):
                break
            _auto_status['current_index'] = idx
            _auto_status['log'].append(f"⚙️ بدء معالجة: **{animal}** ({idx+1}/{len(animals)})")
        try:
            num_facts = params['num_facts']
            video_w   = params['video_w']
            video_h   = params['video_h']
            voice     = params['voice']
            show_text = params['show_text']
            facts = fetch_facts_from_gemini_ar(animal, num_facts)
            if not facts:
                with _auto_lock:
                    _auto_status['log'].append(f"⚠️ فشل جلب الحقائق لـ {animal}، تخطي.")
                continue
            images_with_facts = generate_images(facts, width=video_w, height=video_h)
            generate_audio_sync_ar(facts, voice=voice)
            output_video_file = os.path.join("videos", f"{num_facts} حقائق مذهلة عن {animal} لن تصدقها.mp4")
            success, final_path = create_video_from_data_ar(
                images_with_facts, output_video_file, video_w, video_h,
                show_text_on_video=show_text, animal_name=animal,
                num_facts=num_facts, voice=voice
            )
            with _auto_lock:
                if success and final_path:
                    _auto_status['log'].append(f"✅ اكتمل الفيديو: `{os.path.basename(final_path)}`")
                    _auto_status.setdefault('done_paths', []).append(final_path)
                    yt_url = ""
                    # Auto-upload to YouTube if enabled
                    if params.get('auto_yt_upload'):
                        _auto_status['log'].append(f"📤 جاري رفع الفيديو إلى YouTube Shorts: {animal}...")
                        ok, result = upload_to_youtube_shorts(
                            final_path, animal,
                            params.get('yt_title_template', '{animal} حقائق مذهلة #shorts')
                        )
                        if ok:
                            yt_url = f"https://youtube.com/shorts/{result}"
                            _auto_status['log'].append(f"✅ تم الرفع إلى YouTube: {yt_url}")
                        else:
                            _auto_status['log'].append(f"❌ فشل رفع YouTube: {result}")
                    _save_history_entry(animal, params.get('num_facts', 0), final_path, yt_url)
                else:
                    _auto_status['log'].append(f"❌ فشل إنشاء الفيديو لـ {animal}.")
        except Exception as e:
            with _auto_lock:
                _auto_status['log'].append(f"❌ خطأ أثناء معالجة {animal}: {e}")
        if idx < len(animals) - 1:
            with _auto_lock:
                if not _auto_status.get('running'):
                    break
                h = interval_seconds // 3600
                m = (interval_seconds % 3600) // 60
                _auto_status['log'].append(f"⏳ انتظار {h}h {m}m قبل الفيديو التالي...")
            time.sleep(interval_seconds)
    with _auto_lock:
        _auto_status['running'] = False
        _auto_status['log'].append("🏁 انتهت جميع الفيديوهات المجدولة!")


def run_streamlit_app_ar():
    _check_password()   # ← blocks until authenticated
    st.title('🎬 مولد الفيديو بالذكاء الاصطناعي (بالعربية)')
    st.sidebar.header("إعدادات الفيديو")
    if 'step' not in st.session_state: st.session_state.step = 'initial'
    if 'image_data' not in st.session_state: st.session_state.image_data = {}
    if 'facts_data' not in st.session_state: st.session_state.facts_data = {}

    animals_input = st.sidebar.text_area("أدخل قائمة بالحيوانات (كل حيوان في سطر)", "أسد\nزرافة")
    animals = [a.strip() for a in animals_input.split('\n') if a.strip()]
    num_facts = st.sidebar.slider("عدد الحقائق", 1, 100, 10)
    aspect_ratio = st.sidebar.radio("نسبة العرض إلى الارتفاع", ["16:9 (أفقي)", "9:16 (عمودي)"], index=1)
    video_w, video_h = (1280, 720) if aspect_ratio.startswith("16:9") else (720, 1280)
    show_text_on_video = st.sidebar.checkbox("إظهار النص على الفيديو", False)

    st.sidebar.header("إعدادات الصوت")
    voice_cat_keys = {'أصوات عربية (ذكور)': 'ar_male', 'أصوات عربية (إناث)': 'ar_Female'}
    sel_voice_cat = st.sidebar.selectbox("فئة الصوت", list(voice_cat_keys.keys()))
    voice_dict = globals()[voice_cat_keys[sel_voice_cat]]
    voice_accent_key = st.sidebar.selectbox("اللهجة/الصوت", list(voice_dict.keys()))
    selected_voice = voice_dict[voice_accent_key]

    if not os.path.exists("videos"): os.makedirs("videos")

    # YouTube settings
    st.sidebar.markdown("---")
    st.sidebar.header("🎬 إعدادات YouTube")
    yt_secrets_file = st.sidebar.file_uploader("📂 ارفع ملف client_secrets.json", type="json", key="yt_secrets_uploader")
    if yt_secrets_file:
        secrets_save_path = "yt_client_secrets.json"
        with open(secrets_save_path, "wb") as f:
            f.write(yt_secrets_file.read())
        st.session_state["yt_client_secrets_path"] = secrets_save_path
        st.sidebar.success("✅ تم حفظ ملف الاعتماد")
    yt_ready = bool(st.session_state.get("yt_client_secrets_path") and os.path.exists(st.session_state.get("yt_client_secrets_path", "")))
    yt_title_template = st.sidebar.text_input(
        "📝 قالب عنوان الفيديو (استخدم {animal})",
        value="{animal} - حقائق مذهلة لن تصدقها 🐾 #shorts #حيوانات",
        help="سيتم استبدال {animal} باسم الحيوان تلقائياً"
    )
    if not _YT_LIBS_OK:
        st.sidebar.warning("⚠️ مكتبات YouTube غير مثبتة: pip install google-auth google-auth-oauthlib google-api-python-client")
    if yt_ready:
        st.sidebar.success("🟢 YouTube متصل")
    else:
        st.sidebar.info("🔴 YouTube غير متصل — ارفع client_secrets.json")

    st.sidebar.markdown("---")

    # ── Manual mode ───────────────────────────────────────────────────────────
    if st.sidebar.button("1. توليد الحقائق والصور"):
        if animals:
            st.session_state.step = 'generating_images'
            st.session_state.image_data, st.session_state.facts_data = {}, {}
            st.rerun()

    if st.session_state.step == 'generating_images':
        log_placeholder = st.empty()
        logger = StreamlitLogger(log_placeholder)
        with st.spinner("جاري توليد الحقائق والصور... (انظر السجل أدناه)"), contextlib.redirect_stdout(logger):
            for animal in animals:
                print(f"\n--- بدء معالجة: {animal} ---")
                facts = fetch_facts_from_gemini_ar(animal, num_facts)
                if facts:
                    st.session_state.facts_data[animal] = facts
                    st.session_state.image_data[animal] = generate_images(facts, width=video_w, height=video_h)
            print("\n--- اكتملت مرحلة توليد الصور ---")
        st.session_state.step = 'reviewing_images'
        st.rerun()

    if st.session_state.step == 'reviewing_images':
        st.header("🖼️ مراجعة الصور وتعديلها")
        for animal, images_with_facts in st.session_state.image_data.items():
            with st.expander(f"الصور الخاصة بـ: {animal}", expanded=True):
                for i, (image_path, fact_text, original_prompt) in enumerate(images_with_facts):
                    c1, c2 = st.columns([3, 1])
                    c1.image(image_path, caption=f"صورة {i+1}: {fact_text}", use_column_width=True)
                    if c2.button(f"🔄 تجديد", key=f"regen_{animal}_{i}"):
                        download_image(original_prompt, image_path, width=video_w, height=video_h, seed=random.randint(1,99999))
                        st.rerun()
        if st.sidebar.button("✅ 2. إنشاء جميع الفيديوهات الآن!"):
            st.session_state.step = 'generating_videos'
            st.rerun()

    if st.session_state.step == 'generating_videos':
        log_placeholder = st.empty()
        logger = StreamlitLogger(log_placeholder)
        with st.spinner("جاري إنشاء جميع الفيديوهات... (انظر السجل أدناه)"), contextlib.redirect_stdout(logger):
            video_paths = []
            print("--- بدء عملية إنشاء الفيديوهات النهائية ---")
            for animal, images_with_facts in st.session_state.image_data.items():
                print(f"\n======================\n--- إنشاء الفيديو لـ: {animal} ---\n======================")
                output_video_file = os.path.join("videos", f"{num_facts} حقائق مذهلة عن {animal} لن تصدقها.mp4")
                print("--- [المرحلة 1/2] توليد الصوتيات ---")
                generate_audio_sync_ar(st.session_state.facts_data[animal], voice=selected_voice)
                print("\n--- [المرحلة 2/2] إنشاء الفيديو ---")
                success, final_path = create_video_from_data_ar(
                    images_with_facts, output_video_file, video_w, video_h,
                    show_text_on_video=show_text_on_video, animal_name=animal,
                    num_facts=num_facts, voice=selected_voice
                )
                if success and final_path:
                    video_paths.append(final_path)
                    _save_history_entry(animal, num_facts, final_path)
            if video_paths:
                zip_path = os.path.join("videos", "جميع_الفيديوهات.zip")
                with zipfile.ZipFile(zip_path, 'w') as zf:
                    for path in video_paths: zf.write(path, os.path.basename(path))
                st.success(f"🎉 تم إنشاء {len(video_paths)} فيديو بنجاح!")
                with open(zip_path, 'rb') as f:
                    st.download_button("📥 تحميل جميع الفيديوهات (ZIP)", f, file_name="جميع_الفيديوهات.zip")
            else:
                st.error("❌ لم يتم إنشاء أي فيديو.")
        st.session_state.step = 'initial'

    # ── Tabs: Automation | History ───────────────────────────────────────────
    st.markdown("---")
    tab_auto, tab_history = st.tabs(["🤖 التشغيل التلقائي", "📜 السجل التاريخي"])

    with tab_auto:
        st.caption("أضف قائمة الحيوانات في الشريط الجانبي، اختر الفاصل الزمني، ثم اضغط بدء.")

        auto_yt_upload = st.checkbox(
            "📤 رفع تلقائي إلى YouTube Shorts بعد كل فيديو",
            value=False,
            disabled=not yt_ready or not _YT_LIBS_OK,
            help="يتطلب توصيل حساب YouTube من الشريط الجانبي أولاً"
        )

        col1, col2 = st.columns(2)
        interval_hours   = col1.number_input("⏱ ساعات الانتظار بين الفيديوهات", min_value=0, max_value=23, value=7, step=1)
        interval_minutes = col2.number_input("⏱ دقائق إضافية", min_value=0, max_value=59, value=0, step=5)
        interval_seconds_total = int(interval_hours * 3600 + interval_minutes * 60)

        with _auto_lock:
            is_running   = _auto_status.get('running', False)
            current_idx  = _auto_status.get('current_index', 0)
            log_lines    = list(_auto_status.get('log', []))
            done_paths   = list(_auto_status.get('done_paths', []))
            auto_animals = _auto_status.get('animals', [])

        if not is_running:
            if st.button("▶️ بدء التشغيل التلقائي", type="primary", disabled=not animals):
                params = {
                    'num_facts':          num_facts,
                    'video_w':            video_w,
                    'video_h':            video_h,
                    'voice':              selected_voice,
                    'show_text':          show_text_on_video,
                    'auto_yt_upload':     auto_yt_upload,
                    'yt_title_template':  yt_title_template,
                }
                with _auto_lock:
                    _auto_status.clear()
                    _auto_status.update({
                        'running':       True,
                        'current_index': 0,
                        'log':           [f"🚀 بدأ التشغيل التلقائي لـ {len(animals)} حيوان — فاصل {interval_hours}h {interval_minutes}m بين كل فيديو."],
                        'animals':       list(animals),
                        'params':        params,
                        'done_paths':    [],
                    })
                t = _threading.Thread(
                    target=_run_automation,
                    args=(list(animals), interval_seconds_total, params),
                    daemon=True
                )
                t.start()
                st.rerun()
        else:
            animal_now = auto_animals[current_idx] if current_idx < len(auto_animals) else "..."
            st.info(f"⏳ جاري معالجة **{animal_now}** — الحيوان {current_idx+1} من {len(auto_animals)}")
            progress_val = current_idx / max(len(auto_animals), 1)
            st.progress(progress_val)
            if st.button("⏹️ إيقاف التشغيل التلقائي"):
                with _auto_lock:
                    _auto_status['running'] = False
                st.warning("⚠️ سيتوقف التشغيل بعد انتهاء الفيديو الحالي.")
            st.caption("🔄 يتم تحديث الصفحة كل 5 ثوانٍ تلقائياً...")
            time.sleep(5)
            st.rerun()

        if log_lines:
            st.subheader("📋 سجل التشغيل")
            for line in reversed(log_lines[-25:]):
                st.markdown(line)

        if done_paths:
            st.subheader("📥 الفيديوهات المكتملة في هذه الجلسة")
            for p in done_paths:
                if os.path.exists(p):
                    dcol, ucol = st.columns([3, 1])
                    with open(p, 'rb') as f:
                        dcol.download_button(
                            f"⬇️ {os.path.basename(p)}",
                            f,
                            file_name=os.path.basename(p),
                            key=f"dl_auto_{p}"
                        )
                    _basename = os.path.splitext(os.path.basename(p))[0]
                    _animal_guess = _basename.split("عن ")[-1].split(" لن")[0].strip() if "عن " in _basename else _basename
                    if ucol.button("📤 رفع YT", key=f"yt_{p}", disabled=not yt_ready or not _YT_LIBS_OK):
                        with st.spinner("جاري الرفع إلى YouTube Shorts..."):
                            ok, result = upload_to_youtube_shorts(p, _animal_guess, yt_title_template)
                        if ok:
                            st.success(f"✅ تم الرفع! https://youtube.com/shorts/{result}")
                            _save_history_entry(_animal_guess, 0, p, f"https://youtube.com/shorts/{result}")
                        else:
                            st.error(f"❌ فشل الرفع: {result}")

    with tab_history:
        st.header("📜 السجل التاريخي للفيديوهات")
        history = _load_history()
        if not history:
            st.info("لا يوجد سجل بعد — ستظهر الفيديوهات هنا بعد إنشائها.")
        else:
            # Summary stats
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("إجمالي الفيديوهات", len(history))
            sc2.metric("مرفوع على YouTube", sum(1 for e in history if e.get("yt_url")))
            total_mb = sum(e.get("size_mb", 0) for e in history)
            sc3.metric("الحجم الكلي", f"{total_mb:.1f} MB")

            st.markdown("---")

            # Search / filter
            search_q = st.text_input("🔍 بحث باسم الحيوان", "")
            filtered = [e for e in history if search_q.lower() in e.get("animal","").lower()] if search_q else history

            if st.button("🗑️ حذف كل السجل", type="secondary"):
                with open(_HISTORY_FILE, "w", encoding="utf-8") as _hf:
                    json.dump([], _hf)
                st.success("تم مسح السجل.")
                st.rerun()

            st.markdown("---")
            for idx, entry in enumerate(filtered):
                with st.expander(f"🎬 {entry.get('animal','؟')} — {entry.get('date','')}   ({entry.get('size_mb',0)} MB)", expanded=False):
                    col_a, col_b = st.columns([2,1])
                    col_a.write(f"**الحيوان:** {entry.get('animal','')}")
                    col_a.write(f"**عدد الحقائق:** {entry.get('num_facts','')}")
                    col_a.write(f"**التاريخ:** {entry.get('date','')}")
                    col_a.write(f"**الملف:** `{entry.get('filename','')}`")
                    if entry.get("yt_url"):
                        col_a.markdown(f"**YouTube:** [{entry['yt_url']}]({entry['yt_url']})")
                    else:
                        col_a.write("**YouTube:** لم يُرفع")

                    vpath = entry.get("video_path","")
                    if vpath and os.path.exists(vpath):
                        with open(vpath, "rb") as _vf:
                            col_b.download_button(
                                "⬇️ تحميل",
                                _vf,
                                file_name=entry.get("filename","video.mp4"),
                                key=f"hist_dl_{idx}"
                            )
                        if not entry.get("yt_url") and col_b.button("📤 رفع YT", key=f"hist_yt_{idx}", disabled=not yt_ready or not _YT_LIBS_OK):
                            with st.spinner("جاري الرفع..."):
                                ok, result = upload_to_youtube_shorts(vpath, entry.get("animal",""), yt_title_template)
                            if ok:
                                yt_link = f"https://youtube.com/shorts/{result}"
                                st.success(f"✅ {yt_link}")
                                # Update history entry with yt_url
                                all_hist = _load_history()
                                for h in all_hist:
                                    if h.get("video_path") == vpath:
                                        h["yt_url"] = yt_link
                                        break
                                with open(_HISTORY_FILE, "w", encoding="utf-8") as _hf:
                                    json.dump(all_hist, _hf, ensure_ascii=False, indent=2)
                                st.rerun()
                            else:
                                st.error(f"❌ {result}")
                    else:
                        col_b.caption("⚠️ الملف غير موجود")

                    if col_b.button("🗑️ حذف", key=f"hist_del_{idx}"):
                        _delete_history_entry(idx)
                        st.rerun()



if __name__ == '__main__':
    st.set_page_config(layout="wide", page_title="مولد الفيديو بالذكاء الاصطناعي")
    
    fix_imagemagick_policy()
    download_default_font(FONT_PATH)
    
    run_streamlit_app_ar()
