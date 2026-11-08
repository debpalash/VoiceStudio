import io

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


def portrait_bytes():
    data = io.BytesIO()
    Image.new("RGB", (400, 300), "purple").save(data, "PNG")
    return data.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from api.routers import profiles, profile_images
    from core import db
    monkeypatch.setattr(profiles, "VOICES_DIR", str(tmp_path / "voices"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    db.init_db()
    app = FastAPI()
    app.include_router(profiles.router)
    app.include_router(profile_images.router)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client


def test_portrait_saved_served_replaced_and_deleted(client):
    response = client.post("/profiles", data={"name": "Scarlet"}, files={
        "ref_audio": ("voice.wav", b"RIFF" + bytes(2000), "audio/wav"),
        "image": ("portrait.png", portrait_bytes(), "image/png"),
    })
    assert response.status_code == 200
    profile = response.json()
    assert profile["ref_audio_path"]
    image = client.get(profile["image_url"])
    assert image.status_code == 200
    assert Image.open(io.BytesIO(image.content)).size == (256, 256)
    assert client.get("/profiles").json()[0]["image_url"] == profile["image_url"]
    assert client.put(f"/profiles/{profile['id']}/image", files={
        "image": ("new.png", portrait_bytes(), "image/png"),
    }).status_code == 200
    assert client.delete(f"/profiles/{profile['id']}").status_code == 200
    assert client.get(profile["image_url"]).status_code == 404


def test_bad_image_does_not_create_partial_profile(client):
    response = client.post("/profiles", data={"name": "Scarlet"}, files={
        "ref_audio": ("voice.wav", b"RIFF" + bytes(2000), "audio/wav"),
        "image": ("portrait.png", b"not an image", "image/png"),
    })
    assert response.status_code == 422
    assert client.get("/profiles").json() == []


def test_search_is_keyless_and_returns_five_thumbnails(client, monkeypatch):
    from api.routers import profile_images
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    requests = []

    def respond(request):
        requests.append(request)
        assert "X-Subscription-Token" not in request.headers
        if request.url.host == "www.google.com":
            assert request.url.params["safe"] == "active"
            assert request.url.params["tbs"] == "ift:jpg"
            assert request.url.params["q"] == "Scarlet"
            return httpx.Response(200, text="".join(
                f'<img alt="Portrait" src="https://encrypted-tbn0.gstatic.com/image{i}">'
                for i in range(8)
            ))
        assert request.url.host == "encrypted-tbn0.gstatic.com"
        return httpx.Response(200, content=portrait_bytes())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(profile_images.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    response = client.get("/profile-images/search", params={"name": "Scarlet"})
    assert response.status_code == 200
    assert len(response.json()["images"]) == 5
    assert len(requests) == 6


@pytest.mark.parametrize("status,page", [
    (429, "Unusual traffic"), (200, "Update your browser"), (200, "Consent required"),
    (302, ""), (200, '<img src="http://localhost/private">'),
])
def test_blocked_google_search_falls_back_to_safe_openverse_thumbnails(
    client, monkeypatch, status, page,
):
    from api.routers import profile_images
    def respond(request):
        if request.url.host == "www.google.com":
            return httpx.Response(status, text=page)
        assert request.url.host == "api.openverse.org"
        if request.url.path == "/v1/images/":
            return httpx.Response(200, json={"results": [
                {
                    "title": f"Scarlet {index}",
                    "thumbnail": f"https://api.openverse.org/v1/images/{index:032x}/thumb/",
                    "license": "cc0",
                }
                for index in range(5)
            ]})
        return httpx.Response(200, content=portrait_bytes())
    real_client = httpx.AsyncClient
    monkeypatch.setattr(profile_images.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    response = client.get("/profile-images/search", params={"name": "Scarlet"})
    assert response.status_code == 200
    assert len(response.json()["images"]) == 5


def test_parser_deduplicates_and_reads_embedded_jpeg():
    from api.routers.profile_images import google_thumbnails
    source = "https://encrypted-tbn0.gstatic.com/test?a=1&amp;b=2"
    results = google_thumbnails(f'<img src="{source}"><img src="{source}"><script>var image="data:image/jpeg;base64,YWJj";</script>')
    assert results == [("", source.replace("&amp;", "&")), ("", "data:image/jpeg;base64,YWJj")]


@pytest.mark.parametrize("url", [
    "http://encrypted-tbn0.gstatic.com/a", "https://localhost/a",
    "https://encrypted-tbn0.gstatic.com.evil.test/a",
    "https://user@encrypted-tbn0.gstatic.com/a",
    "https://encrypted-tbn0.gstatic.com:invalid/a",
])
def test_thumbnail_hosts_are_restricted(url):
    from api.routers.profile_images import trusted_thumbnail
    assert not trusted_thumbnail(url)
