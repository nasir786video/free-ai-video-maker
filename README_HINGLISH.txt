FREE AI CARTOON VIDEO MAKER - BACKEND

Is package mein:
- FastAPI backend
- External AI video API connector
- Hindi text-to-speech voice (edge-tts)
- Original simple background audio tone
- FFmpeg se voice/music/video merge
- MP4 output

IMPORTANT:
1) Ye backend code hai; GitHub Pages akela video model nahi chala sakta.
2) Real AI video ke liye ek video-generation provider ka API URL aur API key server par set karna zaroori hai.
3) Har provider ka request format alag hota hai. app/main.py ke request_video() function ko us provider ke official format ke mutabiq set karna hoga.
4) Free, unlimited hosted video generation ki guarantee nahi hai. 480p/low-resolution option provider support par depend karega.
5) API key ko kabhi index.html, GitHub public repo, ya chat mein mat daalna.

Run locally:
  pip install -r requirements.txt
  uvicorn app.main:app --reload

Health check:
  http://127.0.0.1:8000/health

Generate endpoint:
  POST http://127.0.0.1:8000/generate
  JSON:
  {
    "prompt": "A little rabbit helps a lost bird find its home.",
    "duration_seconds": 8,
    "aspect_ratio": "9:16",
    "voice_language": "hi",
    "add_music": true
  }

Provider API contract expected by current adapter:
  POST VIDEO_API_URL
  Header: Authorization: Bearer VIDEO_API_KEY
  JSON: {"prompt":"...", "duration":8, "aspect_ratio":"9:16"}
  Response JSON: {"video_url":"https://.../video.mp4"}
  OR direct MP4 response.

Next required engineering step:
- Select a real video provider and adapt request_video() to its documented API.
- Deploy this backend to a server with FFmpeg.
- Then connect the existing GitHub Pages frontend to this backend URL.
