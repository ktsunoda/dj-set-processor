"""Create a private Spotify playlist from a VirtualDJ set."""

import logging
import os

LOGGER = logging.getLogger(__name__)
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SETTINGS = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")


def normalized(value):
    return " ".join(value.casefold().split())


class SpotifyPlaylist:
    def __init__(self, session):
        self.session = session
        self.refresh_token = None

    def access_token(self):
        settings = [os.environ.get(name) for name in SETTINGS]
        if not all(settings):
            raise ValueError("Spotify credentials are incomplete")
        client_id, client_secret, configured_refresh_token = settings
        response = self.session.post(
            TOKEN_URL, auth=(client_id, client_secret),
            data={"grant_type": "refresh_token",
                  "refresh_token": self.refresh_token or configured_refresh_token},
            timeout=30,
        )
        if response.status_code != 200:
            LOGGER.error("Spotify token refresh failed (HTTP %s)", response.status_code)
            response.raise_for_status()
        tokens = response.json()
        self.refresh_token = tokens.get("refresh_token") or self.refresh_token
        return tokens["access_token"]

    def api(self, method, path, access_token, **kwargs):
        response = self.session.request(
            method, API + path, headers={"Authorization": f"Bearer {access_token}"},
            timeout=30, **kwargs,
        )
        if not response.ok:
            LOGGER.error("Spotify %s failed (HTTP %s)", method, response.status_code)
        response.raise_for_status()
        return response.json()

    def search(self, access_token, artist, title, remix):
        # Keep a named remix significant when resolving candidates.
        names = (f"{title} ({remix})", f"{title} - {remix}") if remix else (title,)
        for name in names:
            results = self.api(
                "GET", "/search", access_token,
                params={"q": f"track:{name} artist:{artist}", "type": "track", "limit": 10},
            )["tracks"]["items"]
            for item in results:
                artists = {normalized(entry["name"]) for entry in item["artists"]}
                candidate = normalized(item["name"])
                if candidate in {normalized(value) for value in names} and normalized(artist) in artists:
                    return item["uri"]
        return None

    def __call__(self, playlist, tracks):
        token = self.access_token()
        cache = {}
        uris = []
        for track in tracks:
            artist, title, remix = (
                track.get(field, "").strip() for field in ("artist", "title", "remix")
            )
            if not artist or not title:
                continue
            key = (artist, title, remix)
            if key not in cache:
                cache[key] = self.search(token, *key)
            if cache[key] is not None:
                uris.append(cache[key])
        unmatched = len(tracks) - len(uris)
        if not uris:
            LOGGER.error("Spotify found no matching tracks (%s unmatched)", unmatched)
            raise ValueError("Spotify found no matching tracks")
        result = self.api(
            "POST", "/me/playlists", token,
            json={"name": playlist.spotify_name, "public": False},
        )
        path = f"/playlists/{result['id']}/items"
        for start in range(0, len(uris), 100):
            self.api("POST", path, token, json={"uris": uris[start:start + 100]})["snapshot_id"]
        LOGGER.info("Spotify playlist created (%s added, %s unmatched)", len(uris), unmatched)
