import asyncio
import json
import os
import re
import sqlite3
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="YT-DLP Web UI")

DB_PATH = str(BASE_DIR / "downloads.db")
DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads")


# ─── Database ────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id TEXT PRIMARY KEY,
            url TEXT,
            title TEXT,
            format TEXT,
            quality TEXT,
            output_dir TEXT,
            status TEXT,
            filesize TEXT,
            duration TEXT,
            thumbnail TEXT,
            created_at TEXT
        )
    """)
    con.commit()
    con.close()

init_db()

def db_insert(row: dict):
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        INSERT OR REPLACE INTO history
        (id, url, title, format, quality, output_dir, status, filesize, duration, thumbnail, created_at)
        VALUES (:id, :url, :title, :format, :quality, :output_dir, :status, :filesize, :duration, :thumbnail, :created_at)
    """, row)
    con.commit()
    con.close()

def db_update_status(id: str, status: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE history SET status=? WHERE id=?", (status, id))
    con.commit()
    con.close()

def db_get_history():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM history ORDER BY created_at DESC LIMIT 50").fetchall()
    con.close()
    return [dict(r) for r in rows]

def db_delete(id: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM history WHERE id=?", (id,))
    con.commit()
    con.close()


# ─── Models ──────────────────────────────────────────────────────────────────

class VideoInfoRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    format: str = "video"       # "video" | "audio"
    quality: str = "best"       # "best" | "1080" | "720" | "480" | "360"
    output_dir: str = DEFAULT_DOWNLOAD_DIR
    download_id: Optional[str] = None


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(BASE_DIR / "templates" / "index.html") as f:
        return f.read()

@app.get("/api/default-dir")
async def get_default_dir():
    return {"dir": DEFAULT_DOWNLOAD_DIR}

@app.post("/api/info")
async def get_video_info(req: VideoInfoRequest):
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-playlist", req.url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return JSONResponse({"error": result.stderr or "Failed to fetch info"}, status_code=400)

        info = json.loads(result.stdout)
        formats = []
        seen = set()
        for f in info.get("formats", []):
            h = f.get("height")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            if h and vcodec != "none" and h not in seen:
                seen.add(h)
                formats.append({
                    "label": f"{h}p",
                    "value": str(h),
                    "fps": f.get("fps"),
                    "ext": f.get("ext")
                })
        formats.sort(key=lambda x: int(x["value"]), reverse=True)

        duration_s = info.get("duration", 0)
        duration = f"{int(duration_s//60)}:{int(duration_s%60):02d}" if duration_s else "N/A"

        return {
            "title": info.get("title", "Unknown"),
            "thumbnail": info.get("thumbnail", ""),
            "uploader": info.get("uploader", ""),
            "duration": duration,
            "view_count": info.get("view_count", 0),
            "like_count": info.get("like_count", 0),
            "description": (info.get("description") or "")[:300],
            "formats": formats,
            "upload_date": info.get("upload_date", ""),
            "webpage_url": info.get("webpage_url", req.url),
        }
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Request timed out"}, status_code=408)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/history")
async def get_history():
    return db_get_history()

@app.delete("/api/history/{id}")
async def delete_history(id: str):
    db_delete(id)
    return {"ok": True}


# ─── WebSocket Download ───────────────────────────────────────────────────────

@app.websocket("/ws/download")
async def ws_download(ws: WebSocket):
    await ws.accept()

    try:
        data = await ws.receive_json()
        req = DownloadRequest(**data)
        download_id = req.download_id or str(uuid.uuid4())

        output_dir = req.output_dir.strip() or DEFAULT_DOWNLOAD_DIR
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Build yt-dlp command
        cmd = ["yt-dlp", "--newline", "--progress", "--no-playlist"]

        if req.format == "audio":
            cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            if req.quality == "best":
                cmd += ["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"]
            else:
                cmd += ["-f", f"bestvideo[height<={req.quality}][ext=mp4]+bestaudio[ext=m4a]/best[height<={req.quality}]"]

        template = os.path.join(output_dir, "%(title)s.%(ext)s")
        cmd += ["-o", template, req.url]

        await ws.send_json({"type": "start", "id": download_id, "cmd": " ".join(cmd)})

        # Insert to DB as "downloading"
        db_insert({
            "id": download_id,
            "url": req.url,
            "title": "Fetching...",
            "format": req.format,
            "quality": req.quality,
            "output_dir": output_dir,
            "status": "downloading",
            "filesize": "",
            "duration": "",
            "thumbnail": "",
            "created_at": datetime.now().isoformat()
        })

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )

        title = None
        percent = 0.0
        speed = ""
        eta = ""
        filesize = ""

        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()

            # Parse progress line
            m_pct = re.search(r"(\d+\.?\d*)%", line)
            m_speed = re.search(r"at\s+([\d.]+\w+/s)", line)
            m_eta = re.search(r"ETA\s+([\d:]+)", line)
            m_size = re.search(r"of\s+~?([\d.]+\w+)", line)
            m_title = re.search(r'\[download\] Destination: .+?/(.+?)$', line)
            m_title2 = re.search(r'\[ffmpeg\].*?(.+?\.\w+)$', line)

            if m_pct:
                percent = float(m_pct.group(1))
            if m_speed:
                speed = m_speed.group(1)
            if m_eta:
                eta = m_eta.group(1)
            if m_size:
                filesize = m_size.group(1)
            if m_title and not title:
                title = m_title.group(1)
            if m_title2 and not title:
                title = m_title2.group(1)

            await ws.send_json({
                "type": "progress",
                "percent": percent,
                "speed": speed,
                "eta": eta,
                "filesize": filesize,
                "line": line
            })

        await proc.wait()
        success = proc.returncode == 0

        final_status = "done" if success else "error"
        db_update_status(download_id, final_status)

        # Update title if captured
        if title:
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE history SET title=?, filesize=? WHERE id=?", (title, filesize, download_id))
            con.commit()
            con.close()

        await ws.send_json({
            "type": "done" if success else "error",
            "message": "Download complete!" if success else "Download failed.",
            "title": title or req.url
        })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except:
            pass
