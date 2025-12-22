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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Planetify - Deine Musik</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
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

        .nav-item:hover {
            color: #fff;
            background: #1a1a1a;
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
        }

        .upload-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 30px rgba(255, 107, 0, 0.5);
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

        .song-card:hover {
            background: linear-gradient(135deg, #2a2a2a 0%, #1a1a1a 100%);
            transform: translateY(-8px);
            border-color: #ff6b00;
            box-shadow: 0 12px 40px rgba(255, 107, 0, 0.2);
        }

        .song-card:hover .song-options {
            opacity: 1;
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
        }

        .song-card:hover .play-overlay {
            opacity: 1;
            transform: translateY(0);
        }

        .song-options {
            position: absolute;
            top: 14px;
            left: 14px;
            opacity: 0;
            transition: all 0.3s;
            display: flex;
            gap: 6px;
            z-index: 10;
        }

        .option-btn {
            width: 36px;
            height: 36px;
            background: rgba(0,0,0,0.9);
            border: 1px solid #333;
            border-radius: 50%;
            color: #fff;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
            backdrop-filter: blur(10px);
        }

        .option-btn:hover {
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            border-color: #ff6b00;
            transform: scale(1.1);
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

        .control-btn:hover {
            color: #fff;
            transform: scale(1.15);
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

        .play-pause-btn:hover {
            transform: scale(1.1);
            box-shadow: 0 4px 16px rgba(255,255,255,0.3);
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
            height: 4px;
            background: #4d4d4d;
            border-radius: 2px;
            cursor: pointer;
            position: relative;
        }

        .progress-bar-container:hover .progress-bar {
            background: linear-gradient(90deg, #ff6b00, #ff3d00);
        }

        .progress-bar {
            height: 100%;
            background: #fff;
            border-radius: 2px;
            width: 0%;
            transition: background 0.2s;
        }

        .player-volume {
            width: 150px;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .player-eq {
            display: flex;
            align-items: center;
        }

        .eq-btn {
            background: transparent;
            border: 1px solid #333;
            color: #b3b3b3;
            width: 36px;
            height: 36px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 18px;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .eq-btn:hover {
            border-color: #ff6b00;
            color: #ff6b00;
            transform: scale(1.1);
        }

        .eq-modal {
            width: 700px;
            max-width: 95%;
        }

        .eq-presets {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 8px;
            margin-bottom: 32px;
        }

        .preset-btn {
            background: #0a0a0a;
            border: 1px solid #333;
            color: #b3b3b3;
            padding: 10px;
            border-radius: 8px;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
        }

        .preset-btn:hover {
            border-color: #ff6b00;
            color: #fff;
        }

        .preset-btn.active {
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            border-color: #ff6b00;
            color: #fff;
        }

        .eq-sliders {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 32px;
            padding: 20px;
            background: #0a0a0a;
            border-radius: 12px;
            min-height: 280px;
        }

        .eq-slider-group {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 12px;
        }

        .eq-slider-container {
            position: relative;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
        }

        .eq-slider {
            -webkit-appearance: slider-vertical;
            writing-mode: bt-lr;
            height: 180px;
            width: 4px;
            background: #333;
            border-radius: 2px;
            outline: none;
            cursor: pointer;
        }

        .eq-slider::-webkit-slider-thumb {
            -webkit-appearance: none;
            appearance: none;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            cursor: pointer;
            box-shadow: 0 2px 8px rgba(255, 107, 0, 0.4);
        }

        .eq-slider::-moz-range-thumb {
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            cursor: pointer;
            border: none;
            box-shadow: 0 2px 8px rgba(255, 107, 0, 0.4);
        }

        .eq-value {
            font-size: 11px;
            color: #ff6b00;
            font-weight: 600;
            min-width: 40px;
            text-align: center;
        }

        .eq-slider-group label {
            font-size: 11px;
            color: #b3b3b3;
            font-weight: 500;
        }

        .volume-slider {
            flex: 1;
            height: 4px;
            background: #4d4d4d;
            border-radius: 2px;
            position: relative;
            cursor: pointer;
        }

        .volume-level {
            height: 100%;
            background: #fff;
            border-radius: 2px;
            width: 70%;
        }

        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.9);
            z-index: 1000;
            align-items: center;
            justify-content: center;
            backdrop-filter: blur(10px);
        }

        .modal.active {
            display: flex;
        }

        .modal-content {
            background: linear-gradient(135deg, #1a1a1a 0%, #0d0d0d 100%);
            border-radius: 16px;
            padding: 32px;
            width: 500px;
            max-width: 90%;
            border: 1px solid #333;
            box-shadow: 0 20px 60px rgba(0,0,0,0.8);
        }

        .modal-header {
            font-size: 24px;
            font-weight: 700;
            margin-bottom: 24px;
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .form-group {
            margin-bottom: 20px;
        }

        .form-group label {
            display: block;
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #b3b3b3;
        }

        .form-group input {
            width: 100%;
            background: #0a0a0a;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 12px;
            color: #fff;
            font-size: 14px;
            transition: all 0.2s;
        }

        .form-group input:focus {
            outline: none;
            border-color: #ff6b00;
            box-shadow: 0 0 0 3px rgba(255, 107, 0, 0.1);
        }

        .upload-area {
            border: 2px dashed #535353;
            border-radius: 12px;
            padding: 48px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            margin-bottom: 20px;
            background: #0a0a0a;
        }

        .upload-area:hover {
            border-color: #ff6b00;
            background: rgba(255, 107, 0, 0.05);
        }

        .upload-area.drag-over {
            border-color: #ff6b00;
            background: rgba(255, 107, 0, 0.1);
        }

        .upload-area input {
            display: none;
        }

        .upload-icon {
            font-size: 56px;
            margin-bottom: 16px;
        }

        .upload-info {
            font-size: 12px;
            color: #b3b3b3;
            margin-top: 8px;
        }

        .file-list {
            max-height: 200px;
            overflow-y: auto;
            margin-bottom: 20px;
        }

        .file-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: #0a0a0a;
            border-radius: 6px;
            margin-bottom: 6px;
            font-size: 13px;
        }

        .file-item-name {
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .file-item-remove {
            background: transparent;
            border: none;
            color: #e22134;
            cursor: pointer;
            padding: 4px 8px;
            font-size: 16px;
        }

        .modal-buttons {
            display: flex;
            gap: 12px;
            margin-top: 24px;
        }

        .modal-btn {
            flex: 1;
            padding: 12px;
            border: none;
            border-radius: 24px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }

        .modal-btn-cancel {
            background: transparent;
            color: #fff;
            border: 1px solid #535353;
        }

        .modal-btn-cancel:hover {
            border-color: #fff;
        }

        .modal-btn-submit {
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            color: #fff;
            box-shadow: 0 4px 16px rgba(255, 107, 0, 0.3);
        }

        .modal-btn-submit:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 24px rgba(255, 107, 0, 0.5);
        }

        .modal-btn-delete {
            background: #e22134;
            color: #fff;
        }

        .modal-btn-delete:hover {
            background: #ff3d3d;
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
        }

        .empty-state-icon {
            font-size: 72px;
            margin-bottom: 20px;
            opacity: 0.5;
        }

        ::-webkit-scrollbar {
            width: 12px;
        }

        ::-webkit-scrollbar-track {
            background: transparent;
        }

        ::-webkit-scrollbar-thumb {
            background: #333;
            border-radius: 6px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: #535353;
        }

        /* ============================================ */
        /* MOBILE RESPONSIVE STYLES */
        /* ============================================ */
        @media (max-width: 768px) {
            .app-container {
                flex-direction: column;
            }

            .sidebar {
                width: 100%;
                padding: 16px;
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
                padding: 8px 12px;
                font-size: 13px;
                gap: 8px;
            }

            .main-content {
                padding: 16px;
                padding-bottom: 200px;
            }

            .header {
                flex-direction: column;
                gap: 16px;
                align-items: flex-start;
            }

            .greeting {
                font-size: 24px;
            }

            .upload-btn {
                width: 100%;
                padding: 14px 24px;
            }

            .songs-grid {
                grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
                gap: 16px;
            }

            .song-card {
                padding: 12px;
            }

            .song-card-title {
                font-size: 14px;
            }

            .song-card-artist {
                font-size: 12px;
            }

            .play-overlay {
                width: 48px;
                height: 48px;
                font-size: 20px;
                top: 12px;
                right: 12px;
            }

            .song-options {
                opacity: 1;
                top: 8px;
                left: 8px;
            }

            .option-btn {
                width: 32px;
                height: 32px;
                font-size: 14px;
            }

            .player-bar {
                height: auto;
                flex-direction: column;
                padding: 12px;
                gap: 12px;
                align-items: stretch;
            }

            .player-track-info {
                width: 100%;
                justify-content: flex-start;
            }

            .player-track-info img {
                width: 48px;
                height: 48px;
            }

            .player-track-details h4 {
                font-size: 13px;
            }

            .player-track-details p {
                font-size: 11px;
            }

            .player-controls {
                width: 100%;
            }

            .progress-section {
                max-width: 100%;
            }

            .player-volume {
                width: 100%;
            }

            .player-eq {
                position: absolute;
                top: 12px;
                right: 12px;
            }

            .discord-indicator {
                top: auto;
                bottom: 220px;
                right: 12px;
                font-size: 10px;
                padding: 6px 12px;
            }

            .discord-dot {
                width: 6px;
                height: 6px;
            }

            .modal-content {
                width: 95%;
                padding: 24px;
                margin: 16px;
            }

            .modal-header {
                font-size: 20px;
            }

            .upload-area {
                padding: 32px 16px;
            }

            .upload-icon {
                font-size: 48px;
            }

            .eq-modal {
                width: 95%;
                max-height: 90vh;
                overflow-y: auto;
            }

            .eq-presets {
                grid-template-columns: repeat(2, 1fr);
            }

            .eq-sliders {
                flex-wrap: wrap;
                gap: 8px;
                padding: 16px;
                min-height: auto;
            }

            .eq-slider {
                height: 120px;
            }

            .eq-slider-group {
                gap: 8px;
            }

            .control-buttons {
                gap: 12px;
            }
        }

        @media (max-width: 480px) {
            .songs-grid {
                grid-template-columns: repeat(2, 1fr);
                gap: 12px;
            }

            .greeting {
                font-size: 20px;
            }

            .section-title {
                font-size: 20px;
            }

            .eq-presets {
                grid-template-columns: 1fr;
            }

            .control-btn {
                font-size: 18px;
            }

            .play-pause-btn {
                width: 36px;
                height: 36px;
                font-size: 18px;
            }

            .modal-buttons {
                flex-direction: column;
            }

            .modal-btn {
                width: 100%;
            }
        }

        /* Landscape Tablets */
        @media (min-width: 769px) and (max-width: 1024px) {
            .sidebar {
                width: 220px;
                padding: 20px;
            }

            .songs-grid {
                grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            }
        }
    </style>
</head>
<body>
    <audio id="audioPlayer" preload="metadata"></audio>

    <div class="app-container">
        <div class="sidebar">
            <div class="logo">
                <span>🌍</span>
                PLANETIFY
            </div>
            
            <div>
                <div class="nav-item active">
                    <span>🏠</span>
                    Start
                </div>
                <div class="nav-item">
                    <span>📚</span>
                    Deine Mediathek
                </div>
                <div class="nav-item">
                    <span>🔍</span>
                    Suchen
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
                <button class="upload-btn" onclick="openUploadModal()">
                    📁 Songs hochladen
                </button>
            </div>

            <div class="section-title">Deine Musik</div>
            <div class="songs-grid" id="songsGrid">
                <div class="empty-state">
                    <div class="empty-state-icon">🎵</div>
                    <h3>Noch keine Songs</h3>
                    <p>Lade deine ersten MP3s hoch!</p>
                </div>
            </div>
        </div>
    </div>

    <div class="player-bar">
        <div class="player-track-info">
            <img id="playerCover" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect fill='%23ff6b00' width='100' height='100'/%3E%3C/svg%3E" alt="Cover">
            <div class="player-track-details">
                <h4 id="playerTitle">Wähle einen Song</h4>
                <p id="playerArtist">Artist</p>
            </div>
        </div>

        <div class="player-controls">
            <div class="control-buttons">
                <button class="control-btn" onclick="previousTrack()">⏮️</button>
                <button class="control-btn play-pause-btn" id="playPauseBtn" onclick="togglePlay()">▶️</button>
                <button class="control-btn" onclick="nextTrack()">⏭️</button>
            </div>
            <div class="progress-section">
                <span class="time-label" id="currentTime">0:00</span>
                <div class="progress-bar-container" id="progressContainer">
                    <div class="progress-bar" id="progressBar"></div>
                </div>
                <span class="time-label" id="duration">0:00</span>
            </div>
        </div>

        <div class="player-volume">
            <span>🔊</span>
            <div class="volume-slider" id="volumeSlider">
                <div class="volume-level" id="volumeLevel"></div>
            </div>
        </div>

        <div class="player-eq">
            <button class="eq-btn" onclick="toggleEqualizer()" title="Equalizer">
                🎚️
            </button>
        </div>
    </div>

    <!-- Equalizer Modal -->
    <div class="modal" id="eqModal">
        <div class="modal-content eq-modal">
            <div class="modal-header">Equalizer</div>
            
            <div class="eq-presets">
                <button class="preset-btn active" onclick="applyPreset('flat')">Natürlich</button>
                <button class="preset-btn" onclick="applyPreset('bass')">Bass-Booster</button>
                <button class="preset-btn" onclick="applyPreset('treble')">Höhen-Booster</button>
                <button class="preset-btn" onclick="applyPreset('vocal')">Gesprochenes Wort</button>
                <button class="preset-btn" onclick="applyPreset('rock')">Rock</button>
                <button class="preset-btn" onclick="applyPreset('pop')">Pop</button>
                <button class="preset-btn" onclick="applyPreset('jazz')">Jazz</button>
                <button class="preset-btn" onclick="applyPreset('classical')">Klassik</button>
                <button class="preset-btn" onclick="applyPreset('electronic')">Electronic</button>
                <button class="preset-btn" onclick="applyPreset('hiphop')">Hip-Hop</button>
            </div>

            <div class="eq-sliders">
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq60" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val60">0dB</div>
                    </div>
                    <label>60Hz</label>
                </div>
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq170" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val170">0dB</div>
                    </div>
                    <label>170Hz</label>
                </div>
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq310" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val310">0dB</div>
                    </div>
                    <label>310Hz</label>
                </div>
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq600" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val600">0dB</div>
                    </div>
                    <label>600Hz</label>
                </div>
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq1000" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val1000">0dB</div>
                    </div>
                    <label>1kHz</label>
                </div>
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq3000" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val3000">0dB</div>
                    </div>
                    <label>3kHz</label>
                </div>
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq6000" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val6000">0dB</div>
                    </div>
                    <label>6kHz</label>
                </div>
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq12000" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val12000">0dB</div>
                    </div>
                    <label>12kHz</label>
                </div>
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq14000" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val14000">0dB</div>
                    </div>
                    <label>14kHz</label>
                </div>
                <div class="eq-slider-group">
                    <div class="eq-slider-container">
                        <input type="range" class="eq-slider" id="eq16000" min="-12" max="12" value="0" step="1" orient="vertical">
                        <div class="eq-value" id="val16000">0dB</div>
                    </div>
                    <label>16kHz</label>
                </div>
            </div>

            <div class="modal-buttons">
                <button class="modal-btn modal-btn-cancel" onclick="resetEqualizer()">Zurücksetzen</button>
                <button class="modal-btn modal-btn-submit" onclick="closeEqualizer()">Fertig</button>
            </div>
        </div>
    </div>

    <div class="modal" id="uploadModal">
        <div class="modal-content">
            <div class="modal-header">Songs hochladen</div>
            <div class="upload-area" id="uploadArea" onclick="document.getElementById('fileInput').click()">
                <input type="file" id="fileInput" accept=".mp3,.wav,.ogg,.flac,.m4a" multiple onchange="handleFileSelect(event)">
                <div class="upload-icon">📁</div>
                <p>Klicke hier oder ziehe Dateien rein</p>
                <p class="upload-info">Unterstützt: MP3, WAV, OGG, FLAC, M4A</p>
            </div>
            <div class="file-list" id="fileList" style="display:none;"></div>
            <div class="modal-buttons">
                <button class="modal-btn modal-btn-cancel" onclick="closeUploadModal()">Abbrechen</button>
                <button class="modal-btn modal-btn-submit" id="uploadButton" onclick="uploadFiles()" style="display:none;">
                    Hochladen
                </button>
            </div>
        </div>
    </div>

    <div class="modal" id="editModal">
        <div class="modal-content">
            <div class="modal-header">Song bearbeiten</div>
            <input type="hidden" id="editSongId">
            <div class="form-group">
                <label>Titel</label>
                <input type="text" id="editTitle" placeholder="Song Titel">
            </div>
            <div class="form-group">
                <label>Artist</label>
                <input type="text" id="editArtist" placeholder="Artist Name">
            </div>
            <div class="form-group">
                <label>Album</label>
                <input type="text" id="editAlbum" placeholder="Album Name">
            </div>
            <div class="modal-buttons">
                <button class="modal-btn modal-btn-delete" onclick="deleteSong()">🗑️ Löschen</button>
                <button class="modal-btn modal-btn-cancel" onclick="closeEditModal()">Abbrechen</button>
                <button class="modal-btn modal-btn-submit" onclick="saveSongEdit()">Speichern</button>
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
        
        // Volume & Progress Touch Support
        let isDraggingVolume = false;
        let isDraggingProgress = false;
        
        function updateVolume(e) {
            const slider = document.getElementById('volumeSlider');
            const rect = slider.getBoundingClientRect();
            const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
            const clickX = clientX - rect.left;
            const width = rect.width;
            const percentage = Math.max(0, Math.min(1, clickX / width));
            
            audioPlayer.volume = percentage;
            document.getElementById('volumeLevel').style.width = (percentage * 100) + '%';
        }
        
        function updateProgress(e) {
            if (currentTrackIndex === -1 || !audioPlayer.duration) return;
            const bar = document.getElementById('progressContainer');
            const rect = bar.getBoundingClientRect();
            const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
            const clickX = clientX - rect.left;
            const width = rect.width;
            const percentage = clickX / width;
            audioPlayer.currentTime = audioPlayer.duration * percentage;
        }
        
        // Volume Slider Events
        document.getElementById('volumeSlider').addEventListener('mousedown', (e) => {
            isDraggingVolume = true;
            updateVolume(e);
        });
        
        document.getElementById('volumeSlider').addEventListener('touchstart', (e) => {
            isDraggingVolume = true;
            updateVolume(e);
            e.preventDefault();
        });
        
        document.addEventListener('mousemove', (e) => {
            if (isDraggingVolume) {
                updateVolume(e);
            }
        });
        
        document.addEventListener('touchmove', (e) => {
            if (isDraggingVolume) {
                updateVolume(e);
            }
        }, { passive: false });
        
        document.addEventListener('mouseup', () => {
            isDraggingVolume = false;
        });
        
        document.addEventListener('touchend', () => {
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

        // Progress Bar Events (Click & Touch & Drag)
        document.getElementById('progressContainer').addEventListener('click', updateProgress);
        
        document.getElementById('progressContainer').addEventListener('touchstart', (e) => {
            updateProgress(e);
            e.preventDefault();
        });

        document.getElementById('progressContainer').addEventListener('mousedown', (e) => {
            isDraggingProgress = true;
            updateProgress(e);
        });

        document.addEventListener('mousemove', (e) => {
            if (isDraggingProgress) {
                updateProgress(e);
            }
        });

        document.addEventListener('mouseup', () => {
            isDraggingProgress = false;
        });

        document.addEventListener('touchmove', (e) => {
            if (isDraggingProgress) {
                updateProgress(e);
            }
        }, { passive: false });

        document.addEventListener('touchend', () => {
            isDraggingProgress = false;
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



@app.route("/rpc_state")
def rpc_state():
    if player_state["current_track_index"] == -1:
        return jsonify({"playing": False})

    song = playlist[player_state["current_track_index"]]

    return jsonify({
        "playing": player_state["is_playing"],
        "title": song["title"],
        "artist": song["artist"],
        "start": int(time.time()) - player_state["current_time"],
        "end": int(time.time()) - player_state["current_time"] + song["duration"]
    })


if __name__ == '__main__':
    threading.Thread(target=init_discord_rpc, daemon=True).start()
    
    print("🌍 Planetify startet...")
    print("✨ Öffne http://localhost:5000")
    print("📁 Musik-Ordner: ./music_library/")
    print("🎵 Viel Spaß!")
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)