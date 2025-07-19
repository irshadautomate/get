from flask import Flask, request, jsonify
from moviepy.editor import *
from moviepy.video.fx.all import resize
from pydub import AudioSegment
import os, uuid, requests, together
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# 🔐 Load environment variables
from dotenv import load_dotenv
load_dotenv()

# ✅ API Keys
together.api_key = os.getenv("TOGETHER_API_KEY")
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")
VOICE_ID = os.getenv("VOICE_ID")
GOOGLE_SERVICE_ACCOUNT_FILE = "service_account.json"  # uploaded to Render

# ✅ Google Drive Auth
creds = service_account.Credentials.from_service_account_file(
    GOOGLE_SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/drive"]
)
drive_service = build('drive', 'v3', credentials=creds)

# ✅ Upload video to Google Drive
def upload_and_share(filepath):
    file_metadata = {'name': os.path.basename(filepath)}
    media = MediaFileUpload(filepath, resumable=True)
    uploaded = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    file_id = uploaded.get("id")
    drive_service.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone"}).execute()
    return f"https://drive.google.com/uc?id={file_id}"

# ✅ Generate Image
def generate_image(prompt, index):
    try:
        print(f"🎨 Generating image for: {prompt}")
        response = together.Image.create(
            prompt=prompt,
            model="black-forest-labs/FLUX.1-schnell-Free",
            num_images=1,
            size="768x768",
            steps=3
        )
        print("📦 Together Response:", response)
        if "output" in response and "images" in response["output"]:
            image_url = response["output"]["images"][0]
        elif "data" in response and isinstance(response["data"], list):
            image_url = response["data"][0]["url"]
        else:
            raise ValueError("Unexpected response from Together API")

        img_path = f"scene_{index}.png"
        with open(img_path, "wb") as f:
            f.write(requests.get(image_url).content)
        return img_path
    except Exception as e:
        print(f"❌ Image generation failed: {e}")
        return None

# ✅ Generate Voice
def generate_voice(text, index):
    try:
        print(f"🎤 Generating voice for: {text}")
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_multilingual_v2"}
        )
        if r.status_code == 200:
            path = f"voice_{index}.mp3"
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        else:
            print("❌ ElevenLabs Error:", r.text)
    except Exception as e:
        print(f"❌ Voice generation failed: {e}")
    return None

# ✅ Zoom effect
def apply_zoom_in(img_path, duration):
    clip = ImageClip(img_path, duration=duration).resize(height=720)
    return clip.fx(resize, lambda t: 1 + 0.05 * t)

# ✅ Flask App
app = Flask(__name__)

@app.route("/generate-video", methods=["POST"])
def generate_video():
    data = request.get_json()
    clips = data.get("clips", [])
    scene_clips = []

    for i, scene in enumerate(clips):
        img = generate_image(scene.get("image_prompt", ""), i)
        voice = generate_voice(scene.get("voiceText", ""), i)
        if not img or not voice:
            continue
        voice_clip = AudioFileClip(voice)
        image_clip = apply_zoom_in(img, voice_clip.duration)
        video_clip = image_clip.set_audio(voice_clip)
        scene_clips.append(video_clip)

    if not scene_clips:
        return jsonify({"error": "No valid clips generated"}), 400

    final = concatenate_videoclips(scene_clips, method="compose")

    # ✅ Background music
    if os.path.exists("background.mp3"):
        print("🎵 Mixing background music...")
        duration_ms = int(final.duration * 1000)
        bg_audio = AudioSegment.from_file("background.mp3")
        bg_audio = (bg_audio * ((duration_ms // len(bg_audio)) + 1))[:duration_ms]

        voice_audio = AudioSegment.empty()
        for i in range(len(scene_clips)):
            voice_audio += AudioSegment.from_file(f"voice_{i}.mp3")

        mixed = voice_audio.overlay(bg_audio - 5)
        mixed_path = "mixed_audio.mp3"
        mixed.export(mixed_path, format="mp3")
        final = final.set_audio(AudioFileClip(mixed_path))

    out_path = f"final_{uuid.uuid4().hex[:6]}.mp4"
    final.write_videofile(out_path, codec="libx264", fps=24)

    video_url = upload_and_share(out_path)
    return jsonify({"video_url": video_url})
