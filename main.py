import asyncio, shutil, subprocess, uuid
from pathlib import Path
from typing import Optional
import edge_tts
from gradio_client import Client
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
HF_SPACE = "Lightricks/ltx-video-distilled"

app = FastAPI(title="Free AI Cartoon Video Maker API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/outputs", StaticFiles(directory=str(OUT)), name="outputs")

class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=3000)
    duration_seconds: int = Field(default=5, ge=4, le=8)
    aspect_ratio: str = "9:16"
    voice_language: str = "hi"
    add_music: bool = True

class GenerateResponse(BaseModel):
    job_id: str
    status: str
    message: str
    video_url: Optional[str] = None

def ffmpeg_path():
    return shutil.which("ffmpeg")

def make_video(prompt: str, ratio: str, folder: Path, seconds: int) -> Path:
    height, width = (896, 512) if ratio == "9:16" else (512, 896)
    try:
        client = Client(HF_SPACE, verbose=False)
        result = client.predict(
            prompt, "blurry, distorted, watermark, jittery motion",
            None, None, height, width, "text-to-video", float(seconds),
            9, 42, True, 3.0, False, api_name="/text_to_video"
        )
    except Exception as exc:
        raise HTTPException(status_code=502,
            detail="Free video model busy/unavailable or API changed. Try later. " + str(exc)[:180])
    item = result[0] if isinstance(result, (list, tuple)) else result
    source = item.get("path") or item.get("url") if isinstance(item, dict) else str(item or "")
    target = folder / "scene.mp4"
    if source and Path(source).is_file():
        shutil.copyfile(source, target)
    elif source.startswith(("http://", "https://")):
        import httpx
        response = httpx.get(source, timeout=180)
        response.raise_for_status()
        target.write_bytes(response.content)
    else:
        raise HTTPException(status_code=502, detail="Model returned no accessible video file.")
    return target

async def make_voice(text: str, output: Path, language: str):
    voice = "hi-IN-SwaraNeural" if language.lower().startswith("hi") else "en-US-AriaNeural"
    await edge_tts.Communicate(text, voice).save(str(output))

def make_music(output: Path, seconds: int):
    fade = max(0, seconds - 1)
    subprocess.run([ffmpeg_path(), "-y", "-f", "lavfi", "-i",
        f"sine=frequency=220:duration={seconds}", "-filter:a",
        f"volume=0.025,afade=t=in:st=0:d=1,afade=t=out:st={fade}:d=1",
        "-ac", "2", str(output)], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

@app.get("/")
def home():
    return {"app": "Free AI Cartoon Video Maker", "status": "backend running"}

@app.get("/health")
def health():
    return {"status": "ok", "ffmpeg_available": bool(ffmpeg_path()),
            "video_provider": "Hugging Face public Space; shared free GPU limits apply"}

@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    if not ffmpeg_path():
        raise HTTPException(status_code=500, detail="FFmpeg is not installed.")
    if req.aspect_ratio not in ("9:16", "16:9"):
        raise HTTPException(status_code=400, detail="Choose 9:16 or 16:9.")
    job_id = uuid.uuid4().hex[:12]
    folder = OUT / job_id
    folder.mkdir(parents=True, exist_ok=True)
    seconds = min(req.duration_seconds, 8)
    scene = await asyncio.to_thread(make_video, req.prompt, req.aspect_ratio, folder, seconds)
    voice = folder / "voice.mp3"
    await make_voice(req.prompt, voice, req.voice_language)
    final = folder / "final.mp4"
    if req.add_music:
        music = folder / "music.wav"
        make_music(music, seconds)
        cmd = [ffmpeg_path(), "-y", "-i", str(scene), "-i", str(voice), "-i", str(music),
               "-filter_complex", "[1:a]volume=1[v];[2:a]volume=0.16[m];[v][m]amix=inputs=2:duration=first[a]",
               "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(final)]
    else:
        cmd = [ffmpeg_path(), "-y", "-i", str(scene), "-i", str(voice),
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(final)]
    try:
        await asyncio.to_thread(subprocess.run, cmd, check=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail="Audio/video merge failed: " +
                            exc.stderr.decode(errors="ignore")[-350:])
    return GenerateResponse(job_id=job_id, status="completed",
        message=f"{seconds}-second video ready. Free model limits apply.",
        video_url=f"/outputs/{job_id}/final.mp4")
