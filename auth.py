#!/usr/bin/env python3
# this is the spotify authentication file. you need it to get access tokens for the spotify api
# to configure this with your own spotify, look at the spotdl documentation. You need a premium account
# but the setup is quite simple otherwise. It should create json files in the spotify folder automatically
from pathlib import Path
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

CLIENT_ID     = "your_client_id_here"
CLIENT_SECRET = "your_client_secret_here"
REDIRECT_URI  = "http://127.0.0.1:8888/callback" 
SCOPE         = "user-modify-playback-state user-read-playback-state playlist-read-private"
CACHE_PATH    = "/data/spotify/token_cache.json"

def main():
    auth_manager = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=CACHE_PATH,
        open_browser=True,            # ← launches your browser
        # requests_local_server=True  # ← spotipy v2.25+ enables this by default when open_browser=True
    )
    sp = Spotify(auth_manager=auth_manager)
    user = sp.current_user()         # ← this call triggers the OAuth dance
    print(f"\n✅ Authenticated as {user['display_name']}. Tokens cached to {CACHE_PATH}\n")

def get_auth():
    """
    Return a SpotifyOAuth instance for use by spotify_service.py.
    """
    return SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=CACHE_PATH,
        open_browser=True
    )

if __name__ == "__main__":
    main()
