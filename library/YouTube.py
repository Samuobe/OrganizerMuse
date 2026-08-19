import os
import yt_dlp
from yt_dlp import YoutubeDL


def _get_base_options(download_mp3=False, output_folder="."):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'skip_download': not download_mp3,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web', 'tv'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept-Language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7',
        }
    }
    
    if download_mp3:
        opts.update({
            'quiet': False,
            'format': 'bestaudio/worstvideo+bestaudio/best',
            'retries': 10,
            'fragment_retries': 10,
            'retry_sleep_functions': {'http': lambda n: 2},
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
            'outtmpl': os.path.join(output_folder, '%(title)s.%(ext)s'),
        })
    return opts


def search_videos(query, n=5):
    opts = _get_base_options(download_mp3=False)
    search_query = f"ytsearch{n}:{query}"
    
    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(search_query, download=False)
            results = []
            for entry in info.get('entries', []) if info else []:
                if entry:
                    v_id = entry.get('id')
                    v_url = f"https://www.youtube.com/watch?v={v_id}"
                    results.append({
                        'id': v_id,
                        'title': entry.get('title'),
                        'url': v_url,
                        'duration_sec': entry.get('duration')
                    })
            return results
        except Exception as e:
            err_msg = f"[Errore ricerca YouTube]: {e}"
            print(err_msg, flush=True)
            return []


def search_by_id(video_id):
    opts = _get_base_options(download_mp3=False)
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return {
                'id': info.get('id'),
                'title': info.get('title'),
                'channel': info.get('uploader'),
                'url': url,
                'duration_sec': info.get('duration'),
                'views': info.get('view_count')
            }
        except Exception as e:
            err_msg = f"[Errore lettura dettagli video {video_id}]: {e}"
            print(err_msg, flush=True)
            return None


def download_by_id(video_id, output_folder="./downloads"):
    url = f"https://www.youtube.com/watch?v={video_id}"
    os.makedirs(output_folder, exist_ok=True)
    
    opts = _get_base_options(download_mp3=True, output_folder=output_folder)

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        
        # Sostituiamo l'estensione originale con .mp3 (creata da FFmpegExtractAudio)
        mp3_path = os.path.splitext(filename)[0] + ".mp3"
        
        abs_path = os.path.abspath(mp3_path)
        log_msg = f"[DEBUG YOUTUBE] MP3 scaricato in: {abs_path}"
        print(log_msg, flush=True)
        return abs_path