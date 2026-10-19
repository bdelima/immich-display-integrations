"""Tests for the image cache, run against a small fake Immich on a local port.

    pip install pytest && pytest overflight-feed
"""
import importlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ID1 = "11111111-1111-4111-8111-111111111111"
ID2 = "22222222-2222-4222-8222-222222222222"
BYTES = {ID1: b"\xff\xd8original-one" * 1000, ID2: b"\x00\x00\x00 ftypmp42video" * 500}


class FakeImmich(BaseHTTPRequestHandler):
    downloads = []          # asset ids fetched, in order
    items = []
    listings = 0            # how many times the album was asked for

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        FakeImmich.listings += 1
        body = json.dumps({"assets": {"items": FakeImmich.items, "nextPage": None}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        asset = parts[2] if len(parts) == 4 and parts[3] == "original" else None
        if asset not in BYTES or "slug=Live" not in self.path:
            self.send_response(403 if asset in BYTES else 404)
            self.end_headers()
            return
        FakeImmich.downloads.append(asset)
        self.send_response(200)
        self.send_header("Content-Length", str(len(BYTES[asset])))
        self.end_headers()
        self.wfile.write(BYTES[asset])


@pytest.fixture()
def feed(tmp_path, monkeypatch):
    FakeImmich.downloads = []
    FakeImmich.listings = 0
    FakeImmich.items = [
        {"id": ID1, "originalFileName": "IMG_0001.JPG", "type": "IMAGE", "checksum": "abc"},
        {"id": ID2, "originalFileName": "clip.mp4", "type": "VIDEO", "checksum": "def"},
    ]
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeImmich)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("IMMICH_INTERNAL_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("IMMICH_PUBLIC_URL", "https://immich.example.com")
    monkeypatch.setenv("ALBUM_ID", "album")
    monkeypatch.setenv("IMMICH_API_KEY", "key")
    monkeypatch.setenv("SECRETS_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("SHARE_SLUG", "Live")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "images"))
    monkeypatch.setenv("DISABLE_BACKGROUND", "1")
    monkeypatch.delenv("FEED_PUBLIC_URL", raising=False)
    sys.path.insert(0, HERE)
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    yield module, module.app.test_client()
    server.shutdown()
    sys.path.remove(HERE)


def test_links_point_at_the_feed_not_immich(feed):
    module, client = feed
    data = client.get("/wallpapers.json", base_url="http://192.168.0.5:8095").get_json()
    assert data[0]["url_img"].startswith("http://192.168.0.5:8095/img/" + ID1 + "-")
    assert data[0]["url_img"].endswith(".jpg")
    assert data[1]["url_1080p"].startswith("http://192.168.0.5:8095/img/" + ID2 + "-")
    assert "immich.example.com" not in json.dumps(data)


def test_feed_public_url_wins(feed, monkeypatch):
    module, client = feed
    monkeypatch.setattr(module, "FEED_PUBLIC_URL", "https://feed.example.com")
    data = client.get("/wallpapers.json").get_json()
    assert data[0]["url_img"].startswith("https://feed.example.com/img/")


def test_an_original_is_read_from_immich_once_and_served_unchanged(feed):
    module, client = feed
    url = client.get("/wallpapers.json").get_json()[0]["url_img"]
    path = "/img/" + url.rsplit("/img/", 1)[1]
    for _ in range(5):
        r = client.get(path)
        assert r.status_code == 200
        assert r.data == BYTES[ID1]                 # full original, not resized
        assert r.mimetype == "image/jpeg"
    assert FakeImmich.downloads == [ID1]            # Immich was asked once, not five times


def test_warming_reads_each_new_photo_once_and_then_nothing(feed):
    module, client = feed
    wallpapers = module.get_wallpapers_cached()
    module._warm(wallpapers)
    module._warm(wallpapers)
    assert sorted(FakeImmich.downloads) == sorted([ID1, ID2])


def test_range_requests_work_for_video(feed):
    module, client = feed
    url = client.get("/wallpapers.json").get_json()[1]["url_1080p"]
    r = client.get("/img/" + url.rsplit("/img/", 1)[1], headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.data == BYTES[ID2][:10]


def test_a_name_not_in_the_album_is_refused(feed):
    module, client = feed
    client.get("/wallpapers.json")
    assert client.get("/img/" + ID1 + "-0000000000.jpg").status_code == 404
    assert client.get("/img/../app.py").status_code == 404
    assert client.get("/img/not-a-name").status_code == 404


def test_a_replaced_original_is_fetched_again(feed):
    module, client = feed
    module._warm(module.get_wallpapers_cached())
    FakeImmich.items[0]["checksum"] = "changed"
    module._cache["body"] = None
    module._warm(module.get_wallpapers_cached())
    assert FakeImmich.downloads.count(ID1) == 2


def test_a_short_download_is_not_kept(feed, monkeypatch):
    module, client = feed
    BYTES_BACKUP = BYTES[ID1]
    entry = module.get_wallpapers_cached()[0]

    class Short:
        headers = {"Content-Length": "100"}
        def raise_for_status(self): pass
        def iter_content(self, n): yield b"x" * 10
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(module.requests, "get", lambda *a, **k: Short())
    with pytest.raises(module.requests.RequestException):
        module._download(entry)
    assert not os.path.exists(os.path.join(module.CACHE_DIR, entry["name"]))
    assert not [f for f in os.listdir(module.CACHE_DIR) if f.endswith(".part")]
    assert BYTES[ID1] == BYTES_BACKUP


def _age(path, seconds):
    old = os.path.getmtime(path) - seconds
    os.utime(path, (old, old))


def test_a_copy_is_removed_a_day_after_the_photo_left_the_album_not_a_day_after_it_was_downloaded(feed):
    module, client = feed
    wallpapers = module.get_wallpapers_cached()
    module._warm(wallpapers)
    gone = os.path.join(module.CACHE_DIR, "33333333-3333-4333-8333-333333333333-0123456789.jpg")
    open(gone, "wb").close()
    _age(gone, 10 * 24 * 3600)                      # downloaded ten days ago...
    module._prune(wallpapers)                       # ...and only now found to be missing from the album
    assert os.path.exists(gone)                     # so it stays: the day starts now
    assert os.path.exists(gone + ".gone")
    module._prune(wallpapers)
    assert os.path.exists(gone)
    _age(gone + ".gone", 25 * 3600)                 # a day and an hour later
    module._prune(wallpapers)
    assert not os.path.exists(gone) and not os.path.exists(gone + ".gone")
    for w in wallpapers:                            # what is in the album is never touched
        assert os.path.exists(os.path.join(module.CACHE_DIR, w["name"]))


def test_a_photo_that_comes_back_to_the_album_keeps_its_copy(feed):
    module, client = feed
    wallpapers = module.get_wallpapers_cached()
    module._warm(wallpapers)
    first = wallpapers[0]
    path = os.path.join(module.CACHE_DIR, first["name"])
    module._prune(wallpapers[1:])                   # it is missing for a while...
    assert os.path.exists(path + ".gone")
    _age(path + ".gone", 3 * 24 * 3600)
    module._prune(wallpapers)                       # ...but is back before the prune that would have removed it
    assert os.path.exists(path) and not os.path.exists(path + ".gone")
    module._prune(wallpapers[1:])                   # missing again: a fresh day starts, it is not removed at once
    assert os.path.exists(path)


def test_prune_never_empties_the_cache_on_an_empty_listing(feed):
    module, client = feed
    wallpapers = module.get_wallpapers_cached()
    module._warm(wallpapers)
    module._prune([])
    assert os.path.exists(os.path.join(module.CACHE_DIR, wallpapers[0]["name"]))
    assert not [f for f in os.listdir(module.CACHE_DIR) if f.endswith(".gone")]


def test_a_worker_that_has_not_listed_the_album_yet_still_serves_an_album_photo(feed):
    module, client = feed
    assert module._cache["body"] is None            # this worker has not looked at the album
    name = module._cache_name(FakeImmich.items[0])
    r = client.get("/img/" + name)
    assert r.status_code == 200 and r.data == BYTES[ID1]
    assert FakeImmich.downloads == [ID1]


def test_a_photo_added_since_this_worker_last_listed_is_found(feed):
    module, client = feed
    module.get_wallpapers_cached()
    module._cache["fetched_at"] -= 60               # a minute old: still inside CACHE_SECONDS, but older than REFRESH_SECONDS
    ID3 = "33333333-3333-4333-8333-333333333333"
    BYTES[ID3] = b"brand new"
    try:
        FakeImmich.items.append({"id": ID3, "originalFileName": "new.jpg", "type": "IMAGE", "checksum": "ghi"})
        r = client.get("/img/" + module._cache_name(FakeImmich.items[-1]))
        assert r.status_code == 200 and r.data == b"brand new"
    finally:
        BYTES.pop(ID3, None)


def test_a_made_up_name_cannot_make_the_feed_ask_immich_for_the_album_again_and_again(feed):
    module, client = feed
    module.get_wallpapers_cached()
    module._cache["fetched_at"] -= 60
    before = FakeImmich.listings
    for _ in range(5):
        assert client.get("/img/" + ID1 + "-0000000000.jpg").status_code == 404
    assert FakeImmich.listings == before + 1        # asked once, then left alone for REFRESH_SECONDS


def test_cache_off_keeps_the_old_links(feed, monkeypatch):
    module, client = feed
    monkeypatch.setattr(module, "CACHE_ON", False)
    data = client.get("/wallpapers.json").get_json()
    assert data[0]["url_img"] == f"https://immich.example.com/api/assets/{ID1}/original?slug=Live"
    assert client.get("/img/" + module.get_wallpapers_cached()[0]["name"]).status_code == 404
