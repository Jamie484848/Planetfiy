import os
import time
import json
from flask import Flask, render_template_string, jsonify, request, send_from_directory
from pypresence import Presence
import threading
from werkzeug.utils import secure_filename
import mimetypes

# Neue Imports für Metadaten-Erkennung
try:
    from mutagen import File as MutagenFile
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    print("⚠️ mutagen nicht installiert. Metadaten-Erkennung deaktiviert.")
    print("   Installiere mit: pip install mutagen")

app = Flask(__name__)

# Config
UPLOAD_FOLDER = 'music_library'
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'ogg', 'flac', 'm4a'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2000 * 1024 * 1024  # 2 GB

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Discord RPC
CLIENT_ID = "1449141822407315662"

# Songs Database
SONGS_DB = 'songs_db.json'

def load_songs_db():
    if os.path.exists(SONGS_DB):
        with open(SONGS_DB, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_songs_db(songs):
    with open(SONGS_DB, 'w', encoding='utf-8') as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)

playlist = load_songs_db()

player_state = {
    "current_track_index": -1,
    "current_time": 0,
    "is_playing": False,
    "volume": 70
}

rpc = None
rpc_connected = False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_metadata(filepath):
    """Extrahiert Metadaten aus Audio-Dateien"""
    if not MUTAGEN_AVAILABLE:
        # Fallback: Dateiname verwenden
        filename = os.path.basename(filepath)
        title = os.path.splitext(filename)[0]
        return {
            'title': title,
            'artist': 'Unbekannter Artist',
            'album': '',
            'duration': get_audio_duration_fallback(filepath)
        }
    
    try:
        audio = MutagenFile(filepath, easy=True)
        
        if audio is None:
            # Fallback für WAV oder andere nicht unterstützte Formate
            filename = os.path.basename(filepath)
            title = os.path.splitext(filename)[0]
            return {
                'title': title,
                'artist': 'Unbekannter Artist',
                'album': '',
                'duration': get_audio_duration_fallback(filepath)
            }
        
        # Titel extrahieren
        title = None
        if hasattr(audio, 'tags') and audio.tags:
            # Verschiedene Titel-Tags versuchen
            title = (audio.tags.get('title') or 
                    audio.tags.get('TIT2') or 
                    audio.tags.get('\xa9nam'))
            
            if title and isinstance(title, list):
                title = title[0]
        
        if not title:
            title = os.path.splitext(os.path.basename(filepath))[0]
        
        # Artist extrahieren
        artist = None
        if hasattr(audio, 'tags') and audio.tags:
            artist = (audio.tags.get('artist') or 
                     audio.tags.get('TPE1') or 
                     audio.tags.get('\xa9ART'))
            
            if artist and isinstance(artist, list):
                artist = artist[0]
        
        if not artist:
            artist = 'Unbekannter Artist'
        
        # Album extrahieren
        album = None
        if hasattr(audio, 'tags') and audio.tags:
            album = (audio.tags.get('album') or 
                    audio.tags.get('TALB') or 
                    audio.tags.get('\xa9alb'))
            
            if album and isinstance(album, list):
                album = album[0]
        
        if not album:
            album = ''
        
        # Duration extrahieren
        duration = 180  # Fallback
        if hasattr(audio.info, 'length'):
            duration = int(audio.info.length)
        
        print(f"📀 Metadaten geladen: {title} - {artist} ({duration}s)")
        
        return {
            'title': str(title),
            'artist': str(artist),
            'album': str(album),
            'duration': duration
        }
        
    except Exception as e:
        print(f"⚠️ Metadaten-Fehler für {filepath}: {e}")
        # Fallback
        filename = os.path.basename(filepath)
        title = os.path.splitext(filename)[0]
        return {
            'title': title,
            'artist': 'Unbekannter Artist',
            'album': '',
            'duration': get_audio_duration_fallback(filepath)
        }

def get_audio_duration_fallback(filepath):
    """Fallback für Duration-Berechnung basierend auf Dateigröße"""
    try:
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        return int(size_mb * 60)
    except:
        return 180

def get_audio_duration(filepath):
    """Legacy-Funktion für Kompatibilität"""
    return get_audio_duration_fallback(filepath)

def init_discord_rpc():
    global rpc, rpc_connected
    try:
        rpc = Presence(CLIENT_ID)
        rpc.connect()
        rpc_connected = True
        print("✅ Discord RPC verbunden!")
    except Exception as e:
        print(f"❌ Discord RPC Fehler: {e}")
        rpc_connected = False

def update_discord_presence():
    global rpc, rpc_connected
    if not rpc_connected or rpc is None or player_state["current_track_index"] == -1:
        return
    
    try:
        current_track = playlist[player_state["current_track_index"]]
        
        if player_state["is_playing"]:
            state = f"von {current_track['artist']}"
            details = current_track['title']
            
            current_unix_time = int(time.time())
            elapsed_seconds = player_state["current_time"]
            total_duration = current_track["duration"]
            
            song_start_time = current_unix_time - elapsed_seconds
            song_end_time = song_start_time + total_duration
            
            print(f"Discord: {current_track['title']} | {elapsed_seconds}s / {total_duration}s")
            
            rpc.update(
                details=details,
                state=state,
                start=song_start_time,
                end=song_end_time,
                large_image="planetify_logo",
                large_text="Planetify",
                small_image="play",
                small_text="Wird abgespielt"
            )
        else:
            rpc.update(
                details=f"{current_track['title']}",
                state=f"von {current_track['artist']} • Pausiert",
                large_image="planetify_logo",
                large_text="Planetify",
                small_image="pause",
                small_text="Pausiert"
            )
    except Exception as e:
        print(f"Discord Fehler: {e}")

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Planetify - Deine Musik</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
            background: #000;
            color: #fff;
            height: 100vh;
            overflow: hidden;
        }

        .app-container {
            display: flex;
            height: 100vh;
        }

        .sidebar {
            width: 280px;
            background: #000;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 24px;
            border-right: 1px solid #1a1a1a;
            flex-shrink: 0;
        }

        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(135deg, #ff6b00 0%, #ff3d00 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 16px;
        }

        .nav-item {
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 12px 16px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            font-size: 15px;
            font-weight: 500;
            color: #b3b3b3;
        }

        .nav-item.active {
            color: #fff;
            background: linear-gradient(135deg, rgba(255, 107, 0, 0.2), rgba(255, 61, 0, 0.1));
        }

        .main-content {
            flex: 1;
            background: radial-gradient(ellipse at top, #1a0f00 0%, #000 50%);
            overflow-y: auto;
            padding: 24px;
            padding-bottom: 120px;
            -webkit-overflow-scrolling: touch;
        }

        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 32px;
        }

        .greeting {
            font-size: 32px;
            font-weight: 700;
            background: linear-gradient(135deg, #fff 0%, #b3b3b3 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .upload-btn {
            background: linear-gradient(135deg, #ff6b00 0%, #ff3d00 100%);
            color: #fff;
            border: none;
            padding: 12px 32px;
            border-radius: 24px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            box-shadow: 0 4px 20px rgba(255, 107, 0, 0.3);
            white-space: nowrap;
        }

        .section-title {
            font-size: 24px;
            font-weight: 700;
            margin-bottom: 20px;
        }

        .songs-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 24px;
        }

        .song-card {
            background: linear-gradient(135deg, #1a1a1a 0%, #0d0d0d 100%);
            padding: 20px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s;
            position: relative;
            border: 1px solid transparent;
        }

        .song-card:active {
            transform: scale(0.95);
        }

        .song-card img {
            width: 100%;
            aspect-ratio: 1;
            border-radius: 8px;
            margin-bottom: 16px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.6);
            object-fit: cover;
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
        }

        .song-card-title {
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 6px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            color: #fff;
        }

        .song-card-artist {
            font-size: 14px;
            color: #b3b3b3;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .play-overlay {
            position: absolute;
            top: 20px;
            right: 20px;
            width: 56px;
            height: 56px;
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transform: translateY(8px);
            transition: all 0.3s;
            box-shadow: 0 8px 24px rgba(255, 107, 0, 0.5);
            font-size: 24px;
            pointer-events: none;
        }

        .player-bar {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            height: 90px;
            background: linear-gradient(to top, #000 0%, #0a0a0a 100%);
            border-top: 1px solid #1a1a1a;
            display: flex;
            align-items: center;
            padding: 0 16px;
            gap: 16px;
            z-index: 100;
            backdrop-filter: blur(20px);
        }

        .player-track-info {
            display: flex;
            align-items: center;
            gap: 12px;
            width: 280px;
            flex-shrink: 0;
        }

        .player-track-info img {
            width: 56px;
            height: 56px;
            border-radius: 4px;
            object-fit: cover;
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            box-shadow: 0 4px 12px rgba(0,0,0,0.6);
        }

        .player-track-details h4 {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 4px;
            color: #fff;
        }

        .player-track-details p {
            font-size: 12px;
            color: #b3b3b3;
        }

        .player-controls {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
        }

        .control-buttons {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .control-btn {
            background: transparent;
            border: none;
            color: #b3b3b3;
            font-size: 20px;
            cursor: pointer;
            transition: all 0.2s;
            padding: 8px;
        }

        .play-pause-btn {
            width: 40px;
            height: 40px;
            background: #fff;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #000;
            font-size: 20px;
        }

        .progress-section {
            display: flex;
            align-items: center;
            gap: 12px;
            width: 100%;
            max-width: 600px;
        }

        .time-label {
            font-size: 11px;
            color: #b3b3b3;
            min-width: 40px;
        }

        .progress-bar-container {
            flex: 1;
            height: 8px;
            background: #4d4d4d;
            border-radius: 4px;
            cursor: pointer;
            position: relative;
            touch-action: none;
        }

        .progress-bar {
            height: 100%;
            background: #fff;
            border-radius: 4px;
            width: 30%;
            pointer-events: none;
        }

        .player-volume {
            width: 150px;
            display: flex;
            align-items: center;
            gap: 12px;
            flex-shrink: 0;
        }

        .volume-slider {
            flex: 1;
            height: 8px;
            background: #4d4d4d;
            border-radius: 4px;
            position: relative;
            cursor: pointer;
            touch-action: none;
        }

        .volume-level {
            height: 100%;
            background: #fff;
            border-radius: 4px;
            width: 70%;
            pointer-events: none;
        }

        .discord-indicator {
            position: fixed;
            top: 16px;
            right: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(88, 101, 242, 0.15);
            padding: 8px 16px;
            border-radius: 24px;
            font-size: 12px;
            color: #5865F2;
            z-index: 10;
            border: 1px solid rgba(88, 101, 242, 0.3);
        }

        .discord-dot {
            width: 8px;
            height: 8px;
            background: #5865F2;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.5; transform: scale(0.9); }
        }

        .empty-state {
            text-align: center;
            padding: 80px 20px;
            color: #b3b3b3;
            grid-column: 1 / -1;
        }

        .empty-state-icon {
            font-size: 72px;
            margin-bottom: 20px;
            opacity: 0.5;
        }

        /* MOBILE RESPONSIVE */
        @media (max-width: 768px) {
            .app-container {
                flex-direction: column;
            }

            .sidebar {
                width: 100%;
                padding: 12px 16px;
                border-right: none;
                border-bottom: 1px solid #1a1a1a;
                flex-direction: row;
                justify-content: space-between;
                align-items: center;
                height: auto;
                gap: 12px;
            }

            .logo {
                font-size: 20px;
                margin-bottom: 0;
            }

            .sidebar > div:last-child {
                display: flex;
                gap: 8px;
            }

            .nav-item {
                padding: 8px;
                font-size: 18px;
                gap: 0;
                min-width: 40px;
                min-height: 40px;
                justify-content: center;
            }

            .main-content {
                padding: 16px;
                padding-bottom: 220px;
            }

            .header {
                flex-direction: column;
                gap: 16px;
                align-items: stretch;
            }

            .greeting {
                font-size: 24px;
            }

            .upload-btn {
                width: 100%;
                padding: 14px;
            }

            .songs-grid {
                grid-template-columns: repeat(2, 1fr);
                gap: 12px;
            }

            .song-card {
                padding: 12px;
            }

            .song-card-title {
                font-size: 13px;
            }

            .song-card-artist {
                font-size: 11px;
            }

            .play-overlay {
                opacity: 1;
                width: 44px;
                height: 44px;
                font-size: 18px;
                top: 12px;
                right: 12px;
                transform: translateY(0);
            }

            .player-bar {
                height: auto;
                flex-wrap: wrap;
                padding: 12px;
                gap: 12px;
            }

            .player-track-info {
                width: 100%;
                order: 1;
            }

            .player-controls {
                width: 100%;
                order: 2;
            }

            .player-volume {
                width: 100%;
                order: 3;
            }

            .discord-indicator {
                bottom: 200px;
                top: auto;
                font-size: 10px;
                padding: 6px 12px;
            }

            .progress-bar-container {
                height: 12px;
            }

            .volume-slider {
                height: 12px;
            }
        }

        @media (max-width: 480px) {
            .greeting {
                font-size: 20px;
            }

            .section-title {
                font-size: 18px;
            }
        }

        @media (hover: hover) {
            .song-card:hover {
                background: linear-gradient(135deg, #2a2a2a 0%, #1a1a1a 100%);
                transform: translateY(-8px);
                border-color: #ff6b00;
                box-shadow: 0 12px 40px rgba(255, 107, 0, 0.2);
            }

            .song-card:hover .play-overlay {
                opacity: 1;
                transform: translateY(0);
            }

            .upload-btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 8px 30px rgba(255, 107, 0, 0.5);
            }

            .nav-item:hover {
                color: #fff;
                background: #1a1a1a;
            }
        }
    </style>
</head>
<body>
    <div class="app-container">
        <div class="sidebar">
            <div class="logo">
                <span>🌍</span>
                <span>PLANETIFY</span>
            </div>
            
            <div>
                <div class="nav-item active">
                    <span>🏠</span>
                </div>
                <div class="nav-item">
                    <span>📚</span>
                </div>
                <div class="nav-item">
                    <span>🔍</span>
                </div>
            </div>
        </div>

        <div class="main-content">
            <div class="discord-indicator">
                <div class="discord-dot"></div>
                Discord verbunden
            </div>

            <div class="header">
                <div class="greeting">Hey, willkommen zurück!</div>
                <button class="upload-btn" onclick="alert('Upload-Funktion (nur im Backend)')">
                    📁 Songs hochladen
                </button>
            </div>

            <div class="section-title">Deine Musik</div>
            <div class="songs-grid" id="songsGrid">
                <!-- Demo Songs -->
                <div class="song-card">
                    <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='g1' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0%25' stop-color='%23ff6b00'/%3E%3Cstop offset='100%25' stop-color='%23ff3d00'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect fill='url(%23g1)' width='100' height='100'/%3E%3Ctext x='50' y='50' text-anchor='middle' dy='.3em' fill='white' font-size='40'%3E🎵%3C/text%3E%3C/svg%3E" alt="Song 1">
                    <div class="play-overlay">▶️</div>
                    <div class="song-card-title">Demo Song 1</div>
                    <div class="song-card-artist">Demo Artist</div>
                </div>

                <div class="song-card">
                    <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='g2' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0%25' stop-color='%2300d4ff'/%3E%3Cstop offset='100%25' stop-color='%230066ff'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect fill='url(%23g2)' width='100' height='100'/%3E%3Ctext x='50' y='50' text-anchor='middle' dy='.3em' fill='white' font-size='40'%3E🎸%3C/text%3E%3C/svg%3E" alt="Song 2">
                    <div class="play-overlay">▶️</div>
                    <div class="song-card-title">Demo Song 2</div>
                    <div class="song-card-artist">Demo Artist 2</div>
                </div>

                <div class="song-card">
                    <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='g3' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0%25' stop-color='%23ff00ff'/%3E%3Cstop offset='100%25' stop-color='%23ff0066'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect fill='url(%23g3)' width='100' height='100'/%3E%3Ctext x='50' y='50' text-anchor='middle' dy='.3em' fill='white' font-size='40'%3E🎹%3C/text%3E%3C/svg%3E" alt="Song 3">
                    <div class="play-overlay">▶️</div>
                    <div class="song-card-title">Demo Song 3</div>
                    <div class="song-card-artist">Demo Artist 3</div>
                </div>

                <div class="song-card">
                    <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='g4' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0%25' stop-color='%2300ff88'/%3E%3Cstop offset='100%25' stop-color='%2300cc66'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect fill='url(%23g4)' width='100' height='100'/%3E%3Ctext x='50' y='50' text-anchor='middle' dy='.3em' fill='white' font-size='40'%3E🎤%3C/text%3E%3C/svg%3E" alt="Song 4">
                    <div class="play-overlay">▶️</div>
                    <div class="song-card-title">Demo Song 4</div>
                    <div class="song-card-artist">Demo Artist 4</div>
                </div>
            </div>
        </div>
    </div>

    <div class="player-bar">
        <div class="player-track-info">
            <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect fill='%23ff6b00' width='100' height='100'/%3E%3C/svg%3E" alt="Cover">
            <div class="player-track-details">
                <h4>Wähle einen Song</h4>
                <p>Artist</p>
            </div>
        </div>

        <div class="player-controls">
            <div class="control-buttons">
                <button class="control-btn">⏮️</button>
                <button class="control-btn play-pause-btn">▶️</button>
                <button class="control-btn">⏭️</button>
            </div>
            <div class="progress-section">
                <span class="time-label">0:00</span>
                <div class="progress-bar-container" id="progressBar">
                    <div class="progress-bar"></div>
                </div>
                <span class="time-label">3:45</span>
            </div>
        </div>

        <div class="player-volume">
            <span>🔊</span>
            <div class="volume-slider" id="volumeSlider">
                <div class="volume-level"></div>
            </div>
        </div>
    </div>

    <script>
        const audioPlayer = document.getElementById('audioPlayer');
        let playlist = [];
        let currentTrackIndex = -1;
        let isPlaying = false;
        let selectedFiles = [];

        // Volume Slider with Drag Support
        let isDraggingVolume = false;
        
        function updateVolume(e) {
            const slider = document.getElementById('volumeSlider');
            const rect = slider.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const width = rect.width;
            const percentage = Math.max(0, Math.min(1, clickX / width));
            
            audioPlayer.volume = percentage;
            document.getElementById('volumeLevel').style.width = (percentage * 100) + '%';
        }
        
        document.getElementById('volumeSlider').addEventListener('mousedown', (e) => {
            isDraggingVolume = true;
            updateVolume(e);
        });
        
        document.addEventListener('mousemove', (e) => {
            if (isDraggingVolume) {
                updateVolume(e);
            }
        });
        
        document.addEventListener('mouseup', () => {
            isDraggingVolume = false;
        });

        // Audio Context & Equalizer
        let audioContext;
        let sourceNode;
        let gainNode;
        let filters = [];
        let currentPreset = 'flat';

        const frequencies = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000];

        const eqPresets = {
            flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            bass: [8, 6, 4, 2, 0, 0, -2, -2, -2, -2],
            treble: [-2, -2, -2, 0, 0, 2, 4, 6, 8, 8],
            vocal: [-2, -1, 2, 4, 4, 3, 1, 0, -1, -2],
            rock: [5, 3, -2, -3, -1, 1, 3, 4, 4, 4],
            pop: [-1, 2, 4, 4, 2, 0, -1, -1, -1, -1],
            jazz: [3, 2, 0, 1, -1, -1, 0, 1, 2, 3],
            classical: [3, 2, -1, -2, -2, -1, 2, 3, 4, 4],
            electronic: [6, 4, 1, 0, -2, 2, 1, 2, 6, 7],
            hiphop: [7, 5, 1, 2, -1, -1, 1, -1, 2, 3]
        };

        function initAudioContext() {
            if (audioContext) return;

            audioContext = new (window.AudioContext || window.webkitAudioContext)();
            sourceNode = audioContext.createMediaElementSource(audioPlayer);
            gainNode = audioContext.createGain();

            // Create filters for each frequency
            frequencies.forEach((freq, i) => {
                const filter = audioContext.createBiquadFilter();
                filter.type = i === 0 ? 'lowshelf' : i === frequencies.length - 1 ? 'highshelf' : 'peaking';
                filter.frequency.value = freq;
                filter.Q.value = 1;
                filter.gain.value = 0;
                filters.push(filter);
            });

            // Connect everything
            sourceNode.connect(filters[0]);
            for (let i = 0; i < filters.length - 1; i++) {
                filters[i].connect(filters[i + 1]);
            }
            filters[filters.length - 1].connect(gainNode);
            gainNode.connect(audioContext.destination);

            console.log('🎚️ Equalizer initialisiert');
        }

        function toggleEqualizer() {
            document.getElementById('eqModal').classList.add('active');
        }

        function closeEqualizer() {
            document.getElementById('eqModal').classList.remove('active');
        }

        function applyPreset(presetName) {
            currentPreset = presetName;
            const values = eqPresets[presetName];

            // Update UI
            document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');

            // Apply to filters and sliders
            frequencies.forEach((freq, i) => {
                const value = values[i];
                const slider = document.getElementById(`eq${freq}`);
                const valueDisplay = document.getElementById(`val${freq}`);
                
                slider.value = value;
                valueDisplay.textContent = value >= 0 ? `+${value}dB` : `${value}dB`;

                if (filters[i]) {
                    filters[i].gain.value = value;
                }
            });
        }

        function resetEqualizer() {
            applyPreset('flat');
            document.querySelector('.preset-btn').classList.add('active');
        }

        function updateEQValue(freq, value) {
            const valueDisplay = document.getElementById(`val${freq}`);
            valueDisplay.textContent = value >= 0 ? `+${value}dB` : `${value}dB`;

            const index = frequencies.indexOf(parseInt(freq));
            if (filters[index]) {
                filters[index].gain.value = parseFloat(value);
            }

            // Deselect preset if manually adjusted
            document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
        }

        function formatTime(seconds) {
            if (!seconds || isNaN(seconds) || seconds === Infinity) return '0:00';
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return `${mins}:${secs.toString().padStart(2, '0')}`;
        }

        function loadPlaylist() {
            fetch('/playlist')
                .then(res => res.json())
                .then(data => {
                    playlist = data;
                    renderPlaylist();
                });
        }

        function renderPlaylist() {
            const grid = document.getElementById('songsGrid');
            
            if (playlist.length === 0) {
                grid.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon">🎵</div>
                        <h3>Noch keine Songs</h3>
                        <p>Lade deine ersten MP3s hoch!</p>
                    </div>
                `;
                return;
            }

            grid.innerHTML = playlist.map((song, index) => {
                const coverSrc = song.cover_url || "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect fill='%23ff6b00' width='100' height='100'/%3E%3Ctext x='50' y='50' text-anchor='middle' dy='.3em' fill='white' font-size='40'%3E🎵%3C/text%3E%3C/svg%3E";
                
                return `
                    <div class="song-card" onclick="playTrack(${index})">
                        <div class="song-options">
                            <button class="option-btn" onclick="event.stopPropagation(); openEditModal(${index})" title="Bearbeiten">✏️</button>
                            <button class="option-btn" onclick="event.stopPropagation(); quickDelete(${index})" title="Löschen">🗑️</button>
                        </div>
                        <img src="${coverSrc}" alt="${song.title}">
                        <div class="play-overlay">▶️</div>
                        <div class="song-card-title">${song.title}</div>
                        <div class="song-card-artist">${song.artist}</div>
                    </div>
                `;
            }).join('');
        }

        function playTrack(index) {
            currentTrackIndex = index;
            const track = playlist[index];

            // Initialize audio context on first play
            if (!audioContext) {
                initAudioContext();
            }
            
            audioPlayer.src = `/stream/${track.filename}`;
            audioPlayer.load();
            
            audioPlayer.addEventListener('loadedmetadata', () => {
                const realDuration = Math.floor(audioPlayer.duration);
                audioPlayer.play().catch(e => console.log('Playback error:', e));
                isPlaying = true;
                document.getElementById('playPauseBtn').textContent = '⏸️';
                updatePlayerInfo(track, realDuration);
            }, { once: true });
        }

        function updatePlayerInfo(track, realDuration) {
            const coverSrc = track.cover_url || "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect fill='%23ff6b00' width='100' height='100'/%3E%3C/svg%3E";
            
            document.getElementById('playerCover').src = coverSrc;
            document.getElementById('playerTitle').textContent = track.title;
            document.getElementById('playerArtist').textContent = track.artist;
            
            fetch('/set_track', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    index: currentTrackIndex,
                    duration: realDuration || track.duration,
                    time: 0
                })
            });
        }

        function togglePlay() {
            if (currentTrackIndex === -1) return;
            
            if (isPlaying) {
                audioPlayer.pause();
                isPlaying = false;
                document.getElementById('playPauseBtn').textContent = '▶️';
                
                fetch('/pause', { 
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ time: Math.floor(audioPlayer.currentTime) })
                });
            } else {
                audioPlayer.play().catch(e => console.log('Play error:', e));
                isPlaying = true;
                document.getElementById('playPauseBtn').textContent = '⏸️';
                
                fetch('/play', { 
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        time: Math.floor(audioPlayer.currentTime),
                        duration: Math.floor(audioPlayer.duration)
                    })
                });
            }
        }

        function previousTrack() {
            if (currentTrackIndex > 0) {
                playTrack(currentTrackIndex - 1);
            }
        }

        function nextTrack() {
            if (currentTrackIndex < playlist.length - 1) {
                playTrack(currentTrackIndex + 1);
            } else if (playlist.length > 0) {
                playTrack(0);
            }
        }

        function openUploadModal() {
            selectedFiles = [];
            document.getElementById('fileList').style.display = 'none';
            document.getElementById('uploadButton').style.display = 'none';
            document.getElementById('uploadModal').classList.add('active');
        }

        function closeUploadModal() {
            selectedFiles = [];
            document.getElementById('fileInput').value = '';
            document.getElementById('uploadModal').classList.remove('active');
        }

        function handleFileSelect(event) {
            const files = Array.from(event.target.files);
            selectedFiles = files;
            displayFileList();
        }

        function displayFileList() {
            const fileList = document.getElementById('fileList');
            const uploadButton = document.getElementById('uploadButton');
            
            if (selectedFiles.length === 0) {
                fileList.style.display = 'none';
                uploadButton.style.display = 'none';
                return;
            }

            fileList.style.display = 'block';
            uploadButton.style.display = 'block';
            uploadButton.textContent = `${selectedFiles.length} Song(s) hochladen`;

            fileList.innerHTML = selectedFiles.map((file, index) => `
                <div class="file-item">
                    <span class="file-item-name">🎵 ${file.name}</span>
                    <button class="file-item-remove" onclick="removeFile(${index})">✕</button>
                </div>
            `).join('');
        }

        function removeFile(index) {
            selectedFiles.splice(index, 1);
            displayFileList();
        }

        function uploadFiles() {
    if (selectedFiles.length === 0) return;

    const formData = new FormData();
    selectedFiles.forEach(file => {
        formData.append('files', file);
    });

    const uploadButton = document.getElementById('uploadButton');
    uploadButton.textContent = 'Wird hochgeladen...';
    uploadButton.disabled = true;

    fetch('/upload', {
        method: 'POST',
        body: formData
    })
    .then(res => {
        if (!res.ok) {
            throw new Error('Upload fehlgeschlagen');
        }
        return res.json();
    })
    .then(data => {
        closeUploadModal();
        loadPlaylist();
        alert(`✅ ${data.uploaded} Song(s) erfolgreich hochgeladen!`);
    })
    .catch(err => {
        console.error('Upload Fehler:', err);
        alert('❌ Fehler beim Hochladen. Siehe Console für Details.');
        uploadButton.disabled = false;
        uploadButton.textContent = `${selectedFiles.length} Song(s) hochladen`;
    });
}

        const uploadArea = document.getElementById('uploadArea');
        
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('drag-over');
        });

        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('drag-over');
        });

        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('drag-over');
            
            const files = Array.from(e.dataTransfer.files);
            const audioFiles = files.filter(file => {
                const ext = file.name.split('.').pop().toLowerCase();
                return ['mp3', 'wav', 'ogg', 'flac', 'm4a'].includes(ext);
            });

            if (audioFiles.length > 0) {
                selectedFiles = audioFiles;
                displayFileList();
            }
        });

        function openEditModal(index) {
            const song = playlist[index];
            document.getElementById('editSongId').value = song.id;
            document.getElementById('editTitle').value = song.title;
            document.getElementById('editArtist').value = song.artist;
            document.getElementById('editAlbum').value = song.album || '';
            document.getElementById('editModal').classList.add('active');
        }

        function closeEditModal() {
            document.getElementById('editModal').classList.remove('active');
        }

        function saveSongEdit() {
            const id = document.getElementById('editSongId').value;
            const title = document.getElementById('editTitle').value;
            const artist = document.getElementById('editArtist').value;
            const album = document.getElementById('editAlbum').value;

            fetch('/edit_song', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: parseInt(id), title, artist, album })
            })
            .then(res => res.json())
            .then(() => {
                closeEditModal();
                loadPlaylist();
            });
        }

        function quickDelete(index) {
            const song = playlist[index];
            if (!confirm(`"${song.title}" wirklich löschen?`)) return;
            
            fetch('/delete_song', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: song.id })
            })
            .then(res => res.json())
            .then(() => {
                if (currentTrackIndex === index) {
                    audioPlayer.pause();
                    currentTrackIndex = -1;
                    isPlaying = false;
                    document.getElementById('playPauseBtn').textContent = '▶️';
                }
                loadPlaylist();
            });
        }

        function deleteSong() {
            const id = document.getElementById('editSongId').value;
            if (!confirm('Song wirklich löschen?')) return;
            
            fetch('/delete_song', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: parseInt(id) })
            })
            .then(res => res.json())
            .then(() => {
                closeEditModal();
                const deletedIndex = playlist.findIndex(s => s.id === parseInt(id));
                if (currentTrackIndex === deletedIndex) {
                    audioPlayer.pause();
                    currentTrackIndex = -1;
                    isPlaying = false;
                }
                loadPlaylist();
            });
        }

        document.getElementById('progressContainer').addEventListener('click', (e) => {
            if (currentTrackIndex === -1 || !audioPlayer.duration) return;
            const bar = e.currentTarget;
            const rect = bar.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const width = rect.width;
            const percentage = clickX / width;
            audioPlayer.currentTime = audioPlayer.duration * percentage;
        });

        document.getElementById('volumeSlider').addEventListener('click', (e) => {
            const slider = e.currentTarget;
            const rect = slider.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const width = rect.width;
            const percentage = clickX / width;
            
            audioPlayer.volume = percentage;
            document.getElementById('volumeLevel').style.width = (percentage * 100) + '%';
        });

        let lastUpdateTime = 0;
        audioPlayer.addEventListener('timeupdate', () => {
            if (audioPlayer.duration && !isNaN(audioPlayer.duration) && audioPlayer.duration !== Infinity) {
                const percentage = (audioPlayer.currentTime / audioPlayer.duration) * 100;
                document.getElementById('progressBar').style.width = percentage + '%';
                document.getElementById('currentTime').textContent = formatTime(audioPlayer.currentTime);
                document.getElementById('duration').textContent = formatTime(audioPlayer.duration);
                
                const now = Date.now();
                if (now - lastUpdateTime > 2000) {
                    lastUpdateTime = now;
                    fetch('/update_time', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ 
                            time: Math.floor(audioPlayer.currentTime),
                            duration: Math.floor(audioPlayer.duration),
                            is_playing: !audioPlayer.paused
                        })
                    });
                }
            }
        });

        audioPlayer.addEventListener('ended', () => {
            nextTrack();
        });

        audioPlayer.addEventListener('error', (e) => {
            console.error('Audio error:', e);
        });

        loadPlaylist();
        audioPlayer.volume = 0.7;

        // Initialize EQ sliders
        frequencies.forEach(freq => {
            const slider = document.getElementById(`eq${freq}`);
            slider.addEventListener('input', (e) => {
                updateEQValue(freq, e.target.value);
            });
        });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/playlist')
def get_playlist():
    return jsonify(playlist)

@app.route('/stream/<filename>')
def stream_music(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    mimetype, _ = mimetypes.guess_type(filename)
    if not mimetype:
        mimetype = 'audio/mpeg'
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, mimetype=mimetype)

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'files' not in request.files:
        return jsonify({'error': 'Keine Dateien'}), 400
    
    files = request.files.getlist('files')
    uploaded = 0
    
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            
            base_name = filename
            counter = 1
            while os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
                name, ext = os.path.splitext(base_name)
                filename = f"{name}_{counter}{ext}"
                counter += 1
            
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Metadaten automatisch extrahieren
            metadata = extract_metadata(filepath)
            
            new_song = {
                'id': max([s['id'] for s in playlist], default=0) + 1,
                'filename': filename,
                'title': metadata['title'],
                'artist': metadata['artist'],
                'album': metadata['album'],
                'duration': metadata['duration'],
                'cover_url': ''
            }
            playlist.append(new_song)
            uploaded += 1
    
    save_songs_db(playlist)
    return jsonify({'success': True, 'uploaded': uploaded})

@app.route('/edit_song', methods=['POST'])
def edit_song():
    data = request.get_json()
    song_id = data['id']
    
    for song in playlist:
        if song['id'] == song_id:
            song['title'] = data['title']
            song['artist'] = data['artist']
            song['album'] = data['album']
            break
    
    save_songs_db(playlist)
    return jsonify({'success': True})

@app.route('/delete_song', methods=['POST'])
def delete_song():
    global playlist
    data = request.get_json()
    song_id = data['id']
    
    song_to_delete = None
    for song in playlist:
        if song['id'] == song_id:
            song_to_delete = song
            break
    
    if song_to_delete:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], song_to_delete['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        
        playlist = [s for s in playlist if s['id'] != song_id]
        save_songs_db(playlist)
    
    return jsonify({'success': True})

@app.route('/set_track', methods=['POST'])
def set_track():
    data = request.get_json()
    player_state['current_track_index'] = data['index']
    player_state['current_time'] = data.get('time', 0)
    player_state['is_playing'] = True
    
    if 'duration' in data and data['index'] >= 0 and data['index'] < len(playlist):
        playlist[data['index']]['duration'] = data['duration']
        save_songs_db(playlist)
    
    update_discord_presence()
    return jsonify({'success': True})

@app.route('/play', methods=['POST'])
def play():
    data = request.get_json() if request.get_json() else {}
    player_state['is_playing'] = True
    
    if 'time' in data:
        player_state['current_time'] = data['time']
    if 'duration' in data and player_state['current_track_index'] >= 0:
        playlist[player_state['current_track_index']]['duration'] = data['duration']
    
    update_discord_presence()
    return jsonify({'success': True})

@app.route('/pause', methods=['POST'])
def pause():
    data = request.get_json() if request.get_json() else {}
    player_state['is_playing'] = False
    
    if 'time' in data:
        player_state['current_time'] = data['time']
    
    update_discord_presence()
    return jsonify({'success': True})

@app.route('/update_time', methods=['POST'])
def update_time():
    data = request.get_json()
    player_state['current_time'] = data['time']
    
    if 'is_playing' in data:
        old_state = player_state['is_playing']
        player_state['is_playing'] = data['is_playing']
        
        if old_state != player_state['is_playing']:
            update_discord_presence()
    
    if 'duration' in data and player_state['current_track_index'] >= 0:
        playlist[player_state['current_track_index']]['duration'] = data['duration']
    
    return jsonify({'success': True})

if __name__ == '__main__':
    threading.Thread(target=init_discord_rpc, daemon=True).start()
    
    print("🌍 Planetify startet...")
    print("✨ Öffne http://localhost:5000")
    print("📁 Musik-Ordner: ./music_library/")
    print("🎵 Viel Spaß!")
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)