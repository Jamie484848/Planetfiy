import os
import time
import json
import secrets
from flask import Flask, render_template_string, jsonify, request, send_from_directory, Response, session, g
from flask_babel import Babel, gettext as _, lazy_gettext as _l
from flask import render_template
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
    print("WARNUNG: mutagen nicht installiert. Metadaten-Erkennung deaktiviert.")
    print("   Installiere mit: pip install mutagen")

from flask_cors import CORS

# Neue Imports für URL-Downloads
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False
    print("WARNUNG: yt-dlp nicht installiert. URL-Downloads deaktiviert.")
    print("   Installiere mit: pip install yt-dlp")

try:
    import spotdl
    SPOTDL_AVAILABLE = True
except ImportError:
    SPOTDL_AVAILABLE = False
    print("WARNUNG: spotdl nicht installiert. Spotify-Downloads deaktiviert.")
    print("   Installiere mit: pip install spotdl")

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(16)
app.config['BABEL_DEFAULT_LOCALE'] = 'de'
app.config['BABEL_SUPPORTED_LOCALES'] = ['de', 'en']
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

babel = Babel(app)

def get_locale():
    """Determine the best match for supported languages."""
    if 'lang' in request.args:
        session['lang'] = request.args.get('lang')
    return session.get('lang', request.accept_languages.best_match(['de', 'en']))

babel.init_app(app, locale_selector=get_locale)

# Base URL für öffentliche Links (ändere dies für lokale Entwicklung)
BASE_URL = os.environ.get('BASE_URL', 'https://planetfiy.onrender.com')

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
SONGS_DB = os.path.join(os.path.dirname(__file__), 'songs_db.json')

# Listening Sessions
listening_sessions = {}
# Format: { 'session_id': { 'host': user_id, 'users': [user_ids], 'state': {...} } }

def load_songs_db():
    """Einfache und robuste Funktion zum Laden der Songs-Datenbank"""
    if os.path.exists(SONGS_DB):
        with open(SONGS_DB, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_songs_db(songs):
    with open(SONGS_DB, 'w', encoding='utf-8') as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)

# Playlist wird lazy geladen
playlist = None

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
        
        print(f"Metadaten geladen: {title} - {artist} ({duration}s)")
        
        return {
            'title': str(title),
            'artist': str(artist),
            'album': str(album),
            'duration': duration
        }
        
    except Exception as e:
        print(f"WARNUNG: Metadaten-Fehler für {filepath}: {e}")
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

def clean_print(text):
    """Entfernt Emojis aus Text für Windows-Kompatibilität"""
    import re
    # Entfernt alle Unicode-Emojis
    emoji_pattern = re.compile("["
                              u"\U0001F600-\U0001F64F"  # emoticons
                              u"\U0001F300-\U0001F5FF"  # symbols & pictographs
                              u"\U0001F680-\U0001F6FF"  # transport & map symbols
                              u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                              u"\U00002700-\U000027BF"  # dingbats
                              u"\U0001f926-\U0001f937"  # gestures
                              u"\U00010000-\U0010ffff"  # other unicode
                              u"\u2640-\u2642"  # gender symbols
                              u"\u2600-\u2B55"  # misc symbols
                              u"\u200d"  # zero width joiner
                              u"\u23cf"  # eject symbol
                              u"\u23e9"  # fast forward
                              u"\u231a"  # watch
                              u"\ufe0f"  # variation selector
                              u"\u3030"  # wavy dash
                              "]+", flags=re.UNICODE)
    return emoji_pattern.sub('', text)

def embed_cover_and_metadata(filepath, metadata, cover_url=None):
    """Bettet Cover und Metadaten in MP3-Datei ein"""
    try:
        import requests
        import re
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB

        audio = MP3(filepath, ID3=ID3)

        # Erstelle Tags falls keine vorhanden
        if audio.tags is None:
            audio.add_tags()
            tags_created = True
        else:
            tags_created = False

        # Cover nur hinzufügen wenn keins vorhanden ist oder URL angegeben
        has_cover = any('APIC' in str(key) for key in audio.tags.keys())
        if cover_url and not has_cover:
            try:
                print(f" Lade Cover herunter: {cover_url}")
                response = requests.get(cover_url, timeout=30)
                if response.status_code == 200:
                    cover_data = response.content

                    audio.tags.add(
                        APIC(
                            encoding=3,  # UTF-8
                            mime='image/jpeg',
                            type=3,  # Cover (front)
                            desc='Cover',
                            data=cover_data
                        )
                    )
                    print(" Cover eingebettet")
            except Exception as cover_error:
                print(f" Cover-Download fehlgeschlagen: {cover_error}")

        # Metadaten nur hinzufügen wenn Tags neu erstellt wurden
        if tags_created:
            if metadata.get('title'):
                audio.tags.add(TIT2(encoding=3, text=metadata['title']))
            if metadata.get('artist'):
                audio.tags.add(TPE1(encoding=3, text=metadata['artist']))
            if metadata.get('album'):
                audio.tags.add(TALB(encoding=3, text=metadata['album']))

            audio.save()
            print(" Metadaten gespeichert")
        else:
            print("ℹ Tags bereits vorhanden, überspringe Metadaten-Update")

    except Exception as e:
        print(f" Fehler beim Einbetten von Cover/Metadaten: {e}")

def download_from_url(url):
    """Lädt Audio von YouTube/Spotify/etc. herunter und gibt den Dateipfad zurück"""
    try:
        # Überprüfen ob es eine Spotify-URL ist
        if 'spotify.com' in url and SPOTDL_AVAILABLE:
            return download_from_spotify(url)
        elif YTDLP_AVAILABLE:
            return download_from_youtube(url)
        else:
            raise Exception("Keine geeignete Download-Bibliothek verfügbar")

    except Exception as e:
        print(f" Download-Fehler für {url}: {e}")
        raise Exception(f"Download fehlgeschlagen: {str(e)}")

def download_from_spotify(url):
    """Lädt Audio von Spotify mit spotdl herunter"""
    try:
        import subprocess
        import tempfile
        import shutil
        import sys
        import requests
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB

        print(f" Lade von Spotify herunter: {url}")

        # Temporäres Verzeichnis für den Download
        with tempfile.TemporaryDirectory() as temp_dir:
            # spotdl über python -m ausführen
            cmd = [
                sys.executable, '-m', 'spotdl', url,
                '--output', temp_dir,
                '--format', 'mp3',
                '--bitrate', '320k',
                '--threads', '1'
            ]

            print(f" Führe aus: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)  # 10 Minuten Timeout

            if result.returncode != 0:
                print(f" spotdl stderr: {result.stderr}")
                print(f" spotdl stdout: {result.stdout}")
                raise Exception(f"spotdl Download fehlgeschlagen: {result.stderr}")

            # Gefundene Dateien suchen
            downloaded_files = []
            for file in os.listdir(temp_dir):
                if file.endswith('.mp3'):
                    downloaded_files.append(os.path.join(temp_dir, file))

            if not downloaded_files:
                print(f" Inhalt von temp_dir: {os.listdir(temp_dir)}")
                raise Exception("Keine MP3-Datei wurde heruntergeladen")

            # Erste gefundene MP3-Datei verwenden
            temp_filepath = downloaded_files[0]

            # Metadaten aus der Datei extrahieren
            file_metadata = extract_metadata(temp_filepath)

            # Metadaten vorbereiten
            metadata = {
                'title': file_metadata['title'],
                'artist': file_metadata['artist'],
                'album': file_metadata['album'],
                'duration': file_metadata['duration']
            }

            # Versuche ein Cover für den Song zu finden
            cover_url = find_cover_for_song(metadata['title'], metadata['artist'])

            # Cover und Metadaten einbetten
            embed_cover_and_metadata(temp_filepath, metadata, cover_url)

            # Dateiname für die finale Datei erstellen
            safe_title = "".join(c for c in metadata['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
            if not safe_title:
                safe_title = f"spotify_{int(time.time())}"

            filename = f"{safe_title}.mp3"
            final_filepath = os.path.join(UPLOAD_FOLDER, filename)

            # Sicherstellen dass Dateiname eindeutig ist
            counter = 1
            base_name = filename
            while os.path.exists(final_filepath):
                name, ext = os.path.splitext(base_name)
                filename = f"{name}_{counter}{ext}"
                final_filepath = os.path.join(UPLOAD_FOLDER, filename)
                counter += 1

            # Datei in das Musik-Verzeichnis kopieren
            shutil.copy2(temp_filepath, final_filepath)

            print(f" Spotify Download abgeschlossen: {filename}")
            return final_filepath, metadata

    except subprocess.TimeoutExpired:
        raise Exception("Download-Zeitüberschreitung (10 Minuten)")
    except Exception as e:
        print(f" Spotify Download-Fehler: {e}")
        raise

def find_cover_for_song(title, artist):
    """Sucht nach einem Cover für einen Song basierend auf Titel und Artist"""
    try:
        import requests
        import re

        # Erstelle Suchbegriff für YouTube
        search_query = f"{title} {artist} official music video"
        search_query = re.sub(r'[^\w\s]', '', search_query)  # Entferne Sonderzeichen

        # YouTube Search API (kostenlos, kein API-Key nötig)
        search_url = f"https://www.youtube.com/results?search_query={search_query.replace(' ', '+')}"

        # Einfache HTML-Parsing (nicht ideal, aber funktioniert)
        response = requests.get(search_url, timeout=10)
        if response.status_code != 200:
            return None

        # Suche nach dem ersten Video-ID
        video_id_match = re.search(r'watch\?v=([a-zA-Z0-9_-]{11})', response.text)
        if video_id_match:
            video_id = video_id_match.group(1)
            # Hole Thumbnail
            thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

            # Teste ob Thumbnail existiert
            thumb_response = requests.head(thumbnail_url, timeout=5)
            if thumb_response.status_code == 200:
                return thumbnail_url

        return None

    except Exception as e:
        print(f" Cover-Suche fehlgeschlagen: {e}")
        return None

def download_from_youtube(url):
    """Lädt Audio von YouTube/etc. mit yt-dlp herunter"""
    try:
        import requests
        import re
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB

        # yt-dlp Optionen für Audio-Download
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': os.path.join(UPLOAD_FOLDER, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        print(f" Lade von YouTube herunter: {url}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Info extrahieren
            info = ydl.extract_info(url, download=False)

            # Sicherstellen dass es ein einzelnes Video ist
            if 'entries' in info:
                info = info['entries'][0]

            # Titel für Dateiname bereinigen
            title = info.get('title', 'Unknown')
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            if not safe_title:
                safe_title = f"download_{int(time.time())}"

            filename = f"{safe_title}.mp3"
            filepath = os.path.join(UPLOAD_FOLDER, filename)

            # Sicherstellen dass Dateiname eindeutig ist
            counter = 1
            base_name = filename
            while os.path.exists(filepath):
                name, ext = os.path.splitext(base_name)
                filename = f"{name}_{counter}{ext}"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                counter += 1

            # ydl_opts mit dem korrekten Dateinamen aktualisieren
            ydl_opts['outtmpl'] = filepath.replace('.mp3', '.%(ext)s')

            # Download durchführen
            with yt_dlp.YoutubeDL(ydl_opts) as ydl_download:
                ydl_download.download([url])

            # Metadaten vorbereiten
            metadata = {
                'title': info.get('title', safe_title),
                'artist': info.get('uploader', 'Unknown Artist'),
                'album': info.get('album', ''),
                'duration': info.get('duration', 180)
            }

            # Versuche besseres Cover zu bekommen
            cover_url = info.get('thumbnail')
            if cover_url:
                # Verwende maxresdefault für bessere Qualität
                video_id_match = re.search(r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})', url)
                if video_id_match:
                    video_id = video_id_match.group(1)
                    maxres_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
                    # Teste ob maxres verfügbar ist
                    try:
                        import requests
                        response = requests.head(maxres_url, timeout=5)
                        if response.status_code == 200:
                            cover_url = maxres_url
                    except:
                        pass  # Verwende ursprüngliches Thumbnail

            # Cover und Metadaten einbetten
            embed_cover_and_metadata(filepath, metadata, cover_url)

            print(f" YouTube Download abgeschlossen: {filename}")
            return filepath, metadata

    except Exception as e:
        print(f" YouTube Download-Fehler: {e}")
        raise

def init_discord_rpc():
    global rpc, rpc_connected
    try:
        rpc = Presence(CLIENT_ID)
        rpc.connect()
        rpc_connected = True
        print("Discord RPC verbunden!")
    except Exception as e:
        print(f"Discord RPC Fehler: {e}")
        rpc_connected = False

def update_discord_presence():
    global rpc, rpc_connected
    if not rpc_connected or rpc is None or player_state["current_track_index"] == -1:
        return
    
    try:
        current_track = playlist[player_state["current_track_index"]]
        
        # Erstelle automatisch eine Session-ID für "Mithören"
        # Diese wird im listening_sessions Dictionary gespeichert
        active_session_id = None
        
        # Suche nach aktiver Session oder erstelle eine neue
        for sid, session in listening_sessions.items():
            if len(session['users']) > 0:
                active_session_id = sid
                break
        
        # Button URLs
        website_url = BASE_URL
        listen_url = f"{BASE_URL}?session={active_session_id}" if active_session_id else BASE_URL
        
        if player_state["is_playing"]:
            state = f"von {current_track['artist']}"
            details = current_track['title']
            
            current_unix_time = int(time.time())
            elapsed_seconds = player_state["current_time"]
            total_duration = current_track["duration"]
            
            song_start_time = current_unix_time - elapsed_seconds
            song_end_time = song_start_time + total_duration
            
            print(f"Discord: {current_track['title']} | {elapsed_seconds}s / {total_duration}s")
            
            try:
                rpc.update(
                    details=details,
                    state=state,
                    start=song_start_time,
                    end=song_end_time,
                    large_image="planetify_logo",
                    large_text="Planetify",
                    small_image="play",
                    small_text="Wird abgespielt",
                    buttons=[
                        {"label": "Website", "url": website_url},
                        {"label": "Mithören", "url": listen_url}
                    ]
                )
            except (TypeError, Exception) as e:
                print(f" Discord Buttons Fehler: {e}")
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
            try:
                rpc.update(
                    details=f"{current_track['title']}",
                    state=f"von {current_track['artist']} • Pausiert",
                    large_image="planetify_logo",
                    large_text="Planetify",
                    small_image="pause",
                    small_text="Pausiert",
                    buttons=[
                        {"label": "Website", "url": website_url},
                        {"label": "Mithören", "url": listen_url}
                    ]
                )
            except (TypeError, Exception) as e:
                print(f" Discord Buttons Fehler: {e}")
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
    print(f" Session erstellt: {session_id}")

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
    
    print(f" User joined session {session_id}: {len(session['users'])} users")

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
            print(f" Session geschlossen: {session_id}")
        else:
            emit('user_left', {
                'user_count': len(session['users'])
            }, room=session_id)
            print(f" User left session {session_id}")

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
    print(f"▶ Sync play in session {session_id}")

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
    print(f"⏸ Sync pause in session {session_id}")

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

# HTML template moved to templates/index.html

@app.route('/')
def index():
    return render_template('index.html', BASE_URL=BASE_URL, MUTAGEN_AVAILABLE=MUTAGEN_AVAILABLE)

@app.route('/playlist')
def get_playlist():
    global playlist
    # Lazy loading der Playlist
    if playlist is None:
        playlist = load_songs_db()
        print(f"Loaded playlist with {len(playlist)} songs")
    return jsonify(playlist)

@app.route('/test')
def test():
    return "Hello World"

@app.route('/stream/<filename>')
def stream_music(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    mimetype, _ = mimetypes.guess_type(filename)
    if not mimetype:
        mimetype = 'audio/mpeg'
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, mimetype=mimetype)

@app.route('/cover/<filename>')
def get_cover(filename):
    """Extrahiert und gibt das Cover aus einer MP3-Datei zurück"""
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(filepath):
            # Erstelle ein dynamisches Cover für nicht gefundene Dateien
            return create_dynamic_cover("Cover nicht gefunden", "Planetify", 200, 200)

        from mutagen.mp3 import MP3
        audio = MP3(filepath)

        if audio.tags:
            # Suche nach APIC-Tag (Album Cover)
            for tag_name in audio.tags.keys():
                if 'APIC' in str(tag_name):
                    pic = audio.tags[tag_name]
                    return Response(pic.data, mimetype=f'image/{pic.mime.split("/")[1]}')

        # Fallback: Erstelle ein dynamisches Cover basierend auf dem Dateinamen
        song_title = filename.replace('.mp3', '').replace('_', ' ')
        return create_dynamic_cover(song_title, "Planetify", 200, 200)

    except Exception as e:
        print(f"Cover-Fehler für {filename}: {e}")
        # Bei Fehlern immer ein Cover zurückgeben
        return create_dynamic_cover("Fehler", "Planetify", 200, 200)

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
            
            global playlist
            # Stelle sicher, dass playlist geladen ist
            if playlist is None:
                playlist = load_songs_db()

            new_song = {
                'id': max([s['id'] for s in playlist] + [0]) + 1,
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
    # Stelle sicher, dass globale playlist mit Datenbank synchronisiert ist
    playlist = load_songs_db()
    print(f" {uploaded} Song(s) erfolgreich hochgeladen und Playlist aktualisiert")
    return jsonify({'success': True, 'uploaded': uploaded})

@app.route('/upload_url', methods=['POST'])
def upload_from_url():
    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'Keine URL angegeben'}), 400

    # Überprüfen ob URL von unterstützten Plattformen ist
    supported_domains = ['youtube.com', 'youtu.be', 'spotify.com', 'soundcloud.com', 'music.youtube.com']
    if not any(domain in url.lower() for domain in supported_domains):
        return jsonify({'error': 'Nicht unterstützte URL. Unterstützt: YouTube, Spotify, SoundCloud'}), 400

    # Spezielle Überprüfung für Spotify
    if 'spotify.com' in url.lower() and not SPOTDL_AVAILABLE:
        return jsonify({'error': 'Spotify-URLs benötigen spotdl. Installiere mit: pip install spotdl'}), 400

    try:
        # Download durchführen
        filepath, metadata = download_from_url(url)

        # Song zur Playlist hinzufügen
        global playlist
        # Stelle sicher, dass playlist geladen ist
        if playlist is None:
            playlist = load_songs_db()

        new_song = {
            'id': max([s['id'] for s in playlist] + [0]) + 1,
            'filename': os.path.basename(filepath),
            'title': metadata['title'],
            'artist': metadata['artist'],
            'album': metadata['album'],
            'duration': metadata['duration'],
            'cover_url': ''
        }

        playlist.append(new_song)
        save_songs_db(playlist)

        # Stelle sicher, dass globale playlist mit Datenbank synchronisiert ist
        playlist = load_songs_db()

        print(f" Song hinzugefügt: {metadata['title']} - {metadata['artist']}")
        return jsonify({
            'success': True,
            'song': new_song,
            'message': f"'{metadata['title']}' wurde erfolgreich hinzugefügt!"
        })

    except Exception as e:
        print(f" URL Upload Fehler: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/edit_song', methods=['POST'])
def edit_song():
    data = request.get_json()
    song_id = data['id']

    global playlist
    # Stelle sicher, dass playlist geladen ist
    if playlist is None:
        playlist = load_songs_db()

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

@app.route('/scan_metadata', methods=['POST'])
def scan_metadata():
    """Scannt alle Songs und aktualisiert Titel und Artist aus den Datei-Metadaten"""
    if not MUTAGEN_AVAILABLE:
        return jsonify({'error': 'Mutagen ist nicht installiert. Metadaten-Scan nicht verfügbar.'}), 400

    global playlist
    if playlist is None:
        playlist = load_songs_db()

    updated_count = 0
    errors = []

    for song in playlist:
        try:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], song['filename'])
            if os.path.exists(filepath):
                # Extrahiere frische Metadaten
                metadata = extract_metadata(filepath)

                # Aktualisiere Song-Daten nur wenn sich etwas geändert hat
                changed = False
                if song['title'] != metadata['title']:
                    song['title'] = metadata['title']
                    changed = True
                if song['artist'] != metadata['artist']:
                    song['artist'] = metadata['artist']
                    changed = True
                if song['album'] != metadata['album']:
                    song['album'] = metadata['album']
                    changed = True
                if song['duration'] != metadata['duration']:
                    song['duration'] = metadata['duration']
                    changed = True

                if changed:
                    updated_count += 1
                    print(f"✓ Aktualisiert: {song['title']} - {song['artist']}")
            else:
                errors.append(f"Datei nicht gefunden: {song['filename']}")

        except Exception as e:
            errors.append(f"Fehler bei {song['filename']}: {str(e)}")
            print(f"✗ Fehler bei {song['filename']}: {e}")

    # Speichere aktualisierte Datenbank
    if updated_count > 0:
        save_songs_db(playlist)

    return jsonify({
        'success': True,
        'updated': updated_count,
        'total': len(playlist),
        'errors': errors
    })









# ERSETZE die /share/ Route mit dieser Version für schöne Discord-Embeds

@app.route('/share/<int:song_id>')
def share_song(song_id):
    """Discord Rich Embed für ALLE Song-IDs"""
    global playlist
    if playlist is None:
        playlist = load_songs_db()

    song = next((s for s in playlist if s['id'] == song_id), None)
    if not song:
        return "Song nicht gefunden", 404

    # WICHTIG: Verwende die aktuelle song_id dynamisch
    share_url = f"{BASE_URL}/share/{song_id}"
    duration_str = f"{song['duration']//60}:{song['duration']%60:02d}"
    
    # Escape für HTML
    title = song['title'].replace('"', '&quot;').replace("'", '&#39;')
    artist = song['artist'].replace('"', '&quot;').replace("'", '&#39;')
    album = song.get('album', '').replace('"', '&quot;').replace("'", '&#39;')
    
    description = f"""🎵 {artist}{' • ' + album if album else ''} • {duration_str}

▶️ [Hörprobe anhören]({share_url}/preview)
🎧 [Vollversion abspielen]({share_url}/play)"""
    
    html = f"""<!DOCTYPE html>
<html lang="de" prefix="og: http://ogp.me/ns#">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    
    <title>{title} - {artist} | Planetify</title>
    
    <!-- Discord Rich Embed -->
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="Planetify">
    <meta property="og:title" content="{title}">
    <meta property="og:description" content="{description}">
    <meta property="og:url" content="{share_url}">
    <meta property="og:image" content="{share_url}/cover.jpg">
    <meta property="og:image:secure_url" content="{share_url}/cover.jpg">
    <meta property="og:image:type" content="image/jpeg">
    <meta property="og:image:width" content="512">
    <meta property="og:image:height" content="512">
    <meta property="og:image:alt" content="{title} - Cover">
    
    <!-- Theme Color für Discord (Orange) -->
    <meta name="theme-color" content="#ff6b00">
    
    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{title}">
    <meta name="twitter:description" content="🎵 {artist} • {duration_str}">
    <meta name="twitter:image" content="{share_url}/cover.jpg">
    
    <style>
        * {{margin:0;padding:0;box-sizing:border-box}}
        body {{
            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
            background:linear-gradient(135deg,#1a0f00 0%,#000 100%);
            color:#fff;
            min-height:100vh;
            display:flex;
            align-items:center;
            justify-content:center;
            padding:20px
        }}
        .card {{
            background:linear-gradient(135deg,#1a1a1a 0%,#0d0d0d 100%);
            border-radius:20px;
            padding:40px;
            max-width:500px;
            width:100%;
            box-shadow:0 20px 60px rgba(0,0,0,.8);
            border:1px solid #333;
            animation:fadeIn 0.5s ease-out
        }}
        @keyframes fadeIn {{from{{opacity:0;transform:translateY(20px)}}to{{opacity:1;transform:translateY(0)}}}}
        .cover {{
            width:100%;
            aspect-ratio:1;
            border-radius:12px;
            margin-bottom:24px;
            box-shadow:0 12px 40px rgba(255,107,0,.4);
            object-fit:cover;
            transition:transform 0.3s ease
        }}
        .cover:hover {{transform:scale(1.05)}}
        .title {{
            font-size:28px;
            font-weight:700;
            margin-bottom:8px;
            text-align:center;
            background:linear-gradient(135deg,#fff 0%,#b3b3b3 100%);
            -webkit-background-clip:text;
            -webkit-text-fill-color:transparent;
            line-height:1.3
        }}
        .artist {{
            font-size:18px;
            color:#b3b3b3;
            text-align:center;
            margin-bottom:8px
        }}
        .duration {{
            font-size:14px;
            color:#ff6b00;
            text-align:center;
            margin-bottom:32px;
            font-weight:600
        }}
        .buttons {{
            display:grid;
            gap:12px;
            margin-bottom:24px
        }}
        .btn {{
            padding:16px 24px;
            border:none;
            border-radius:12px;
            font-size:15px;
            font-weight:600;
            cursor:pointer;
            text-decoration:none;
            display:flex;
            align-items:center;
            justify-content:center;
            gap:10px;
            transition:all 0.3s;
            text-align:center
        }}
        .btn-preview {{
            background:linear-gradient(135deg,#ff6b00 0%,#ff3d00 100%);
            color:#fff;
            box-shadow:0 4px 20px rgba(255,107,0,.4);
            font-size:16px
        }}
        .btn-preview:hover {{
            transform:translateY(-2px);
            box-shadow:0 8px 30px rgba(255,107,0,.6)
        }}
        .btn-play {{
            background:rgba(255,255,255,0.1);
            color:#fff;
            border:1px solid rgba(255,255,255,0.2);
            backdrop-filter:blur(10px)
        }}
        .btn-play:hover {{
            background:rgba(255,255,255,0.15);
            border-color:rgba(255,255,255,0.3)
        }}
        .btn-home {{
            background:transparent;
            color:#b3b3b3;
            border:1px solid #333;
            font-size:13px
        }}
        .btn-home:hover {{
            color:#fff;
            border-color:#535353
        }}
        .logo {{
            text-align:center;
            margin-top:24px;
            font-size:18px;
            font-weight:700;
            background:linear-gradient(135deg,#ff6b00 0%,#ff3d00 100%);
            -webkit-background-clip:text;
            -webkit-text-fill-color:transparent;
            opacity:0.7
        }}
        .info {{
            background:rgba(255,107,0,0.1);
            border:1px solid rgba(255,107,0,0.3);
            border-radius:8px;
            padding:12px;
            margin-bottom:20px;
            font-size:13px;
            text-align:center;
            color:#ff6b00
        }}
    </style>
</head>
<body>
    <div class="card">
        <img src="{share_url}/cover.jpg" alt="{title}" class="cover">
        
        <div class="title">{title}</div>
        <div class="artist">🎵 {artist}</div>
        <div class="duration">⏱️ {duration_str}</div>
        
        <div class="info">
            🔊 Klicke auf die Buttons für Audio-Wiedergabe
        </div>
        
        <div class="buttons">
            <a href="{share_url}/preview" class="btn btn-preview">
                ▶️ 30-Sekunden Hörprobe
            </a>
            <a href="{share_url}/play" class="btn btn-play">
                💿 Vollversion abspielen
            </a>
            <a href="/" class="btn btn-home">
                🏠 Zurück zu Planetify
            </a>
        </div>
        
        <div class="logo">PLANETIFY</div>
    </div>
</body>
</html>"""
    
    return html, 200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }


@app.route('/share/<int:song_id>/preview')
def share_preview_page(song_id):
    """30-Sekunden Hörprobe mit Auto-Play - funktioniert für alle IDs"""
    global playlist
    if playlist is None:
        playlist = load_songs_db()

    song = next((s for s in playlist if s['id'] == song_id), None)
    if not song:
        return "Song nicht gefunden", 404

    share_url = f"{BASE_URL}/share/{song_id}"
    title = song['title'].replace('"', '&quot;')
    artist = song['artist'].replace('"', '&quot;')
    
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Hörprobe | Planetify</title>
    
    <!-- Discord Preview -->
    <meta property="og:type" content="website">
    <meta property="og:title" content="🔊 {title} - Hörprobe">
    <meta property="og:description" content="🎵 {artist} • 30 Sekunden Preview">
    <meta property="og:image" content="{share_url}/cover.jpg">
    <meta name="theme-color" content="#ff6b00">
    
    <style>
        * {{margin:0;padding:0;box-sizing:border-box}}
        body {{
            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
            background:linear-gradient(135deg,#1a0f00 0%,#000 100%);
            color:#fff;
            min-height:100vh;
            display:flex;
            align-items:center;
            justify-content:center;
            padding:20px
        }}
        .player {{
            background:linear-gradient(135deg,#1a1a1a 0%,#0d0d0d 100%);
            border-radius:20px;
            padding:40px;
            max-width:400px;
            width:100%;
            box-shadow:0 20px 60px rgba(0,0,0,.8);
            border:1px solid #333;
            text-align:center
        }}
        .cover {{
            width:200px;
            height:200px;
            border-radius:12px;
            margin:0 auto 24px;
            box-shadow:0 12px 40px rgba(255,107,0,.4);
            object-fit:cover;
            animation:pulse 2s infinite
        }}
        @keyframes pulse {{
            0%,100%{{transform:scale(1)}}
            50%{{transform:scale(1.05)}}
        }}
        h2 {{
            font-size:24px;
            margin-bottom:8px;
            background:linear-gradient(135deg,#fff 0%,#b3b3b3 100%);
            -webkit-background-clip:text;
            -webkit-text-fill-color:transparent
        }}
        .artist {{
            font-size:16px;
            color:#b3b3b3;
            margin-bottom:24px
        }}
        .label {{
            background:rgba(255,107,0,0.2);
            border:1px solid rgba(255,107,0,0.4);
            border-radius:8px;
            padding:12px;
            margin-bottom:20px;
            font-size:14px;
            color:#ff6b00;
            font-weight:600
        }}
        audio {{
            width:100%;
            margin-bottom:24px
        }}
        .btn {{
            display:inline-block;
            padding:12px 32px;
            background:linear-gradient(135deg,#ff6b00 0%,#ff3d00 100%);
            color:#fff;
            text-decoration:none;
            border-radius:24px;
            font-weight:600;
            transition:all 0.3s;
            margin:8px
        }}
        .btn:hover {{
            transform:translateY(-2px);
            box-shadow:0 8px 30px rgba(255,107,0,.5)
        }}
        .btn-secondary {{
            background:transparent;
            border:1px solid #535353
        }}
        .btn-secondary:hover {{
            border-color:#fff;
            box-shadow:0 4px 16px rgba(255,255,255,.2)
        }}
    </style>
</head>
<body>
    <div class="player">
        <img src="{share_url}/cover.jpg" alt="{title}" class="cover">
        <h2>{title}</h2>
        <div class="artist">🎵 {artist}</div>
        <div class="label">🔊 30-Sekunden Hörprobe</div>
        <audio controls autoplay>
            <source src="{share_url}/preview.mp3" type="audio/mpeg">
            Dein Browser unterstützt kein Audio.
        </audio>
        <a href="{share_url}/play" class="btn">💿 Vollversion</a><br>
        <a href="{share_url}" class="btn btn-secondary">← Zurück</a>
    </div>
</body>
</html>"""


@app.route('/share/<int:song_id>/play')
def share_play_page(song_id):
    """Vollversion - funktioniert für alle IDs"""
    global playlist
    if playlist is None:
        playlist = load_songs_db()

    song = next((s for s in playlist if s['id'] == song_id), None)
    if not song:
        return "Song nicht gefunden", 404

    share_url = f"{BASE_URL}/share/{song_id}"
    title = song['title'].replace('"', '&quot;')
    artist = song['artist'].replace('"', '&quot;')
    
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Vollversion | Planetify</title>
    
    <!-- Discord Preview -->
    <meta property="og:type" content="website">
    <meta property="og:title" content="💿 {title} - Vollversion">
    <meta property="og:description" content="🎵 {artist}">
    <meta property="og:image" content="{share_url}/cover.jpg">
    <meta name="theme-color" content="#ff6b00">
    
    <style>
        * {{margin:0;padding:0;box-sizing:border-box}}
        body {{
            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
            background:linear-gradient(135deg,#1a0f00 0%,#000 100%);
            color:#fff;
            min-height:100vh;
            display:flex;
            align-items:center;
            justify-content:center;
            padding:20px
        }}
        .player {{
            background:linear-gradient(135deg,#1a1a1a 0%,#0d0d0d 100%);
            border-radius:20px;
            padding:40px;
            max-width:450px;
            width:100%;
            box-shadow:0 20px 60px rgba(0,0,0,.8);
            border:1px solid #333;
            text-align:center
        }}
        .cover {{
            width:250px;
            height:250px;
            border-radius:12px;
            margin:0 auto 24px;
            box-shadow:0 12px 40px rgba(255,107,0,.4);
            object-fit:cover
        }}
        h2 {{
            font-size:26px;
            margin-bottom:8px;
            background:linear-gradient(135deg,#fff 0%,#b3b3b3 100%);
            -webkit-background-clip:text;
            -webkit-text-fill-color:transparent
        }}
        .artist {{
            font-size:18px;
            color:#b3b3b3;
            margin-bottom:24px
        }}
        .label {{
            background:rgba(255,107,0,0.2);
            border:1px solid rgba(255,107,0,0.4);
            border-radius:8px;
            padding:12px;
            margin-bottom:20px;
            font-size:14px;
            color:#ff6b00;
            font-weight:600
        }}
        audio {{
            width:100%;
            margin-bottom:24px
        }}
        .btn {{
            display:inline-block;
            padding:12px 32px;
            background:transparent;
            color:#fff;
            text-decoration:none;
            border-radius:24px;
            font-weight:600;
            transition:all 0.3s;
            margin:8px;
            border:1px solid #535353
        }}
        .btn:hover {{
            border-color:#fff;
            box-shadow:0 4px 16px rgba(255,255,255,.2)
        }}
    </style>
</head>
<body>
    <div class="player">
        <img src="{share_url}/cover.jpg" alt="{title}" class="cover">
        <h2>{title}</h2>
        <div class="artist">🎵 {artist}</div>
        <div class="label">💿 Vollversion</div>
        <audio controls autoplay>
            <source src="/stream/{song['filename']}" type="audio/mpeg">
            Dein Browser unterstützt kein Audio.
        </audio>
        <a href="{share_url}" class="btn">← Zurück zur Übersicht</a><br>
        <a href="/" class="btn">🏠 Zu Planetify</a>
    </div>
</body>
</html>"""


# Cover und Preview Routes bleiben gleich wie vorher
@app.route('/share/<int:song_id>/cover.jpg')
def share_cover(song_id):
    """Cover für ALLE Song-IDs"""
    global playlist
    if playlist is None:
        playlist = load_songs_db()

    song = next((s for s in playlist if s['id'] == song_id), None)
    if not song:
        return create_dynamic_cover("Nicht gefunden", "Planetify", 512, 512)

    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], song['filename'])
        if not os.path.exists(filepath):
            return create_dynamic_cover(song['title'], song['artist'], 512, 512)

        from mutagen.mp3 import MP3
        from PIL import Image
        import io

        audio = MP3(filepath)
        if audio.tags:
            for tag in audio.tags.keys():
                if 'APIC' in str(tag):
                    pic = audio.tags[tag]
                    img = Image.open(io.BytesIO(pic.data))
                    
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    img = img.resize((512, 512), Image.Resampling.LANCZOS)
                    
                    output = io.BytesIO()
                    img.save(output, format='JPEG', quality=95)
                    output.seek(0)
                    
                    return Response(
                        output.getvalue(),
                        mimetype='image/jpeg',
                        headers={'Cache-Control': 'public, max-age=86400'}
                    )

        return create_dynamic_cover(song['title'], song['artist'], 512, 512)

    except Exception as e:
        print(f"Cover error: {e}")
        return create_dynamic_cover(song['title'], song['artist'], 512, 512)


@app.route('/share/<int:song_id>/preview.mp3')
def share_preview_mp3(song_id):
    """Preview MP3 für ALLE Song-IDs"""
    global playlist
    if playlist is None:
        playlist = load_songs_db()

    song = next((s for s in playlist if s['id'] == song_id), None)
    if not song:
        return "Song nicht gefunden", 404

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], song['filename'])
    if not os.path.exists(filepath):
        return "Datei nicht gefunden", 404

    preview_path = os.path.join(app.config['UPLOAD_FOLDER'], f'.preview_{song["filename"]}')
    
    if os.path.exists(preview_path):
        return send_from_directory(
            os.path.dirname(preview_path),
            os.path.basename(preview_path),
            mimetype='audio/mpeg'
        )

    try:
        import subprocess
        import tempfile
        import shutil

        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
            temp_path = tmp.name

        cmd = [
            'ffmpeg', '-i', filepath,
            '-ss', '0', '-t', '30',
            '-acodec', 'libmp3lame',
            '-b:a', '128k',
            '-y', temp_path
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=60)

        if result.returncode == 0 and os.path.exists(temp_path):
            shutil.copy2(temp_path, preview_path)
            
            with open(temp_path, 'rb') as f:
                data = f.read()
            
            os.unlink(temp_path)
            
            return Response(
                data,
                mimetype='audio/mpeg',
                headers={'Cache-Control': 'public, max-age=86400'}
            )
        else:
            raise Exception(f"FFmpeg failed")

    except Exception as e:
        print(f"Preview error: {e}")
        return send_from_directory(
            app.config['UPLOAD_FOLDER'],
            song['filename'],
            mimetype='audio/mpeg'
        )


def create_dynamic_cover(title, artist, width=512, height=512):
    """Erstellt ein dynamisches Cover für Songs ohne Cover"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        # Erstelle ein Bild mit angegebener Größe
        img = Image.new('RGB', (width, height), color='#ff6b00')
        draw = ImageDraw.Draw(img)

        # Erstelle einen Gradient-Hintergrund
        for y in range(height):
            r = int(255 - (y / height) * 100)
            g = int(107 - (y / height) * 50)
            b = int(0 + (y / height) * 100)
            for x in range(width):
                draw.point((x, y), fill=(r, g, b))

        # Zeichne einen Musik-Emoji in die Mitte
        try:
            # Versuche eine große Schriftart zu verwenden
            font = ImageFont.truetype("arial.ttf", 200)
        except:
            # Fallback auf Default-Schriftart
            font = ImageFont.load_default()

        # Zentriere den Emoji
        emoji = "🎵"
        bbox = draw.textbbox((0, 0), emoji, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (width - text_width) // 2
        y = (height - text_height) // 2 - height * 0.1  # 10% über der Mitte

        draw.text((x, y), emoji, fill='white', font=font)

        # Füge Song-Info hinzu
        try:
            small_font = ImageFont.truetype("arial.ttf", 40)
        except:
            small_font = ImageFont.load_default()

        # Titel
        title_text = title[:20] + "..." if len(title) > 20 else title
        bbox = draw.textbbox((0, 0), title_text, font=small_font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        y_title = height * 0.68  # 68% der Höhe
        draw.text((x, y_title), title_text, fill='white', font=small_font)

        # Artist
        artist_text = artist[:25] + "..." if len(artist) > 25 else artist
        bbox = draw.textbbox((0, 0), artist_text, font=small_font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        y_artist = height * 0.78  # 78% der Höhe
        draw.text((x, y_artist), artist_text, fill='white', font=small_font)

        # Speichere als JPEG
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=95)
        output.seek(0)

        return Response(output.getvalue(), mimetype='image/jpeg')

    except ImportError:
        # Fallback ohne PIL
        return send_from_directory('static', 'default_cover.jpg', mimetype='image/jpeg')
    except Exception as e:
        print(f"Dynamic cover error: {e}")
        return send_from_directory('static', 'default_cover.jpg', mimetype='image/jpeg')


if __name__ == '__main__':
    threading.Thread(target=init_discord_rpc, daemon=True).start()
    
    print("Planetify startet...")
    print("Offne http://localhost:10000")
    print("Musik-Ordner: ./music_library/")
    print("Viel Spass!")
    
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False)