# YT-DLP Web UI

Web interface lokal untuk yt-dlp — jalanin di Debian, akses via browser.

## Instalasi

```bash
cd ytdlp-web
pip install -r requirements.txt --break-system-packages
```

## Jalankan

```bash
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Buka browser → **http://localhost:8080**

## Atau pakai script ini

```bash
chmod +x run.sh
./run.sh
```

## Struktur
```
ytdlp-web/
├── main.py          # FastAPI backend
├── requirements.txt
├── run.sh
├── downloads.db     # SQLite history (auto-dibuat)
└── templates/
    └── index.html   # Frontend
```

## Fitur
- ✅ Fetch video info + thumbnail preview
- ✅ Pilih format (video/audio MP3)
- ✅ Pilih kualitas (best/1080p/720p/...)
- ✅ Live progress bar via WebSocket
- ✅ Log terminal output
- ✅ Riwayat download (SQLite)
- ✅ Custom output folder
