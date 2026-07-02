# 🎵 VuTune — IMVU Music Bot

A full-featured IMVU music bot that joins your rooms, listens to chat commands, and plays music from YouTube — with a beautiful web dashboard to control everything.

---

## Features

| Feature | Details |
|---|---|
| **All languages** | Any song in any language — it's just a YouTube search |
| **Full command set** | !play, !add, !next, !prev, !stop, !playlist, !remove, !clear, and more |
| **Auto-queue** | Songs play automatically one after another |
| **Web Dashboard** | Control rooms, queue, and music from your browser |
| **Multi-room** | Bot can be in multiple rooms at once |
| **Ban system** | !mban / !munban to control who uses the bot |
| **AI Playlist** | !fillplaylist to auto-generate playlists |

---

## Quick Start

### Step 1 — Install Prerequisites

1. **Python 3.10+** → [python.org](https://python.org)
2. **ffmpeg** → [ffmpeg.org/download](https://ffmpeg.org/download.html)
   - Download the Windows build
   - Extract and add the `bin` folder to your system PATH
3. **Icecast for Windows** → [icecast.org/download](https://icecast.org/download/)
   - Install it (default settings)

### Step 2 — Run Setup

Double-click `setup.bat` — it will install all Python packages.

### Step 3 — Start Icecast

1. Open Icecast (installed in Step 1)
2. Use `icecast\icecast.xml` as the config file
3. Start the Icecast server

### Step 4 — Start VuTune

Double-click `start.bat`

You'll see:
```
╔══════════════════════════════════════════╗
║           🎵  VuTune  v1.0               ║
╠══════════════════════════════════════════╣
║  Dashboard  →  http://localhost:5000      ║
║  Stream URL →  http://localhost:8000/stream ║
╚══════════════════════════════════════════╝
```

### Step 5 — Set Up Your IMVU Room

1. Copy the Stream URL: `http://YOUR_IP:8000/stream`
   - Find your local IP: open Command Prompt → type `ipconfig`
   - Use the IPv4 address e.g. `http://192.168.1.5:8000/stream`
2. In IMVU, go to your room settings → Media/Radio
3. Paste the URL and save
4. Done! 🎉

### Step 6 — Add Rooms via Dashboard

1. Open your browser → `http://localhost:5000`
2. Click **+ Add Room** in the Rooms panel
3. Enter your IMVU Room ID (find it in the room's URL)
4. VuTune will join automatically!

---

## Commands

| Command | Description |
|---|---|
| `!play [song]` | Search YouTube and play immediately |
| `!add [song]` | Add to playlist/queue |
| `!next` / `!skip` | Skip to next song |
| `!prev` | Play previous song |
| `!stop` | Stop the radio |
| `!remove [song or #]` | Remove a song from queue |
| `!clear` | Clear the entire queue |
| `!playlist` | Show next 10 songs |
| `!playing` | Show now playing |
| `!listeners` | View listener count |
| `!radio` | Get the stream URL for rooms |
| `!install` | Setup guide for your room |
| `!web` | Get the dashboard URL |
| `!fillplaylist [genre]` | AI-generate a playlist |
| `!showwhoadded` | Toggle who-added display |
| `!mhere` | Confirm bot is in room |
| `!silent` | Toggle auto-messages |
| `!mban [user]` | Ban user from bot (admin) |
| `!munban [user]` | Unban user (admin) |
| `!help` | List all commands |

---

## Configuration (`config.json`)

```json
{
  "imvu": {
    "username": "VuTune",
    "password": "VuTune@1908!"
  },
  "anyone_can_use": true,
  "allowed_users": ["YourUsername"],
  "rooms": []
}
```

- Set `anyone_can_use` to `false` if you want only specific users to control music
- Add usernames to `allowed_users` for admin access

---

## Project Structure

```
VuTune/
├── main.py              ← Entry point
├── config.json          ← Your settings
├── start.bat            ← Start the bot (Windows)
├── setup.bat            ← First-time setup
├── bot/
│   ├── imvu_client.py   ← IMVU API (login, chat, rooms)
│   ├── player.py        ← YouTube → ffmpeg → Icecast
│   ├── queue_manager.py ← Song queue
│   ├── command_handler.py ← All ! commands
│   └── room_manager.py  ← Multi-room management
├── dashboard/
│   ├── app.py           ← Flask REST API
│   ├── templates/index.html ← Dashboard UI
│   └── static/          ← CSS + JS
├── icecast/
│   └── icecast.xml      ← Icecast server config
└── utils/
    └── config.py        ← Config loader/saver
```

---

## Notes

- The bot uses IMVU's unofficial HTTP API — keep VuTune's account in good standing
- For 24/7 hosting, consider a cheap VPS (DigitalOcean, Vultr, etc.)
- The dashboard is only accessible on your local network by default
