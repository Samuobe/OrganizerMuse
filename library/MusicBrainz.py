import os
import urllib.request
import configparser
import base64
import re
from pydub import AudioSegment
import musicbrainzngs
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TCON, TDRC, TDOR, TRCK, TPOS, TPUB, UFID, TXXX, APIC
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus


musicbrainzngs.set_useragent("MyMusicApp", "1.0", "your_email@example.com")


def sanitize_filename(name):
    if not name:
        return "Unknown"

    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def get_or_create_case_insensitive_dir(base_path, folder_name):

    clean_folder_name = sanitize_filename(folder_name)
    if not clean_folder_name:
        clean_folder_name = "Unknown"

    if os.path.exists(base_path):
        for entry in os.listdir(base_path):
            full_entry_path = os.path.join(base_path, entry)
            if os.path.isdir(full_entry_path) and entry.lower() == clean_folder_name.lower():
                return full_entry_path

    target_path = os.path.join(base_path, clean_folder_name)
    os.makedirs(target_path, exist_ok=True)
    return target_path


def search_album(title=None, artist=None, limit=5):
    query_parts = []
    if title:
        query_parts.append(f'release:"{title}"')
    if artist:
        query_parts.append(f'artist:"{artist}"')

    if not query_parts:
        return []

    query = " AND ".join(query_parts)
    raw_results = musicbrainzngs.search_releases(query=query, limit=limit)
    
    results_list = []
    for release in raw_results.get('release-list', []):
        album_name = release.get('title')
        artists = ", ".join([a['artist']['name'] for a in release.get('artist-credit', []) if isinstance(a, dict)])
        release_date = release.get('date', 'N/D')
        release_id = release.get("id") 

        results_list.append([release_id, album_name, artists, release_date])
        
    return results_list


def serch_id(release_id, search_type, includes_recording=False):
    if search_type.lower() == "release":
        includes = ["tags", "artist-credits", "labels"]
        if includes_recording:
            includes.append("recordings")
        result = musicbrainzngs.get_release_by_id(release_id, includes=includes)
        return result['release']
    elif search_type.lower() == "artist":
        result = musicbrainzngs.get_artist_by_id(release_id, includes=["tags"])
        return result['artist']
    elif search_type.lower() == "recording":
        result = musicbrainzngs.get_recording_by_id(release_id, includes=["tags"])
        return result['recording']
    else:
        return "Error, invalid research type"


def _extract_all_genres(mb_release):
    tags_found = []

    def _add_tags_from_obj(obj):
        for key in ['genre-list', 'tag-list', 'user-tag-list', 'user-genre-list']:
            for t in obj.get(key, []):
                name = t.get('name', '').title()
                count = int(t.get('count', 1)) if isinstance(t.get('count'), (int, str)) else 1
                if name and len(name) > 2:
                    tags_found.append((name, count))

    _add_tags_from_obj(mb_release)

    release_group_id = mb_release.get('release-group', {}).get('id')
    if release_group_id:
        try:
            rg = musicbrainzngs.get_release_group_by_id(release_group_id, includes=["tags", "genres"])['release-group']
            _add_tags_from_obj(rg)
        except Exception:
            pass

    artist_id = None
    if mb_release.get('artist-credit'):
        first_artist = mb_release['artist-credit'][0]
        if isinstance(first_artist, dict) and 'artist' in first_artist:
            artist_id = first_artist['artist'].get('id')

    if artist_id:
        try:
            art = musicbrainzngs.get_artist_by_id(artist_id, includes=["tags", "genres"])['artist']
            _add_tags_from_obj(art)
        except Exception:
            pass

    if not tags_found:
        return ""

    genre_scores = {}
    for name, count in tags_found:
        genre_scores[name] = genre_scores.get(name, 0) + count

    sorted_genres = sorted(genre_scores.keys(), key=lambda x: genre_scores[x], reverse=True)
    return ", ".join(sorted_genres[:3])



def process_and_tag_audio(mp3_file_path, track_title, artist_name, release_id, config_path="config.conf"):
    print(f"\n==========================================", flush=True)
    print(f"[PROCESS] Inizio elaborazione per: '{track_title}' - '{artist_name}'", flush=True)
    print(f"[PROCESS] File origine: {mp3_file_path}", flush=True)


    config = configparser.ConfigParser()
    if os.path.exists(config_path):
        config.read(config_path)
        base_download_path = config.get("PREFERENCES", "DownloadPath", fallback="./downloads")
        output_format = config.get("PREFERENCES", "OutputFormat", fallback="opus").lower()
        bitrate = config.get("PREFERENCES", "Bitrate", fallback="320k").lower()
    else:
        base_download_path = "./downloads"
        output_format = "opus"
        bitrate = "320k"

    print(f"[CONFIG] Base Path: {base_download_path} | Formato: {output_format.upper()} | Bitrate: {bitrate}", flush=True)


    album_title = ""
    album_artist = ""
    year = ""
    original_year = ""
    genre_string = ""
    track_number = ""
    total_tracks = ""
    disc_number = "1"
    total_discs = "1"
    label = ""
    barcode = ""
    release_group_id = ""
    recording_id = ""

    try:
        print(f"[MUSICBRAINZ] Download dati completi per Release ID: {release_id}...", flush=True)
        
        # RIMOSSO 'user-tags' PER EVITARE RICHIESTA DI LOGIN
        includes = ["recordings", "artist-credits", "tags", "release-groups", "media", "labels"]
        mb_release = musicbrainzngs.get_release_by_id(release_id, includes=includes)['release']

        album_title = mb_release.get('title', '')
        release_date = mb_release.get('date', '')
        year = release_date.split('-')[0] if release_date else ""
        barcode = mb_release.get('barcode', '')

        if mb_release.get('release-group'):
            release_group_id = mb_release['release-group'].get('id', '')
            first_release_date = mb_release['release-group'].get('first-release-date', '')
            original_year = first_release_date.split('-')[0] if first_release_date else year

        if mb_release.get('artist-credit'):
            album_artist = ", ".join([
                a['artist']['name'] for a in mb_release['artist-credit'] 
                if isinstance(a, dict) and 'artist' in a
            ])

        if mb_release.get('label-info-list'):
            labels = [
                l['label']['name'] for l in mb_release['label-info-list'] 
                if isinstance(l, dict) and 'label' in l and 'name' in l['label']
            ]
            if labels:
                label = labels[0]

        genre_string = _extract_all_genres(mb_release)

        # Matching flessibile per la traccia
        found_track = False
        media_list = mb_release.get('medium-list', [])
        total_discs = str(len(media_list)) if media_list else "1"

        clean_search_title = re.sub(r'[^\w\s]', '', track_title.lower()).strip()

        for medium in media_list:
            current_disc = str(medium.get('position', '1'))
            tracks = medium.get('track-list', [])
            track_count = str(len(tracks))

            for t in tracks:
                rec = t.get('recording', {})
                rec_title = rec.get('title', '') or t.get('title', '')
                clean_rec_title = re.sub(r'[^\w\s]', '', rec_title.lower()).strip()

                if (clean_search_title in clean_rec_title or clean_rec_title in clean_search_title):
                    track_number = str(t.get('number', ''))
                    total_tracks = track_count
                    disc_number = current_disc
                    recording_id = rec.get('id', '')
                    found_track = True
                    break
            if found_track:
                break

        print(f"[MUSICBRAINZ] Dati trovati -> Album: '{album_title}' | Anno: '{year}' | Traccia: '{track_number}/{total_tracks}'", flush=True)

    except Exception as e:
        print(f"[ERROR MusicBrainz] Errore recupero metadati: {e}", flush=True)


    target_artist_name = artist_name if artist_name else (album_artist if album_artist else "Unknown Artist")
    target_album_name = album_title if album_title else "Unknown Album"

    artist_dir = get_or_create_case_insensitive_dir(base_download_path, target_artist_name)
    album_dir = get_or_create_case_insensitive_dir(artist_dir, target_album_name)

    # Definizione del nome del file finale
    clean_track_title = sanitize_filename(track_title)
    if track_number and track_number.isdigit():
        file_name = f"{int(track_number):02d} - {clean_track_title}.{output_format}"
    else:
        file_name = f"{clean_track_title}.{output_format}"

    final_file_path = os.path.join(album_dir, file_name)


    cover_data = None
    try:
        print(f"[COVER] Scaricamento copertina...", flush=True)
        image_list = musicbrainzngs.get_image_list(release_id)
        if image_list.get('images'):
            front_image = next((img for img in image_list['images'] if img.get('front')), image_list['images'][0])
            cover_url = front_image.get('image')
            if cover_url:
                req = urllib.request.Request(cover_url, headers={'User-Agent': 'MyMusicApp/1.0'})
                with urllib.request.urlopen(req) as response:
                    cover_data = response.read()
                print(f"[COVER] Copertina scaricata ({len(cover_data)} bytes)", flush=True)
    except Exception as e:
        print(f"[WARNING Cover] Impossibile recuperare la copertina: {e}", flush=True)


    print(f"[AUDIO] Conversione e salvataggio in: {final_file_path}", flush=True)
    try:
        audio = AudioSegment.from_file(mp3_file_path)
        if output_format == "flac":
            audio.export(final_file_path, format="flac")
        else:
            audio.export(final_file_path, format=output_format, parameters=["-b:a", bitrate])
        print(f"[AUDIO] Conversione completata con successo.", flush=True)
    except Exception as e:
        print(f"[FATAL Audio] Errore durante la conversione audio: {e}", flush=True)
        raise e


    print(f"[TAGGING] Scrittura metadati Picard...", flush=True)
    final_artist = target_artist_name

    try:
        if output_format == "opus":
            f = OggOpus(final_file_path)
            f["title"] = track_title
            f["artist"] = final_artist
            if album_artist: f["albumartist"] = album_artist
            if album_title: f["album"] = album_title
            if genre_string: f["genre"] = genre_string
            if year: f["date"] = year
            if original_year: f["originaldate"] = original_year
            if track_number: f["tracknumber"] = track_number
            if total_tracks: f["tracktotal"] = total_tracks
            if disc_number: f["discnumber"] = disc_number
            if total_discs: f["disctotal"] = total_discs
            if label: f["organization"] = label
            if barcode: f["barcode"] = barcode

            f["musicbrainz_albumid"] = release_id
            if release_group_id: f["musicbrainz_releasegroupid"] = release_group_id
            if recording_id: f["musicbrainz_trackid"] = recording_id

            if cover_data:
                pic = Picture()
                pic.data = cover_data
                pic.type = 3
                pic.mime = "image/jpeg"
                f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode('ascii')]

            f.save()

        elif output_format == "flac":
            f = FLAC(final_file_path)
            f["title"] = track_title
            f["artist"] = final_artist
            if album_artist: f["albumartist"] = album_artist
            if album_title: f["album"] = album_title
            if genre_string: f["genre"] = genre_string
            if year: f["date"] = year
            if original_year: f["originaldate"] = original_year
            if track_number: f["tracknumber"] = track_number
            if total_tracks: f["totaltracks"] = total_tracks
            if disc_number: f["discnumber"] = disc_number
            if total_discs: f["totaldiscs"] = total_discs
            if label: f["label"] = label
            if barcode: f["barcode"] = barcode

            f["musicbrainz_albumid"] = release_id
            if release_group_id: f["musicbrainz_releasegroupid"] = release_group_id
            if recording_id: f["musicbrainz_trackid"] = recording_id

            if cover_data:
                pic = Picture()
                pic.data = cover_data
                pic.type = 3
                pic.mime = "image/jpeg"
                f.add_picture(pic)

            f.save()

        elif output_format in ["m4a", "mp4"]:
            f = MP4(final_file_path)
            f["\xa9nam"] = track_title
            f["\xa9ART"] = final_artist
            if album_artist: f["aART"] = album_artist
            if album_title: f["\xa9alb"] = album_title
            if genre_string: f["\xa9gen"] = genre_string
            if year: f["\xa9day"] = year

            if track_number and track_number.isdigit():
                tot = int(total_tracks) if total_tracks.isdigit() else 0
                f["trkn"] = [(int(track_number), tot)]

            if disc_number and disc_number.isdigit():
                tot_d = int(total_discs) if total_discs.isdigit() else 0
                f["disk"] = [(int(disc_number), tot_d)]

            f["----:com.apple.iTunes:MusicBrainz Album Id"] = release_id.encode('utf-8')

            if cover_data:
                f["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]

            f.save()

        elif output_format == "mp3":
            f = MP3(final_file_path, ID3=ID3)
            try:
                f.add_tags()
            except Exception:
                pass

            f.tags.add(TIT2(encoding=3, text=track_title))
            f.tags.add(TPE1(encoding=3, text=final_artist))
            if album_artist: f.tags.add(TPE2(encoding=3, text=album_artist))
            if album_title: f.tags.add(TALB(encoding=3, text=album_title))
            if genre_string: f.tags.add(TCON(encoding=3, text=genre_string))
            if year: f.tags.add(TDRC(encoding=3, text=year))
            if original_year: f.tags.add(TDOR(encoding=3, text=original_year))
            if label: f.tags.add(TPUB(encoding=3, text=label))

            if track_number:
                trck = f"{track_number}/{total_tracks}" if total_tracks else track_number
                f.tags.add(TRCK(encoding=3, text=trck))

            if disc_number:
                pos = f"{disc_number}/{total_discs}" if total_discs else disc_number
                f.tags.add(TPOS(encoding=3, text=pos))

            f.tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=release_id))
            if release_group_id:
                f.tags.add(TXXX(encoding=3, desc="MusicBrainz Release Group Id", text=release_group_id))
            if recording_id:
                f.tags.add(UFID(owner="http://musicbrainz.org", id=recording_id.encode('utf-8')))

            if cover_data:
                f.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))

            f.save()

        print(f"[TAGGING] Scrittura metadati completata.", flush=True)

    except Exception as e:
        print(f"[ERROR Tagging] Errore scrittura metadati: {e}", flush=True)
        raise e

    if os.path.exists(mp3_file_path) and os.path.abspath(mp3_file_path) != os.path.abspath(final_file_path):
        os.remove(mp3_file_path)
        print(f"[CLEANUP] Rimosso file temporaneo origine: {mp3_file_path}", flush=True)

    print(f"==========================================\n", flush=True)
    return final_file_path













































































