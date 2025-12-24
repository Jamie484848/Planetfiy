import os
import time
import json
import secrets
from flask import Flask, render_template_string, jsonify, request, send_from_directory
from pypresence import Presence
import threading
from werkzeug.utils import secure_filename
import mimetypes
from flask_socketio import SocketIO, emit, join_room, leave_room

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

from flask_cors import CORS

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(16)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

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

# Listening Sessions
listening_sessions = {}
# Format: { 'session_id': { 'host': user_id, 'users': [user_ids], 'state': {...} } }

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
            filename = os.path.basename(filepath)
            title = os.path.splitext(filename)[0]
            return {
                'title': title,
                'artist': 'Unbekannter Artist',
                'album': '',
                'duration': get_audio_duration_fallback(filepath)
            }
        
        title = None
        if hasattr(audio, 'tags') and audio.tags:
            title = (audio.tags.get('title') or 
                    audio.tags.get('TIT2') or 
                    audio.tags.get('\xa9nam'))
            
            if title and isinstance(title, list):
                title = title[0]
        
        if not title:
            title = os.path.splitext(os.path.basename(filepath))[0]
        
        artist = None
        if hasattr(audio, 'tags') and audio.tags:
            artist = (audio.tags.get('artist') or 
                     audio.tags.get('TPE1') or 
                     audio.tags.get('\xa9ART'))
            
            if artist and isinstance(artist, list):
                artist = artist[0]
        
        if not artist:
            artist = 'Unbekannter Artist'
        
        album = None
        if hasattr(audio, 'tags') and audio.tags:
            album = (audio.tags.get('album') or 
                    audio.tags.get('TALB') or 
                    audio.tags.get('\xa9alb'))
            
            if album and isinstance(album, list):
                album = album[0]
        
        if not album:
            album = ''
        
        duration = 180
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
        filename = os.path.basename(filepath)
        title = os.path.splitext(filename)[0]
        return {
            'title': title,
            'artist': 'Unbekannter Artist',
            'album': '',
            'duration': get_audio_duration_fallback(filepath)
        }

def get_audio_duration_fallback(filepath):
    try:
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        return int(size_mb * 60)
    except:
        return 180

def get_audio_duration(filepath):
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

# WebSocket Events für Sync-Listening
@socketio.on('create_session')
def handle_create_session(data):
    session_id = secrets.token_urlsafe(16)
    user_id = request.sid
    
    listening_sessions[session_id] = {
        'host': user_id,
        'users': [user_id],
        'state': {
            'current_track_index': -1,
            'current_time': 0,
            'is_playing': False
        }
    }
    
    join_room(session_id)
    emit('session_created', {'session_id': session_id, 'is_host': True})
    print(f"📻 Session erstellt: {session_id}")

@socketio.on('join_session')
def handle_join_session(data):
    session_id = data.get('session_id')
    user_id = request.sid
    
    if session_id not in listening_sessions:
        emit('error', {'message': 'Session nicht gefunden'})
        return
    
    session = listening_sessions[session_id]
    
    if user_id not in session['users']:
        session['users'].append(user_id)
    
    join_room(session_id)
    
    # Sende aktuelle Session-Daten
    emit('session_joined', {
        'session_id': session_id,
        'is_host': user_id == session['host'],
        'state': session['state'],
        'user_count': len(session['users'])
    })
    
    # Benachrichtige alle in der Session
    emit('user_joined', {
        'user_count': len(session['users'])
    }, room=session_id)
    
    print(f"👤 User joined session {session_id}: {len(session['users'])} users")

@socketio.on('leave_session')
def handle_leave_session(data):
    session_id = data.get('session_id')
    user_id = request.sid
    
    if session_id in listening_sessions:
        session = listening_sessions[session_id]
        
        if user_id in session['users']:
            session['users'].remove(user_id)
        
        leave_room(session_id)
        
        # Wenn Host verlässt, Session schließen
        if user_id == session['host']:
            emit('session_closed', {}, room=session_id)
            del listening_sessions[session_id]
            print(f"🔒 Session geschlossen: {session_id}")
        else:
            emit('user_left', {
                'user_count': len(session['users'])
            }, room=session_id)
            print(f"👋 User left session {session_id}")

@socketio.on('sync_play')
def handle_sync_play(data):
    session_id = data.get('session_id')
    
    if session_id not in listening_sessions:
        return
    
    session = listening_sessions[session_id]
    
    # Nur Host kann steuern
    if request.sid != session['host']:
        return
    
    session['state']['current_track_index'] = data.get('track_index', session['state']['current_track_index'])
    session['state']['current_time'] = data.get('time', 0)
    session['state']['is_playing'] = True
    
    # Sende an alle in der Session
    emit('sync_state', session['state'], room=session_id)
    print(f"▶️ Sync play in session {session_id}")

@socketio.on('sync_pause')
def handle_sync_pause(data):
    session_id = data.get('session_id')
    
    if session_id not in listening_sessions:
        return
    
    session = listening_sessions[session_id]
    
    # Nur Host kann steuern
    if request.sid != session['host']:
        return
    
    session['state']['current_time'] = data.get('time', session['state']['current_time'])
    session['state']['is_playing'] = False
    
    emit('sync_state', session['state'], room=session_id)
    print(f"⏸️ Sync pause in session {session_id}")

@socketio.on('sync_seek')
def handle_sync_seek(data):
    session_id = data.get('session_id')
    
    if session_id not in listening_sessions:
        return
    
    session = listening_sessions[session_id]
    
    # Nur Host kann steuern
    if request.sid != session['host']:
        return
    
    session['state']['current_time'] = data.get('time', 0)
    
    emit('sync_state', session['state'], room=session_id)

@socketio.on('sync_time_update')
def handle_sync_time_update(data):
    session_id = data.get('session_id')
    
    if session_id not in listening_sessions:
        return
    
    session = listening_sessions[session_id]
    
    # Nur Host kann Zeit-Updates senden
    if request.sid != session['host']:
        return
    
    session['state']['current_time'] = data.get('time', 0)
    
    # Sende an alle außer Host
    emit('sync_time_update', {'time': data.get('time', 0)}, room=session_id, skip_sid=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    user_id = request.sid
    
    # Entferne User aus allen Sessions
    for session_id, session in list(listening_sessions.items()):
        if user_id in session['users']:
            session['users'].remove(user_id)
            
            if user_id == session['host']:
                emit('session_closed', {}, room=session_id)
                del listening_sessions[session_id]
            else:
                emit('user_left', {'user_count': len(session['users'])}, room=session_id)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Planetify - Deine Musik</title>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
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

        .sync-section {
            margin-top: auto;
            padding-top: 24px;
            border-top: 1px solid #1a1a1a;
        }

        .sync-btn {
            width: 100%;
            background: linear-gradient(135deg, #ff6b00 0%, #ff3d00 100%);
            color: #fff;
            border: none;
            padding: 12px;
            border-radius: 24px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            margin-bottom: 8px;
        }

        .sync-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 30px rgba(255, 107, 0, 0.5);
        }

        .sync-status {
            text-align: center;
            font-size: 12px;
            color: #b3b3b3;
            padding: 8px;
        }

        .sync-active {
            background: rgba(88, 101, 242, 0.15);
            border-radius: 8px;
            padding: 12px;
            font-size: 13px;
            color: #5865F2;
            margin-top: 8px;
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

        .control-btn:disabled {
            opacity: 0.3;
            cursor: not-allowed;
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

        .link-display {
            background: #0a0a0a;
            padding: 12px;
            border-radius: 8px;
            border: 1px solid #333;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .link-display input {
            flex: 1;
            background: transparent;
            border: none;
            color: #fff;
            font-size: 13px;
            font-family: monospace;
        }

        .copy-btn {
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            color: #fff;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }

        .copy-btn:hover {
            transform: scale(1.05);
        }

        .confirm-dialog {
            text-align: center;
            padding: 20px 0;
        }

        .confirm-dialog p {
            font-size: 15px;
            color: #b3b3b3;
            margin-bottom: 24px;
            line-height: 1.6;
        }

        .host-badge {
            display: inline-block;
            background: linear-gradient(135deg, #ff6b00, #ff3d00);
            color: #fff;
            font-size: 10px;
            padding: 4px 8px;
            border-radius: 12px;
            font-weight: 700;
            margin-left: 8px;
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

            <div class="sync-section">
                <button class="sync-btn" onclick="createListeningSession()">
                    🎧 Session erstellen
                </button>
                <div class="sync-status" id="syncStatus">Nicht verbunden</div>
                <div id="syncActive" style="display: none;"></div>
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
                <button class="control-btn" id="prevBtn" onclick="previousTrack()">⏮️</button>
                <button class="control-btn play-pause-btn" id="playPauseBtn" onclick="togglePlay()">▶️</button>
                <button class="control-btn" id="nextBtn" onclick="nextTrack()">⏭️</button>
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
    </div>

    <!-- Session Create Modal -->
    <div class="modal" id="sessionModal">
        <div class="modal-content">
            <div class="modal-header">Listening Session erstellen</div>
            <p style="color: #b3b3b3; margin-bottom: 20px;">
                Erstelle eine Session und teile den Link mit deinen Freunden, um gemeinsam Musik zu hören!
            </p>
            <div class="link-display" id="sessionLinkDisplay" style="display: none;">
                <input type="text" id="sessionLink" readonly>
                <button class="copy-btn" onclick="copySessionLink()">Kopieren</button>
            </div>
            <div class="modal-buttons">
                <button class="modal-btn modal-btn-cancel" onclick="closeSessionModal()">Abbrechen</button>
                <button class="modal-btn modal-btn-submit" id="createSessionBtn" onclick="confirmCreateSession()">
                    Session erstellen
                </button>
            </div>
        </div>
    </div>

    <!-- Join Session Modal -->
    <div class="modal" id="joinModal">
        <div class="modal-content">
            <div class="modal-header">Session beitreten</div>
            <div class="confirm-dialog">
                <p>
                    Du wurdest eingeladen, einer Listening Session beizutreten.<br>
                    <strong>Bist du sicher, dass du beitreten möchtest?</strong>
                </p>
                <p style="font-size: 13px; color: #666;">
                    Der Host steuert die Wiedergabe für alle Teilnehmer.
                </p>
            </div>
            <div class="modal-buttons">
                <button class="modal-btn modal-btn-cancel" onclick="closeJoinModal()">Nein</button>
                <button class="modal-btn modal-btn-submit" onclick="confirmJoinSession()">Ja, beitreten</button>
            </div>
        </div>
    </div>

    <!-- Upload Modal -->
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

    <!-- Edit Modal -->
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

        // Socket.IO Connection
        const socket = io();

        // Sync-Listening State
        let syncState = {
            active: false,
            sessionId: null,
            isHost: false,
            userCount: 0
        };

        let pendingSessionId = null;

        // Socket.IO Event Handlers
        socket.on('connect', () => {
            console.log('✅ WebSocket verbunden');
        });

        socket.on('session_created', (data) => {
            syncState.active = true;
            syncState.sessionId = data.session_id;
            syncState.isHost = data.is_host;
            syncState.userCount = 1;

            updateSyncUI();
            
            const link = `${window.location.origin}?session=${data.session_id}`;
            document.getElementById('sessionLink').value = link;
            document.getElementById('sessionLinkDisplay').style.display = 'flex';
            document.getElementById('createSessionBtn').style.display = 'none';
            
            console.log('📻 Session erstellt:', data.session_id);
        });

        socket.on('session_joined', (data) => {
            syncState.active = true;
            syncState.sessionId = data.session_id;
            syncState.isHost = data.is_host;
            syncState.userCount = data.user_count;

            updateSyncUI();
            closeJoinModal();

            // Sync mit aktuellem Status
            if (data.state.current_track_index >= 0) {
                console.log('🔄 Syncing with host state:', data.state);
                syncWithState(data.state);
            } else {
                console.log('⏸️ Session beigetreten, warte auf Host...');
            }

            console.log('👥 Session beigetreten:', data.session_id);
        });

        socket.on('user_joined', (data) => {
            syncState.userCount = data.user_count;
            updateSyncUI();
            console.log('➕ User beigetreten:', data.user_count);
        });

        socket.on('user_left', (data) => {
            syncState.userCount = data.user_count;
            updateSyncUI();
            console.log('➖ User verlassen:', data.user_count);
        });

        socket.on('session_closed', () => {
            alert('🔒 Die Session wurde vom Host geschlossen.');
            leaveSession();
        });

        socket.on('sync_state', (state) => {
            if (!syncState.isHost) {
                syncWithState(state);
            }
        });

        socket.on('sync_time_update', (data) => {
            if (!syncState.isHost && currentTrackIndex >= 0) {
                // Nur synchronisieren wenn Unterschied größer als 3 Sekunden
                const timeDiff = Math.abs(audioPlayer.currentTime - data.time);
                if (timeDiff > 3) {
                    console.log(`⏱️ Time drift detected: ${timeDiff}s, syncing to ${data.time}s`);
                    audioPlayer.currentTime = data.time;
                }
            }
        });

        socket.on('error', (data) => {
            alert('❌ Fehler: ' + data.message);
        });

        // Sync Functions
        function createListeningSession() {
            document.getElementById('sessionModal').classList.add('active');
            document.getElementById('sessionLinkDisplay').style.display = 'none';
            document.getElementById('createSessionBtn').style.display = 'block';
        }

        function confirmCreateSession() {
            socket.emit('create_session', {});
        }

        function closeSessionModal() {
            document.getElementById('sessionModal').classList.remove('active');
        }

        function copySessionLink() {
            const linkInput = document.getElementById('sessionLink');
            linkInput.select();
            document.execCommand('copy');
            
            const btn = event.target;
            btn.textContent = '✓ Kopiert!';
            setTimeout(() => {
                btn.textContent = 'Kopieren';
            }, 2000);
        }

        function checkForSessionInURL() {
            const urlParams = new URLSearchParams(window.location.search);
            const sessionId = urlParams.get('session');
            
            if (sessionId) {
                pendingSessionId = sessionId;
                document.getElementById('joinModal').classList.add('active');
            }
        }

        function confirmJoinSession() {
            if (pendingSessionId) {
                socket.emit('join_session', { session_id: pendingSessionId });
                // Remove session from URL
                window.history.replaceState({}, document.title, window.location.pathname);
            }
        }

        function closeJoinModal() {
            document.getElementById('joinModal').classList.remove('active');
            pendingSessionId = null;
            window.history.replaceState({}, document.title, window.location.pathname);
        }

        function leaveSession() {
            if (syncState.sessionId) {
                socket.emit('leave_session', { session_id: syncState.sessionId });
            }
            
            syncState.active = false;
            syncState.sessionId = null;
            syncState.isHost = false;
            syncState.userCount = 0;
            
            updateSyncUI();
            enablePlayerControls();
        }

        function syncWithState(state) {
            console.log('🔄 Syncing state:', state);
            
            if (state.current_track_index >= 0 && state.current_track_index < playlist.length) {
                if (state.current_track_index !== currentTrackIndex) {
                    console.log('🎵 Playing track:', state.current_track_index);
                    playTrack(state.current_track_index, true);
                    
                    // Warte bis Track geladen ist, dann sync Zeit
                    audioPlayer.addEventListener('loadedmetadata', () => {
                        setTimeout(() => {
                            console.log('⏱️ Setting initial time to:', state.current_time);
                            audioPlayer.currentTime = state.current_time;
                        }, 100);
                    }, { once: true });
                } else {
                    // Gleicher Track, nur Zeit synchronisieren
                    const timeDiff = Math.abs(audioPlayer.currentTime - state.current_time);
                    if (timeDiff > 1) {
                        console.log(`⏱️ Syncing time: ${audioPlayer.currentTime}s -> ${state.current_time}s (diff: ${timeDiff}s)`);
                        audioPlayer.currentTime = state.current_time;
                    }
                }
            }

            // Update play/pause state
            setTimeout(() => {
                if (state.is_playing && !isPlaying) {
                    console.log('▶️ Syncing play state');
                    audioPlayer.play().catch(e => console.log('Sync play error:', e));
                    isPlaying = true;
                    document.getElementById('playPauseBtn').textContent = '⏸️';
                } else if (!state.is_playing && isPlaying) {
                    console.log('⏸️ Syncing pause state');
                    audioPlayer.pause();
                    isPlaying = false;
                    document.getElementById('playPauseBtn').textContent = '▶️';
                }
            }, 200);
        }

        function updateSyncUI() {
            const statusEl = document.getElementById('syncStatus');
            const activeEl = document.getElementById('syncActive');
            
            if (syncState.active) {
                const hostBadge = syncState.isHost ? '<span class="host-badge">HOST</span>' : '';
                statusEl.textContent = '';
                activeEl.innerHTML = `
                    <div class="sync-active">
                        🎧 Session aktiv ${hostBadge}<br>
                        <small>${syncState.userCount} ${syncState.userCount === 1 ? 'Person' : 'Personen'} hört zu</small><br>
                        <button class="sync-btn" style="margin-top: 8px; font-size: 12px; padding: 8px;" onclick="leaveSession()">
                            Verlassen
                        </button>
                    </div>
                `;
                activeEl.style.display = 'block';

                // Disable controls for non-hosts
                if (!syncState.isHost) {
                    disablePlayerControls();
                } else {
                    enablePlayerControls();
                }
            } else {
                statusEl.textContent = 'Nicht verbunden';
                activeEl.style.display = 'none';
                enablePlayerControls();
            }
        }

        function disablePlayerControls() {
            document.getElementById('prevBtn').disabled = true;
            document.getElementById('nextBtn').disabled = true;
            document.getElementById('playPauseBtn').disabled = true;
            document.getElementById('progressContainer').style.pointerEvents = 'none';
            
            // Disable song cards
            document.querySelectorAll('.song-card').forEach(card => {
                card.style.pointerEvents = 'none';
                card.style.opacity = '0.5';
            });
        }

        function enablePlayerControls() {
            document.getElementById('prevBtn').disabled = false;
            document.getElementById('nextBtn').disabled = false;
            document.getElementById('playPauseBtn').disabled = false;
            document.getElementById('progressContainer').style.pointerEvents = 'auto';
            
            document.querySelectorAll('.song-card').forEach(card => {
                card.style.pointerEvents = 'auto';
                card.style.opacity = '1';
            });
        }

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

        function playTrack(index, isSync = false) {
            currentTrackIndex = index;
            const track = playlist[index];
            
            audioPlayer.src = `/stream/${track.filename}`;
            audioPlayer.load();
            
            audioPlayer.addEventListener('loadedmetadata', () => {
                const realDuration = Math.floor(audioPlayer.duration);
                audioPlayer.play().catch(e => console.log('Playback error:', e));
                isPlaying = true;
                document.getElementById('playPauseBtn').textContent = '⏸️';
                updatePlayerInfo(track, realDuration);

                // Sync with session - Host sendet an alle
                if (syncState.active && syncState.isHost && !isSync) {
                    console.log('📡 Broadcasting play to session');
                    socket.emit('sync_play', {
                        session_id: syncState.sessionId,
                        track_index: index,
                        time: 0
                    });
                }
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

                // Sync with session
                if (syncState.active && syncState.isHost) {
                    console.log('📡 Broadcasting pause to session');
                    socket.emit('sync_pause', {
                        session_id: syncState.sessionId,
                        time: Math.floor(audioPlayer.currentTime)
                    });
                }
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

                // Sync with session
                if (syncState.active && syncState.isHost) {
                    console.log('📡 Broadcasting play to session');
                    socket.emit('sync_play', {
                        session_id: syncState.sessionId,
                        track_index: currentTrackIndex,
                        time: Math.floor(audioPlayer.currentTime)
                    });
                }
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
            if (syncState.active && !syncState.isHost) return; // Only host can seek
            
            const bar = e.currentTarget;
            const rect = bar.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const width = rect.width;
            const percentage = clickX / width;
            audioPlayer.currentTime = audioPlayer.duration * percentage;

            // Sync seek with session
            if (syncState.active && syncState.isHost) {
                socket.emit('sync_seek', {
                    session_id: syncState.sessionId,
                    time: Math.floor(audioPlayer.currentTime)
                });
            }
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
        let syncInterval = null;

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

                    // Sync time with session (nur Host sendet)
                    if (syncState.active && syncState.isHost && isPlaying) {
                        socket.emit('sync_time_update', {
                            session_id: syncState.sessionId,
                            time: Math.floor(audioPlayer.currentTime)
                        });
                    }
                }
            }
        });

        audioPlayer.addEventListener('ended', () => {
            nextTrack();
        });

        audioPlayer.addEventListener('error', (e) => {
            console.error('Audio error:', e);
        });

        // Initialize
        loadPlaylist();
        audioPlayer.volume = 0.7;
        checkForSessionInURL();
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
    print("✨ Öffne http://localhost:10000")
    print("📁 Musik-Ordner: ./music_library/")
    print("🎵 Viel Spaß!")
    
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False)