"""Live MusicBrainz-powered backend for AlbumHintz.

Run: python3 server.py
Open: http://127.0.0.1:8787
"""
from __future__ import annotations

import json
import random
import re
import time
from threading import Lock
from urllib.error import HTTPError, URLError
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
HEADERS = {"User-Agent": "AlbumHintz/1.0 (local music recommender; contact: local@localhost)", "Accept": "application/json"}
TAGS = ["pop", "rock", "indie", "electronic", "jazz", "folk", "hip-hop", "r&b", "soul", "metal", "classical", "ambient", "dance", "punk", "alternative", "soundtrack", "mandopop", "cantopop", "c-pop"]
JSON_CACHE: dict[str, tuple[float, dict]] = {}
# Release IDs already served for a given set of filters. This prevents the
# random button from repeatedly landing on the same small handful of results.
SERVED_RELEASES: dict[str, set[str]] = {}
MINIMUM_RATING_COUNT = 100
DISCOGS_LOCK = Lock()
LAST_DISCOGS_REQUEST = 0.0


class UpstreamUnavailable(Exception):
    """An upstream catalogue was temporarily unavailable after retries."""


def get_json(url: str, cache_seconds: int = 900) -> dict:
    cached = JSON_CACHE.get(url)
    if cached and time.time() - cached[0] < cache_seconds:
        return cached[1]
    # MusicBrainz occasionally sheds short bursts of traffic with 503.  Retrying
    # here (rather than immediately surfacing that raw error in the UI) also
    # makes its public API gentler to use.
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            JSON_CACHE[url] = (time.time(), data)
            return data
        except HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504}:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() else 1.1 * (2 ** attempt)
        except (URLError, TimeoutError) as error:
            last_error = error
            wait = 1.1 * (2 ** attempt)
        if attempt < 3:
            time.sleep(wait)
    raise UpstreamUnavailable("音乐资料服务暂时繁忙，已自动重试。请稍后再试。") from last_error


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def discogs_json(url: str) -> dict:
    """Use Discogs' public API at a steady pace; cached calls return instantly."""
    global LAST_DISCOGS_REQUEST
    cached = JSON_CACHE.get(url)
    if cached and time.time() - cached[0] < 21600:
        return cached[1]
    with DISCOGS_LOCK:
        cached = JSON_CACHE.get(url)
        if cached and time.time() - cached[0] < 21600:
            return cached[1]
        pause = 1.05 - (time.monotonic() - LAST_DISCOGS_REQUEST)
        if pause > 0:
            time.sleep(pause)
        data = get_json(url, 21600)
        LAST_DISCOGS_REQUEST = time.monotonic()
        return data


def discogs_rating(artist: str, title: str) -> tuple[float | None, int]:
    """Return the rating of the most-rated matching Discogs release edition."""
    params = urllib.parse.urlencode({
        "artist": artist,
        "release_title": title,
        "type": "release",
        "per_page": 50,
    })
    try:
        results = discogs_json(f"https://api.discogs.com/database/search?{params}").get("results", [])
    except (HTTPError, URLError, TimeoutError, UpstreamUnavailable):
        # Metadata still comes from MusicBrainz even if Discogs is briefly down.
        return None, 0
    target_artist, target_title = normalized(artist), normalized(title)
    candidates = []
    for item in results:
        item_title = item.get("title", "")
        item_artist, _, item_album = item_title.partition(" - ")
        # Discogs may add translations in parentheses. Keep close title/artist
        # matches and skip clearly unofficial bootlegs before asking for ratings.
        album_match = target_title and (target_title in normalized(item_album) or normalized(item_album) in target_title)
        artist_match = not target_artist or target_artist in normalized(item_artist) or normalized(item_artist) in target_artist
        descriptions = " ".join(desc for fmt in item.get("formats", []) for desc in fmt.get("descriptions", []))
        if album_match and artist_match and "unofficial" not in descriptions.casefold():
            candidates.append(item)

    # The API's search order is not a rating order. Inspect a bounded set of
    # exact editions, then deliberately use the one with the largest vote count.
    best_average: float | None = None
    best_count = -1
    def fetch_rating(item: dict) -> tuple[float | None, int]:
        try:
            detail = discogs_json(f"https://api.discogs.com/releases/{item['id']}")
            rating = detail.get("community", {}).get("rating", {})
            average = rating.get("average")
            return (float(average), int(rating.get("count") or 0)) if average is not None else (None, 0)
        except (HTTPError, URLError, TimeoutError, UpstreamUnavailable):
            return None, 0

    # This is deliberately sequential: parallel calls trigger Discogs' public
    # API throttle and end up considerably slower than a small, paced batch.
    for item in candidates[:6]:
        average, count = fetch_rating(item)
        if average is not None and count > best_count:
            best_average, best_count = average, count
    return (round(best_average, 1), best_count) if best_average is not None else (None, 0)


def candidate(tag: str, minimum: float, maximum: float, region: str, year_min: int, year_max: int, allow_repeat: bool = True) -> dict | None:
    # Release search supports country directly, so the regional switch is backed
    # by official release-country records rather than language heuristics.
    # Fixed representative countries make each genre/region query cacheable;
    # changing albums thereafter is handled locally from the cached result page.
    if region == "chinese":
        # MusicBrainz indexes release text language in ISO 639-3. This makes
        # the Chinese mode language-based, regardless of where it was issued.
        country_query = "(lang:zho OR lang:cmn OR lang:yue OR lang:nan OR lang:hak OR lang:wuu)"
        region_key = "chinese-languages"
    else:
        country_query = "(country:US OR country:GB OR country:CA OR country:AU OR country:NZ OR country:IE)"
        region_key = "western-english-markets"
    # An exact-year search needs a purpose-built candidate page. Previously we
    # fetched a generic genre page and discarded almost all 30 entries locally.
    # Asking the index for that year keeps the same single request/cache path
    # while providing a much deeper useful pool for a one-year selection.
    date_clause = f" AND date:{year_min}*" if year_min == year_max else ""
    # Filter at the search index rather than after receiving a page packed with
    # alternate CD/vinyl editions and compilations of the same few albums.
    query = urllib.parse.quote(f'tag:"{tag}" AND {country_query} AND primarytype:album AND NOT secondarytype:compilation{date_clause}')
    # Fifty album-first candidates provide real variety while retaining one
    # request and the existing preloaded five-card queue.
    limit = 100 if year_min == year_max else 50
    url = f"https://musicbrainz.org/ws/2/release?query={query}&fmt=json&limit={limit}&inc=release-groups"
    releases = get_json(url).get("releases", [])
    # Some old MusicBrainz records index a bare year rather than a full date.
    # Retry that spelling only when the fast prefix form produces nothing.
    if not releases and year_min == year_max:
        fallback_query = urllib.parse.quote(f'tag:"{tag}" AND {country_query} AND primarytype:album AND NOT secondarytype:compilation AND date:{year_min}')
        fallback_url = f"https://musicbrainz.org/ws/2/release?query={fallback_query}&fmt=json&limit=100&inc=release-groups"
        releases = get_json(fallback_url).get("releases", [])
    # One release per release group avoids surfacing the same album again as a
    # different CD/vinyl/digital edition.
    unique_releases: dict[str, dict] = {}
    for release in releases:
        group = release.get("release-group", {})
        # Exclude compilations and singles/EPs. The picker is intentionally
        # album-only, even when a genre search otherwise returns them.
        if group.get("primary-type") != "Album" or "Compilation" in group.get("secondary-types", []):
            continue
        group_id = group.get("id") or release.get("id", "")
        unique_releases.setdefault(group_id, release)
    eligible = []
    for release in unique_releases.values():
        date = release.get("date", "")
        year = int(date[:4]) if re.match(r"^(19|20)\d{2}", date) else 0
        if year_min <= year <= year_max:
            eligible.append(release)

    served_key = f"{tag.casefold()}|{region_key}|{year_min}|{year_max}|{minimum:.1f}|{maximum:.1f}"
    served = SERVED_RELEASES.setdefault(served_key, set())
    choices = [release for release in eligible if release.get("id") not in served]
    if not choices:
        if not allow_repeat:
            return None
        served.clear()
        choices = eligible[:]
    # Vary the whole eligible pool. The served set guarantees each album is
    # used once before the next shuffle, instead of cycling a few top hits.
    random.shuffle(choices)

    def album_payload(release: dict) -> dict:
        date = release.get("date", "")
        year = int(date[:4]) if re.match(r"^(19|20)\d{2}", date) else 0
        group = release.get("release-group", {})
        group_id = group.get("id", "")
        artist = ", ".join(item.get("name", "") for item in release.get("artist-credit", []))
        return {
            "title": release.get("title", "Untitled"),
            "artist": artist,
            "year": str(year) if year else "",
            "rating": "",
            "count": "",
            "cover": f"https://coverartarchive.org/release/{release['id']}/front-250",
            "cover_fallback": f"https://coverartarchive.org/release-group/{group_id}/front-250" if group_id else "",
            "musicbrainz": f"https://musicbrainz.org/release/{release['id']}",
        }

    # Keep recommendation itself to one MusicBrainz request. Discogs is useful
    # for deliberate research, but its public rate limit is unsuitable for an
    # instant random-picker interaction.
    if choices:
        release = choices[0]
        served.add(release.get("id", ""))
        return album_payload(release)
    return None


class App(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path)
        try:
            if path.path == "/api/genres":
                self.send_json({"genres": TAGS})
                return
            if path.path == "/api/pick":
                query = urllib.parse.parse_qs(path.query)
                tag = query.get("genre", [""])[0].strip()
                if not tag:
                    self.send_json({"error": "genre is required"}, 400)
                    return
                minimum = max(0, min(5, float(query.get("min", [0])[0])))
                maximum = max(minimum, min(5, float(query.get("max", [5])[0])))
                year_min = max(1900, min(2026, int(query.get("year_min", [1900])[0])))
                year_max = max(year_min, min(2026, int(query.get("year_max", [2026])[0])))
                region = query.get("region", ["intl"])[0]
                albums = []
                # Return a short ready-to-preload queue. MusicBrainz data is
                # already cached after the first lookup, so this costs almost
                # nothing server-side and makes subsequent picks immediate.
                for index in range(5):
                    # A short queue must contain distinct albums. If its pool
                    # is exhausted, finish the queue instead of duplicating the
                    # last card and making “next” appear broken.
                    album = candidate(tag, minimum, maximum, region, year_min, year_max, allow_repeat=index == 0)
                    if not album:
                        break
                    albums.append(album)
                if not albums:
                    self.send_json({"error": "这个标签在所选年份暂未找到正式专辑；可试试相邻年份。"}, 404)
                    return
                self.send_json({"album": albums[0], "queue": albums[1:], "genre": tag})
                return
            if path.path == "/api/rating":
                query = urllib.parse.parse_qs(path.query)
                artist = query.get("artist", [""])[0].strip()
                title = query.get("title", [""])[0].strip()
                if not artist or not title:
                    self.send_json({"error": "artist and title are required"}, 400)
                    return
                score, count = discogs_rating(artist, title)
                qualified = score is not None and count >= MINIMUM_RATING_COUNT
                self.send_json({
                    "rating": f"{score:.1f} / 5" if qualified else "评分样本不足 100",
                    "count": f"{count:,} ratings · Discogs" if qualified else "Discogs",
                    "qualified": qualified,
                })
                return
            if path.path == "/api/cover":
                image_url = urllib.parse.parse_qs(path.query).get("url", [""])[0]
                if not image_url.startswith("https://coverartarchive.org/"):
                    self.send_json({"error": "Invalid cover URL"}, 400)
                    return
                request = urllib.request.Request(image_url, headers=HEADERS)
                with urllib.request.urlopen(request, timeout=25) as response:
                    image = response.read()
                    content_type = response.headers.get_content_type()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(image)
                return
        except UpstreamUnavailable as error:
            self.send_json({"error": str(error)}, 503)
            return
        except Exception as error:
            self.send_json({"error": f"资料请求失败：{error}"}, 502)
            return
        super().do_GET()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    import os
    os.chdir(ROOT)
    print("WTL: http://127.0.0.1:8787")
    ThreadingHTTPServer(("127.0.0.1", 8787), App).serve_forever()
