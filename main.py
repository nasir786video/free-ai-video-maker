
import os
import uuid
import asyncio
import subprocess
import shutil
from pathlib import Path
from typing import Optional

import httpx
import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

MODEL = "fal-ai/ltx-video-13b-distilled"

app = FastAPI(title="Free AI Cartoon Video Maker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.mount("/outputs", StaticFiles(directory=str(OUT)), name="outputs")


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=3000)
    duration_seconds: int = Field(default=5, ge=4, le=5)
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


async def request_video(prompt: str, ratio: str, folder: Path) -> Path:
    key = os.getenv("FAL_KEY", "").strip()

    if not key:
        raise HTTPException(
            status_code=503,
            detail="FAL_KEY is not configured on Render."
        )

    headers = {
        "Authorization": f"Key {key}",
        "Content-Type": "application/json"
    }

    payload = {
        "prompt": prompt,
        "resolution": "480p",
        "aspect_ratio": ratio,
        "num_frames": 121,
        "frame_rate": 24,
        "enable_prompt_expansion": True,
        "enable_safety_checker": True,
        "enable_detail_pass": False
    }

    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"https://queue.fal.run/{MODEL}",
            headers=headers,
            json=payload
        )

        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Video request failed: {response.text[:400]}"
            )

        job = response.json()
        status_url = job.get("status_url")
        result_url = job.get("response_url")

        if not status_url or not result_url:
            raise HTTPException(
                status_code=502,
                detail="Video service did not return queue links."
            )

        completed = False

        for _ in range(240):
            await asyncio.sleep(2)

            check = await client.get(
                status_url,
                headers=headers
            )
            check.raise_for_status()
            state = check.json()

            if state.get("status") == "COMPLETED":
                if state.get("error"):
                    raise HTTPException(
                        status_code=502,
                        detail=f"Video generation failed: {state['error']}"
                    )
                completed = True
                break

        if not completed:
            raise HTTPException(
                status_code=504,
                detail="Video generation is taking too long. Try again later."
            )

        result = await client.get(
            result_url,
            headers=headers
        )
        result.raise_for_status()

        video_url = result.json().get("video", {}).get("url")

        if not video_url:
            raise HTTPException(
                status_code=502,
                detail="Video URL was not returned."
            )

        download = await client.get(video_url)
        download.raise_for_status()

        target = folder / "scene.mp4"
        target.write_bytes(download.content)
        return target


async def make_voice(text: str, output: Path, language: str):
    voice = (
        "hi-IN-SwaraNeural"
        if language.lower().startswith("hi")
        else "en-US-AriaNeural"
    )
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output))


def make_music(output: Path, seconds: int):
    fade_start = max(0, seconds - 1)

    cmd = [
        ffmpeg_path(), "-y",
        "-f", "lavfi", "-i",
        f"sine=frequency=220:duration={seconds}",
        "-filter:a",
        f"volume=0.035,afade=t=in:st=0:d=1,afade=t=out:st={fade_start}:d=1",
        "-ac", "2",
        str(output)
    ]

    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )


@app.get("/")
def home():
    return {
        "app": "Free AI Cartoon Video Maker",
        "status": "backend running"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ffmpeg_available": bool(ffmpeg_path()),
        "video_api_configured": bool(os.getenv("FAL_KEY"))
    }


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    if not ffmpeg_path():
        raise HTTPException(
            status_code=500,
            detail="FFmpeg is not installed."
        )

    if req.aspect_ratio not in ("9:16", "16:9"):
        raise HTTPException(
            status_code=400,
            detail="Choose 9:16 or 16:9."
        )

    job_id = uuid.uuid4().hex[:12]
    folder = OUT / job_id
    folder.mkdir(parents=True, exist_ok=True)

    scene = await request_video(
        req.prompt,
        req.aspect_ratio,
        folder
    )

    voice = folder / "voice.mp3"
    await make_voice(
        req.prompt,
        voice,
        req.voice_language
    )

    final_video = folder / "final.mp4"

    if req.add_music:
        music = folder / "music.wav"
        make_music(music, 5)

        cmd = [
            ffmpeg_path(), "-y",
            "-i", str(scene),
            "-i", str(voice),
            "-i", str(music),
            "-filter_complex",
            "[1:a]volume=1.0[v];[2:a]volume=0.18[m];[v][m]amix=inputs=2:duration=first[a]",
            "-map", "0:v:0",
            "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(final_video)
        ]
    else:
        cmd = [
            ffmpeg_path(), "-y",
            "-i", str(scene),
            "-i", str(voice),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(final_video)
        ]

    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )

    return GenerateResponse(
        job_id=job_id,
        status="completed",
        message="5-second video generated.",
        video_url=f"/outputs/{job_id}/final.mp4"
    )
