# server.py — w4xx Web Portal Backend Server
# FastAPI + Discord Voice Engine + Soundboard Vault + Real-time REST API

import os
import sys
import re
import json
import asyncio
import threading
import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import aiohttp
import discord
try:
    from discord.ext.native_voice import VoiceClient as NativeVoiceClient
except ImportError:
    NativeVoiceClient = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOADS_DIR, exist_ok=True)

if sys.platform != "win32":
    FFMPEG_PATH = "ffmpeg"
else:
    FFMPEG_PATH = os.path.join(BASE_DIR, 'ffmpeg.exe')
    if not os.path.exists(FFMPEG_PATH):
        doc_ffmpeg = r"C:\Users\Welcome\Documents\w4xx_project\ffmpeg.exe"
        if os.path.exists(doc_ffmpeg):
            FFMPEG_PATH = doc_ffmpeg
        else:
            FFMPEG_PATH = "ffmpeg"

SAVED_TOKENS_FILE = os.path.join(BASE_DIR, "saved_tokens.txt")
SAVED_AUDIOS_FILE = os.path.join(BASE_DIR, "saved_audios.json")

if sys.platform == "win32":
    win_tokens = r"C:\Users\Welcome\Desktop\saved_tokens.txt"
    win_audios = r"C:\Users\Welcome\Desktop\saved_audios.json"
    if os.path.exists(win_tokens):
        SAVED_TOKENS_FILE = win_tokens
    if os.path.exists(win_audios):
        SAVED_AUDIOS_FILE = win_audios

# ==============================================================================
# STATE & DISCORD MANAGER
# ==============================================================================

class PortalEngine:
    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.bot_thread: Optional[threading.Thread] = None
        self.active_clients: List[discord.Client] = []
        self.connected_accounts: dict = {}  # token -> client
        self.guild_cache: dict = {}         # display_key -> guilds
        self.current_vc_id: Optional[int] = None
        self.current_playing_track: Optional[str] = None
        self.global_volume: float = 1.8
        self.bass_boost_level: int = 6
        self.logs: List[dict] = []
        self._max_logs = 150

    def log(self, message: str, level: str = "info"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        entry = {"time": timestamp, "message": message, "level": level}
        self.logs.append(entry)
        if len(self.logs) > self._max_logs:
            self.logs.pop(0)
        print(f"[{timestamp}] {message}")

    def start_background_loop(self):
        def _run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        self.bot_thread = threading.Thread(target=_run, daemon=True)
        self.bot_thread.start()
        self.log("🚀 Discord Automation Background Loop activated.")

    def run_coro(self, coro):
        if self.loop and self.loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self.loop)
        return None

engine = PortalEngine()
engine.start_background_loop()

# ==============================================================================
# DISCORD TOKEN CLIENT
# ==============================================================================

class WebTokenClient(discord.Client):
    def __init__(self, token_str: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._token_str = token_str

    async def on_ready(self):
        username = str(self.user)
        user_id = self.user.id
        engine.log(f"✅ Node online & authorized: [{username}] (ID: {user_id})")

        guilds_data = []
        for guild in self.guilds:
            channels_data = []
            for ch in guild.channels:
                ch_type = 'voice' if isinstance(ch, discord.VoiceChannel) else ('text' if isinstance(ch, discord.TextChannel) else 'other')
                channels_data.append({
                    'name': ch.name,
                    'id': str(ch.id),
                    'type': ch_type,
                    'user_limit': getattr(ch, 'user_limit', 0) if ch_type == 'voice' else 0,
                    'members_count': len(ch.members) if ch_type == 'voice' else 0
                })
            guilds_data.append({
                'name': guild.name,
                'id': str(guild.id),
                'icon': str(guild.icon.url) if guild.icon else None,
                'member_count': getattr(guild, 'member_count', len(guild.members)),
                'channels': channels_data
            })

        display_key = f"{username} ({user_id})"
        engine.guild_cache[display_key] = guilds_data
        engine.connected_accounts[self._token_str] = self
        if self not in engine.active_clients:
            engine.active_clients.append(self)
        engine.log(f"Synced {len(guilds_data)} servers for node [{username}].")

async def _connect_token_coro(token: str):
    token = token.strip()
    if not token or token in engine.connected_accounts:
        return
    client = WebTokenClient(token_str=token)
    try:
        engine.log(f"Connecting Discord Node with token ({token[:12]}...)...")
        await client.start(token)
    except Exception as e:
        engine.log(f"❌ Connection fault on token ({token[:12]}...): {str(e)[:70]}", "error")

async def _disconnect_token_coro(token: str):
    if token in engine.connected_accounts:
        client = engine.connected_accounts[token]
        await client.close()
        if client in engine.active_clients:
            engine.active_clients.remove(client)
        del engine.connected_accounts[token]
        engine.log(f"Disconnected node with token ({token[:12]}...).")

async def _connect_vc_coro(channel_id: int):
    connected_count = 0
    engine.current_vc_id = None
    last_error = None

    if not engine.active_clients:
        engine.log("❌ No active accounts connected. Please add/connect a token first.", "error")
        return

    for client in engine.active_clients:
        if not client.is_ready():
            continue
        ch = client.get_channel(channel_id)
        if not ch:
            try:
                ch = await client.fetch_channel(channel_id)
            except discord.NotFound:
                last_error = f"Channel ID {channel_id} not found (Account [{client.user.name}] is NOT in that server)!"
                engine.log(f"❌ {last_error}", "error")
                continue
            except discord.Forbidden:
                last_error = f"Permission Denied: Account [{client.user.name}] cannot access channel {channel_id}!"
                engine.log(f"❌ {last_error}", "error")
                continue
            except Exception as ex:
                last_error = f"Error fetching channel: {ex}"
                engine.log(f"❌ {last_error}", "error")
                continue

        if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            for vc in list(client.voice_clients):
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
            try:
                if NativeVoiceClient:
                    vc = await ch.connect(cls=NativeVoiceClient, timeout=25.0, reconnect=True)
                else:
                    vc = await ch.connect(timeout=25.0, reconnect=True)
                connected_count += 1
                engine.current_vc_id = channel_id
                engine.log(f"✅ Node [{client.user.name}] successfully docked in VC '{ch.name}' in server '{ch.guild.name}' (DAVE E2EE Active)!")
            except Exception as e:
                last_error = str(e)
                engine.log(f"❌ Connection fault on [{client.user.name}] in '{ch.name}': {str(e)[:70]}", "error")
        else:
            last_error = f"ID {channel_id} is a {type(ch).__name__}, NOT a Voice Channel!"
            engine.log(f"⚠️ {last_error}", "warn")

    if connected_count > 0:
        engine.log(f"🎉 VC Cluster Docking complete: Successfully connected to VC {channel_id}!")
    else:
        if not last_error:
            last_error = f"Voice Channel {channel_id} not found in any of your 42 servers. Check if you joined that server first!"
        engine.log(f"❌ Failed to join VC: {last_error}", "error")

async def _leave_vc_coro():
    left_any = False
    for client in engine.active_clients:
        for vc in list(client.voice_clients):
            if vc.is_connected():
                await vc.disconnect()
                left_any = True
    engine.current_vc_id = None
    engine.current_playing_track = None
    engine.log("🔌 Disconnected all account nodes from voice channels." if left_any else "No active voice channels connected.")

async def _play_audio_coro(audio_file: str):
    if not engine.active_clients:
        engine.log("⚠️ No active discord nodes connected. Please add and connect a token first.", "warn")
        return

    # Build audio filters
    gain = engine.global_volume
    filters = []
    if gain != 1.0:
        filters.append(f"volume={gain}")
    if engine.bass_boost_level > 0:
        filters.append(f"bass=g={engine.bass_boost_level}")

    opts = f'-af "{",".join(filters)}"' if filters else None
    resolved_path = os.path.normpath(audio_file)

    if not os.path.exists(resolved_path):
        engine.log(f"❌ Audio file not found on disk: '{resolved_path}'", "error")
        return

    track_name = os.path.basename(resolved_path)
    engine.current_playing_track = track_name
    engine.log(f"▶ Initiating stream pipeline for '{track_name}' (Volume: {gain*100:.0f}%, Bass: +{engine.bass_boost_level}dB)...")

    stream_count = 0
    for client in engine.active_clients:
        for vc in client.voice_clients:
            if vc.is_connected():
                if vc.is_playing():
                    vc.stop()
                def make_after(t_name, client_name):
                    def after_cb(err):
                        if err:
                            engine.log(f"❌ Playback error on [{client_name}] for '{t_name}': {err}", "error")
                        else:
                            engine.log(f"⏹ Audio stream completed for '{t_name}'.")
                    return after_cb

                try:
                    source = discord.FFmpegPCMAudio(
                        resolved_path,
                        executable=FFMPEG_PATH,
                        options=opts
                    )
                    vc.play(source, after=make_after(track_name, client.user.name))
                    stream_count += 1
                    engine.log(f"🔊 Streaming live into VC on [{client.user.name}].")
                except Exception as e:
                    engine.log(f"❌ Audio playback pipeline error: {str(e)[:60]}", "error")

    if stream_count == 0:
        engine.log("⚠️ Node is not currently in any Voice Channel. Please join a VC first!", "warn")

async def _stop_audio_coro():
    stopped = False
    for client in engine.active_clients:
        for vc in client.voice_clients:
            if vc.is_connected() and vc.is_playing():
                vc.stop()
                stopped = True
    engine.current_playing_track = None
    engine.log("⏹ Audio playback halted on all active voice pipelines." if stopped else "No audio currently playing.")

# ==============================================================================
# FASTAPI APPLICATION SETUP
# ==============================================================================

app = FastAPI(title="w4xx Web Portal", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Helper functions for persistence
def read_saved_tokens() -> List[str]:
    tokens = []
    env_tokens = os.environ.get("DISCORD_TOKENS", "")
    if env_tokens:
        for t in env_tokens.split(","):
            t = t.strip()
            if t and t not in tokens:
                tokens.append(t)
    if os.path.exists(SAVED_TOKENS_FILE):
        try:
            with open(SAVED_TOKENS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    t = line.strip()
                    if t and t not in tokens:
                        tokens.append(t)
        except Exception:
            pass
    return tokens

def write_saved_tokens(tokens: List[str]):
    try:
        with open(SAVED_TOKENS_FILE, 'w', encoding='utf-8') as f:
            for t in tokens:
                f.write(t.strip() + '\n')
    except Exception as e:
        engine.log(f"Token storage save fault: {e}", "error")

def read_saved_audios() -> List[str]:
    audios = []
    if os.path.exists(SAVED_AUDIOS_FILE):
        try:
            with open(SAVED_AUDIOS_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    audios = [os.path.normpath(p) for p in raw if os.path.exists(p)]
        except Exception:
            pass
    # Include uploads directory files
    for root, _, files in os.walk(UPLOADS_DIR):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in ['.mp3', '.wav', '.ogg', '.m4a', '.flac']:
                full_p = os.path.normpath(os.path.join(root, f))
                if full_p not in audios:
                    audios.append(full_p)
    return audios

def write_saved_audios(audios: List[str]):
    try:
        with open(SAVED_AUDIOS_FILE, 'w', encoding='utf-8') as f:
            json.dump(audios, f, indent=2)
    except Exception as e:
        engine.log(f"Audio storage save fault: {e}", "error")

# Auto-connect existing tokens on launch
@app.on_event("startup")
async def startup_event():
    existing_tokens = read_saved_tokens()
    if existing_tokens:
        engine.log(f"Found {len(existing_tokens)} saved tokens in storage. Initiating auto-connections...")
        for tok in existing_tokens:
            engine.run_coro(_connect_token_coro(tok))

# ==============================================================================
# REST API ENDPOINTS
# ==============================================================================

@app.get("/api/status")
def get_status():
    accounts_info = []
    for tok, client in engine.connected_accounts.items():
        if client.is_ready():
            accounts_info.append({
                "username": str(client.user),
                "id": str(client.user.id),
                "avatar": str(client.user.avatar.url) if client.user.avatar else None,
                "token_preview": tok[:10] + "..." + tok[-4:],
                "guilds_count": len(client.guilds)
            })
    is_actively_playing = False
    for client in engine.active_clients:
        for vc in client.voice_clients:
            if vc.is_connected() and vc.is_playing():
                is_actively_playing = True
                break

    return {
        "online": True,
        "nodes_count": len(engine.active_clients),
        "accounts": accounts_info,
        "current_vc_id": str(engine.current_vc_id) if engine.current_vc_id else None,
        "now_playing": engine.current_playing_track if is_actively_playing else None,
        "volume": engine.global_volume,
        "bass": engine.bass_boost_level
    }

@app.get("/api/tokens")
def get_tokens():
    saved = read_saved_tokens()
    tokens_status = []
    for tok in saved:
        is_conn = tok in engine.connected_accounts and engine.connected_accounts[tok].is_ready()
        client = engine.connected_accounts.get(tok)
        tokens_status.append({
            "token": tok,
            "preview": tok[:10] + "..." + tok[-4:],
            "connected": is_conn,
            "username": str(client.user) if (is_conn and client) else "Connecting..." if tok in engine.connected_accounts else "Offline"
        })
    return tokens_status

@app.post("/api/tokens/add")
async def add_token(data: dict):
    token = data.get("token", "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token cannot be empty")
    tokens = read_saved_tokens()
    if token not in tokens:
        tokens.append(token)
        write_saved_tokens(tokens)
    engine.run_coro(_connect_token_coro(token))
    return {"success": True, "message": "Token added and authorization initiated."}

@app.post("/api/tokens/remove")
async def remove_token(data: dict):
    token = data.get("token", "").strip()
    tokens = read_saved_tokens()
    if token in tokens:
        tokens.remove(token)
        write_saved_tokens(tokens)
    engine.run_coro(_disconnect_token_coro(token))
    return {"success": True, "message": "Token removed."}

@app.get("/api/servers")
def get_servers():
    all_guilds = []
    for account_key, guilds in engine.guild_cache.items():
        for g in guilds:
            all_guilds.append({
                "account": account_key,
                "name": g["name"],
                "id": str(g["id"]),
                "icon": g["icon"],
                "member_count": g["member_count"],
                "channels": g["channels"]
            })
    return all_guilds

@app.post("/api/vc/join")
def join_vc(data: dict):
    channel_id_raw = data.get("channel_id")
    if not channel_id_raw:
        raise HTTPException(status_code=400, detail="channel_id is required")
    try:
        channel_id = int(str(channel_id_raw).strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid channel_id")
    engine.run_coro(_connect_vc_coro(channel_id))
    return {"success": True, "message": f"Connecting to VC {channel_id}..."}

@app.post("/api/vc/leave")
def leave_vc():
    engine.run_coro(_leave_vc_coro())
    return {"success": True, "message": "Disconnecting from voice channels..."}

@app.get("/api/audios")
def get_audios():
    paths = read_saved_audios()
    items = []
    for p in paths:
        if os.path.exists(p):
            fname = os.path.basename(p)
            size_mb = os.path.getsize(p) / (1024 * 1024)
            items.append({
                "path": p,
                "filename": fname,
                "size_mb": round(size_mb, 2)
            })
    return items

@app.post("/api/audios/upload")
async def upload_audio(file: UploadFile = File(...)):
    filename = file.filename
    clean_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    target_path = os.path.normpath(os.path.join(UPLOADS_DIR, clean_name))
    with open(target_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)

    audios = read_saved_audios()
    if target_path not in audios:
        audios.append(target_path)
        write_saved_audios(audios)

    engine.log(f"📥 Audio vault imported: '{clean_name}' ({len(content)/(1024*1024):.2f} MB)")
    return {"success": True, "path": target_path, "filename": clean_name}

@app.post("/api/audios/delete")
def delete_audio(data: dict):
    path = data.get("path", "").strip()
    audios = read_saved_audios()
    if path in audios:
        audios.remove(path)
        write_saved_audios(audios)
    return {"success": True, "message": "Track removed from audio vault."}

@app.post("/api/audios/play")
def play_audio(data: dict):
    audio_path = data.get("path", "").strip()
    if not audio_path:
        raise HTTPException(status_code=400, detail="Audio path required")
    engine.run_coro(_play_audio_coro(audio_path))
    return {"success": True, "message": f"Streaming {os.path.basename(audio_path)}..."}

@app.post("/api/audios/stop")
def stop_audio():
    engine.run_coro(_stop_audio_coro())
    return {"success": True, "message": "Halting audio streams..."}

@app.post("/api/dsp")
def update_dsp(data: dict):
    if "volume" in data:
        engine.global_volume = float(data["volume"])
    if "bass" in data:
        engine.bass_boost_level = int(data["bass"])
    engine.log(f"🎚 DSP parameters updated: Volume={engine.global_volume*100:.0f}%, Bass=+{engine.bass_boost_level}dB")
    return {"success": True, "volume": engine.global_volume, "bass": engine.bass_boost_level}

@app.post("/api/server/join")
async def join_server(data: dict):
    invite = data.get("invite", "").strip()
    if not invite:
        raise HTTPException(status_code=400, detail="Invite code required")

    code = invite.split("/")[-1].split("?")[0]
    engine.log(f"🚀 Invoking auto-join across nodes for server invite: '{code}'...")

    joined = 0
    for tok, client in list(engine.connected_accounts.items()):
        if not client.is_ready():
            continue
        try:
            headers = {"Authorization": tok}
            url = f"https://discord.com/api/v9/invites/{code}"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    if resp.status in (200, 201):
                        joined += 1
                        engine.log(f"✅ Node [{client.user.name}] successfully joined server '{code}'!")
                    else:
                        err_data = await resp.text()
                        engine.log(f"❌ Join error on [{client.user.name}]: {err_data[:60]}", "warn")
        except Exception as e:
            engine.log(f"❌ Join exception: {e}", "error")

    return {"success": True, "joined_count": joined}

@app.get("/api/logs")
def get_logs():
    return engine.logs

@app.get("/")
def get_portal_ui():
    html_file = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(html_file):
        return FileResponse(html_file)
    return HTMLResponse("<h2>w4xx Web Portal Initializing...</h2>")

# Run Server
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"==================================================")
    print(f"🔥 w4xx HAVOC EMPIRE WEB PORTAL ONLINE")
    print(f"🌐 Access locally on: http://localhost:{port}")
    print(f"📱 Access on Mobile (Wi-Fi): http://<Your-PC-IP>:{port}")
    print(f"==================================================")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
