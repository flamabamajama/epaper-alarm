from pathlib import Path
import shutil
import subprocess
import threading
import logging
import json
import concurrent.futures
from spotipy import Spotify
from auth import get_auth

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

class SpotifyService:
    """
    Service to list and download Spotify playlists using yt-dlp for reliable track fetching.
    Now includes management of recent (downloaded) playlists and automatic pruning.
    """
    def __init__(self, music_dir: Path | str = None, recent_limit: int = 3):
        logger.debug("Initializing SpotifyService")
        self.sp = Spotify(
            auth_manager=get_auth(),
            requests_timeout=5,   # die fast on bad network
            retries=1,            # tiny retry
            status_retries=0,     # optional
            backoff_factor=0.1,   # optional
        )
        self.sp = Spotify(auth_manager=get_auth())
        # Base folder for downloads
        self.music_dir = Path(music_dir or "/data/Music/alarm_tracks")
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.recent_limit = recent_limit
        self.recent_file = self.music_dir / "recent_playlists.json"
        self.playlists: list[dict] = []
        self.selected_index = 0
        # Load or init recent list
        self.recent_playlists = self._load_recent()

    def _load_playlists(self) -> None:
        logger.debug("Fetching playlists from Spotify API")
        try:
            resp = self.sp.current_user_playlists(limit=50)
            self.playlists = resp.get("items", [])
            while resp.get("next"):
                resp = self.sp.next(resp)
                self.playlists.extend(resp.get("items", []))
            logger.debug(f"Loaded {len(self.playlists)} playlists")
        except Exception as e:
            logger.error(f"[Spotify] Failed to load playlists: {e}")
            raise
    def enter_playlist_menu(self) -> list[tuple[str, str]]:
        """
        Refresh the playlist cache when entering selection menu.
        """
        self._load_playlists()
        return self.list_playlists()

    def list_playlists(self) -> list[tuple[str, str]]:
        """
        Return cached playlist name/id pairs.
        """
        return [(p["name"], p["id"]) for p in self.playlists]

    def select(self, idx: int) -> tuple[str, str]:
        """
        Select a playlist by index (wrap-around).
        """
        pairs = self.list_playlists()
        self.selected_index = idx % len(pairs)
        name, pid = pairs[self.selected_index]
        logger.debug(f"Selected playlist [{self.selected_index}]: {name} ({pid})")
        return name, pid

    def is_downloaded(self, pid: str) -> bool:
        """
        Return True if any tracks for this playlist are already downloaded.
        """
        folder = self.music_dir / pid
        return folder.exists() and any(folder.glob("*.wav"))

    # --- Recent playlists cache ---
    def _load_recent(self):
        if self.recent_file.exists():
            try:
                with open(self.recent_file) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load recent file: {e}")
        return []

    def _save_recent(self):
        with open(self.recent_file, "w") as f:
            json.dump(self.recent_playlists, f)

    def update_recent(self, playlist_id: str):
        if playlist_id in self.recent_playlists:
            self.recent_playlists.remove(playlist_id)
        self.recent_playlists.insert(0, playlist_id)
        self._save_recent()
        self.enforce_limit()

    def enforce_limit(self):
        while len(self.recent_playlists) > self.recent_limit:
            old = self.recent_playlists.pop()
            folder = self.music_dir / old
            if folder.exists():
                logger.info(f"Removing old playlist: {old}")
                shutil.rmtree(folder, ignore_errors=True)
        self._save_recent()

    # --- Downloading and deleting ---
    def download_playlist(self, pid: str, name: str) -> threading.Thread:
        """
        Download all tracks in the given playlist via yt-dlp.
        Uses ytsearch1 to fetch the top YouTube Music result for each track.
        Also tracks download in recent_playlists, and removes old playlists if over limit.
        """
        final_dir = self.music_dir / pid
        final_dir.mkdir(parents=True, exist_ok=True)

        def _worker():
            logger.debug(f"Starting download for playlist: {name} ({pid})")
            # Fetch track metadata (id, title, artist)
            try:
                resp = self.sp.playlist_items(
                    pid,
                    fields="items(track(id,name,artists(name))),next",
                    limit=5
                )
            except Exception as e:
                logger.error(f"[Spotify] Failed to list playlist items: {e}")
                return  # exits thread; UI will see not-downloaded and fall back

            tracks = []
            try:
                for item in resp.get("items", [])[:5]:
                    t = item.get("track") or {}
                    tid = t.get("id")
                    title = t.get("name")
                    artists = t.get("artists") or []
                    artist = artists[0]["name"] if artists else ""
                    if tid and title:
                        tracks.append({"id": tid, "title": title, "artist": artist})
                while resp.get("next"):
                    resp = self.sp.next(resp)
                    for item in resp.get("items", []):
                        t = item.get("track") or {}
                        tid = t.get("id")
                        title = t.get("name")
                        artists = t.get("artists") or []
                        artist = artists[0]["name"] if artists else ""
                        if tid and title:
                            tracks.append({"id": tid, "title": title, "artist": artist})
                logger.debug(f"Found {len(tracks)} tracks in playlist '{name}'")
            except Exception as e:
                logger.error(f"[Spotify] Error while paging playlist items: {e}")
                return

            def fetch_one(tr):
                artist = tr["artist"]
                title  = tr["title"]
                query  = f"{artist} - {title} official audio"
                outfile = final_dir / f"{artist} - {title}.m4a"

                cmd = [
                    "yt-dlp",
                    "--default-search", "ytsearch1",
                    "-f", "bestaudio[ext=m4a]/bestaudio",
                    "-x", "--audio-format", "wav",
                    "-o", str(outfile),
                    query,
                ]

                # Run and capture everything—nothing will print to your console/display
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                if result.returncode != 0:
                    # Log the error so you can inspect it in your logs, but don't dump to screen
                    logger.error(
                        f"Failed to download [{artist} - {title}]: "
                        f"{result.stderr.strip().splitlines()[-1]}"
                    )
                else:
                    logger.debug(f"Downloaded: {artist} - {title}")

            # Parallelize up to 4 downloads at once
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                pool.map(fetch_one, tracks)

            logger.info(f"Completed download for playlist: {name}")
            # Update recents and prune old
            self.update_recent(pid)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread

    def get_local_tracks(self, pid: str = None) -> list[Path]:
        """
        Return sorted list of .m4a and .mp3 files in the playlist folder.
        """
        if pid is None:
            _, pid = self.list_playlists()[self.selected_index]
        folder = self.music_dir / pid
        return sorted(folder.glob("*.m4a")) + sorted(folder.glob("*.mp3"))

    def delete_playlist(self, pid: str):
        """
        Delete all downloaded tracks for the given playlist ID.
        """
        folder = self.music_dir / pid
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        if pid in self.recent_playlists:
            self.recent_playlists.remove(pid)
            self._save_recent()
