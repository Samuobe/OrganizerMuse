import json
import re
import tempfile
import threading
import queue
import time
import configparser
import os
from flask import Flask, request, render_template, Response, stream_with_context, redirect, url_for
from library.MusicBrainz import search_album as MB_search_album
from library.MusicBrainz import serch_id as MB_serch_id
from library.MusicBrainz import process_and_tag_audio
from library.YouTube import search_videos, download_by_id

app = Flask(__name__)


def worker_download_process(items, album_id, artist_name, msg_queue):
    total_tracks = len(items)
    if total_tracks == 0:
        msg_queue.put({'msg': 'Nessuna traccia selezionata.', 'error': True, 'completed': True})
        return

    msg_queue.put({'msg': 'Avvio download e elaborazione album...', 'progress': 0})

    config = configparser.ConfigParser()
    config.read('config.conf')
    download_path = config.get("PREFERENCES", "DownloadPath", fallback="./downloads")
    
    temp_dir = os.path.join(download_path, "_temp")
    os.makedirs(temp_dir, exist_ok=True)

    for idx, item in enumerate(items, 1):
        title = item.get("title")
        video_id = item.get("video_id")
        progress = int(((idx - 1) / total_tracks) * 100)

        if not video_id:
            msg_queue.put({'msg': f'[{idx}/{total_tracks}] Saltata: {title} (ID mancante)', 'progress': progress})
            continue

        msg_queue.put({'msg': f'[{idx}/{total_tracks}] Scaricamento: {title}...', 'progress': progress})
        try:
            downloaded_mp3_path = download_by_id(video_id, output_folder=temp_dir)
            print(f"[DEBUG APP] Percorso restituito da download_by_id: {downloaded_mp3_path}", flush=True)

            if downloaded_mp3_path and os.path.exists(downloaded_mp3_path):
                if album_id:
                    msg_queue.put({'msg': f'[{idx}/{total_tracks}] Conversione, tag e organizzazione cartelle: {title}...', 'progress': progress})
                    try:
                        final_file = process_and_tag_audio(
                            mp3_file_path=downloaded_mp3_path,
                            track_title=title,
                            artist_name=artist_name,
                            release_id=album_id,
                            config_path="config.conf"
                        )
                        print(f"[DEBUG APP] Elaborazione e salvataggio completati: {final_file}", flush=True)
                    except Exception as tag_error:
                        print(f"[ERROR APP] Tagging/Organizzazione falliti su [{title}]: {tag_error}", flush=True)
                        msg_queue.put({'msg': f'[{idx}/{total_tracks}] Errore tagging {title}: {str(tag_error)}', 'progress': progress})
                else:
                    print(f"[WARNING APP] album_id non presente (valore: '{album_id}'). Salto tagging e conversione.", flush=True)
                    msg_queue.put({'msg': f'[{idx}/{total_tracks}] Tagging saltato (album_id mancante)', 'progress': progress})
        except Exception as e:
            print(f"[ERROR APP] Download fallito [{title}]: {e}", flush=True)
            msg_queue.put({'msg': f'Errore su {title}: {str(e)}', 'progress': progress})
            
    try:
        if os.path.exists(temp_dir) and not os.listdir(temp_dir):
            os.rmdir(temp_dir)
    except Exception:
        pass

    msg_queue.put({'msg': 'Download e organizzazione completati con successo!', 'progress': 100, 'completed': True})


@app.route('/')
def index():
    title = request.args.get('title')
    artist = request.args.get('artist')
    results = MB_search_album(title=title, artist=artist) if title or artist else []
    return render_template('index.html', results=results)

@app.route("/release_info")  
def release_info():
    album_id = request.args.get('id')
    search_engine = request.args.get('search_engine')
    return render_template("release_info.html", album_id=album_id, search_engine=search_engine)

@app.route("/stream_search")
def stream_search():
    album_id = request.args.get('id')
    search_engine = request.args.get('search_engine')

    def generate():
        payload_status = json.dumps({'type': 'status', 'msg': 'Recupero informazioni da MusicBrainz...'})
        yield f"data: {payload_status}\n\n"
        
        tracks = []
        artist_name = ""

        if search_engine == "MB":
            data = MB_serch_id(album_id, "Release", includes_recording=True)
            if data:
                artist_name = data.get("artist-credit", [{}])[0].get("artist", {}).get("name", "")
                for medium in data.get("medium-list", []):
                    for track in medium.get("track-list", []):
                        title = track.get("recording", {}).get("title")
                        if title:
                            tracks.append(title)

        total_tracks = len(tracks)
        if total_tracks == 0:
            payload_err = json.dumps({'type': 'error', 'msg': "Nessuna traccia trovata nell'album."})
            yield f"data: {payload_err}\n\n"
            return

        payload_init = json.dumps({'type': 'init', 'total': total_tracks, 'artist_name': artist_name, 'album_id': album_id})
        yield f"data: {payload_init}\n\n"

        for idx, title in enumerate(tracks, 1):
            query = f"{artist_name} {title}".strip()
            payload_prog = json.dumps({'type': 'progress', 'current': idx, 'total': total_tracks, 'msg': f'Ricerca YouTube per: {title}'})
            yield f"data: {payload_prog}\n\n"
            
            candidates = search_videos(query, n=5)

            track_payload = {
                'title': title,
                'candidates': candidates
            }

            payload_track = json.dumps({'type': 'track', 'index': idx - 1, 'track': track_payload})
            yield f"data: {payload_track}\n\n"
            
            time.sleep(0.5)

        payload_done = json.dumps({'type': 'complete', 'msg': 'Ricerca completata!'})
        yield f"data: {payload_done}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route("/download")
def download_page():
    album_id = request.args.get('id') or request.args.get('album_id')
    artist_name = request.args.get('artist_name', '')
    return render_template("download.html", album_id=album_id, artist_name=artist_name)


@app.route("/stream_download", methods=["POST"])
def stream_download():
    payload = request.get_json() or {}
    items = payload.get("items", [])
    album_id = payload.get("album_id")
    artist_name = payload.get("artist_name", "")

    msg_queue = queue.Queue()

    t = threading.Thread(
        target=worker_download_process,
        args=(items, album_id, artist_name, msg_queue),
        daemon=True
    )
    t.start()

    def generate():
        """Legge dalla coda e trasmette all'HTML finché la connessione è attiva."""
        while True:
            try:
                data_item = msg_queue.get(timeout=1.0)
                payload_str = json.dumps(data_item)
                yield f"data: {payload_str}\n\n"

                if data_item.get('completed'):
                    break
            except queue.Empty:
                if not t.is_alive():
                    break
                yield ": keep-alive\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route("/save_settings")
def save_settings():
    config = configparser.ConfigParser()
    if os.path.exists("config.conf"):
        try:
            config.read('config.conf')
        except configparser.Error:
            pass

    if not config.has_section('PREFERENCES'):
        config.add_section('PREFERENCES')

    download_path = request.args.get("DownloadPath")
    output_format = request.args.get("OutputFormat")
    bitrate = request.args.get("Bitrate")
    port = request.args.get("Port")

    if download_path:
        config['PREFERENCES']['DownloadPath'] = download_path
    if output_format:
        config['PREFERENCES']['OutputFormat'] = output_format.lower()
    if bitrate:
        config['PREFERENCES']['Bitrate'] = bitrate.lower()
    if port and port.isdigit():
        config['PREFERENCES']['Port'] = port

    with open('config.conf', 'w') as configfile:
        config.write(configfile)
    
    return redirect("/")


@app.route("/settings")
def settings():
    config = configparser.ConfigParser()
    if os.path.exists("config.conf"):
        try:
            config.read('config.conf')
        except configparser.Error:
            pass

    download_path = config.get("PREFERENCES", "DownloadPath", fallback="./downloads")
    output_format = config.get("PREFERENCES", "OutputFormat", fallback="opus")
    bitrate = config.get("PREFERENCES", "Bitrate", fallback="320k")
    port = config.get("PREFERENCES", "Port", fallback="5090")

    return render_template(
        "settings.html", 
        download_path=download_path, 
        output_format=output_format,
        bitrate=bitrate,
        port=port
    )


if __name__ == '__main__':
    DEFAULT_CONFIG = {
        'PREFERENCES': {
            'DownloadPath': './downloads',
            'OutputFormat': 'opus',
            'Bitrate': '320k',
            'Port': '5090'
        }
    }

    def sync_config():
        config = configparser.ConfigParser()
        
        if os.path.exists("config.conf"):
            try:
                config.read("config.conf")
            except configparser.Error:
                pass

        has_changes = False

        for section, keys in DEFAULT_CONFIG.items():
            if not config.has_section(section) and section != 'DEFAULT':
                config.add_section(section)
                has_changes = True

            for key, default_value in keys.items():
                if not config.has_option(section, key):
                    config[section][key] = default_value
                    has_changes = True

        if has_changes or not os.path.exists("config.conf"):
            with open("config.conf", 'w') as configfile:
                config.write(configfile)

        return config

    cfg = sync_config()
    server_port = cfg.getint("PREFERENCES", "Port", fallback=5090)
    
    print(f"[SERVER] Avvio di Flask sulla porta: {server_port}", flush=True)
    app.run(debug=True, host="0.0.0.0", port=server_port)