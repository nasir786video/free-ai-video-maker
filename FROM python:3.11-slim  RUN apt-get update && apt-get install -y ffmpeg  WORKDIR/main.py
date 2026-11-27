import os, uuid, asyncio, subprocess, tempfile, shutil
from pathlib import Path
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

app = FastAPI(title="Free AI Cartoon Video Maker API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"]
)
app.mount("/outputs", StaticFiles(directory=str(OUT)), name="outputs")

class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=3000)
    duration_seconds: int = Field(default=8, ge=4, le=10)
    aspect_ratio: str = Field(default="9:16")
    voice_language: str = "hi"
    add_music: bool = True

class GenerateResponse(BaseModel):
    job_id: str
    status: str
    message: str
    video_url: Optional[str] = None

def ffmpeg_path():
    return shutil.which("ffmpeg")

async def request_video(prompt: str, duration: int, aspect_ratio: str, folder: Path) -> Path:
    """
    Connects to a video-generation API that accepts JSON:
      POST VIDEO_API_URL
      Authorization: Bearer VIDEO_API_KEY
      body: { "prompt": "...", "duration": 8, "aspect_ratio": "9:16" }
    The API must return either:
      {"video_url":"https://...mp4"}
    or a direct MP4 response.
    Configure the provider's adapter if its API uses a different format.
    """
    api_url = os.getenv("VIDEO_API_URL", "").strip()
    api_key = os.getenv("VIDEO_API_KEY", "").strip()
    if not api_url or not api_key:
        raise HTTPException(
            status_code=503,
            detail="Video API is not connected yet. VIDEO_API_URL and VIDEO_API_KEY are required on the server."
        )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "duration": duration, "aspect_ratio": aspect_ratio}
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(api_url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Video provider error: {response.text[:500]}")
        ctype = response.headers.get("content-type", "")
        target = folder / "scene.mp4"
        if "video/" in ctype or response.content[:4] == b"\x00\x00\x00\x18":
            target.write_bytes(response.content)
            return target
        data = response.json()
        video_url = data.get("video_url") or data.get("output") or data.get("url")
        if isinstance(video_url, list):
            video_url = video_url[0] if video_url else None
        if not isinstance(video_url, str) or not video_url.startswith("http"):
            raise HTTPException(status_code=502, detail="Provider response must contain a video_url.")
        download = await client.get(video_url, timeout=600)
        download.raise_for_status()
        target.write_bytes(download.content)
        return target

async def make_voice(text: str, output: Path, language: str):
    try:
        import edge_tts
    except ImportError:
        raise HTTPException(status_code=500, detail="Hindi voice package is missing on the server.")
    voice = "hi-IN-SwaraNeural" if language.lower().startswith("hi") else "en-US-AriaNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output))

def make_music(output: Path, seconds: int):
    # Original gentle instrumental-style background tone; no copyrighted track.
    # Generated locally and mixed quietly under narration.
    cmd = [
        ffmpeg_path(), "-y", "-f", "lavfi", "-i",
        f"sine=frequency=220:duration={seconds}",
        "-filter:a", "volume=0.035,afade=t=in:st=0:d=1,afade=t=out:st={max(0, seconds-1)}:d=1",
        "-ac", "2", str(output)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

@app.get("/")
def home():
    return {"app": "Free AI Cartoon Video Maker", "status": "backend running"}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "ffmpeg_available": bool(ffmpeg_path()),
        "video_api_configured": bool(os.getenv("VIDEO_API_URL") and os.getenv("VIDEO_API_KEY"))
    }

@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    if not ffmpeg_path():
        raise HTTPException(status_code=500, detail="FFmpeg is not installed on the server.")
    job_id = uuid.uuid4().hex[:12]
    folder = OUT / job_id
    folder.mkdir(parents=True, exist_ok=True)
    scene = await request_video(req.prompt, req.duration_seconds, req.aspect_ratio, folder)

    voice = folder / "voice.mp3"
    await make_voice(req.prompt, voice, req.voice_language)

    final_video = folder / "final.mp4"
    if req.add_music:
        music = folder / "music.wav"
        make_music(music, req.duration_seconds)
        cmd = [
            ffmpeg_path(), "-y", "-i", str(scene), "-i", str(voice), "-i", str(music),
            "-filter_complex",
            "[1:a]volume=1.0[voice];[2:a]volume=0.18[music];[voice][music]amix=inputs=2:duration=first:dropout_transition=2[a]",
            "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
            "-shortest", str(final_video)
        ]
    else:
        cmd = [
            ffmpeg_path(), "-y", "-i", str(scene), "-i", str(voice),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
            "-shortest", str(final_video)
        ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return GenerateResponse(
        job_id=job_id, status="completed", message="Video generated.",
        video_url=f"/outputs/{job_id}/final.mp4"
    )
