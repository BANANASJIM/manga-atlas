from fastapi.testclient import TestClient


def test_rejects_foreign_host(client: TestClient) -> None:
    assert client.get("/api/x", headers={"host": "evil.example"}).status_code == 421


def test_post_needs_header(client: TestClient) -> None:
    assert client.post("/api/anything").status_code == 403


def test_no_cors_headers(client: TestClient) -> None:
    r = client.get("/api/nothing", headers={"origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def fetch_meta(site: str, mode: str, dest: str, user: bool = False) -> dict[str, str]:
    meta = {"sec-fetch-site": site, "sec-fetch-mode": mode, "sec-fetch-dest": dest}
    return meta | {"sec-fetch-user": "?1"} if user else meta


def test_rejects_cross_site_subresources_and_fetches(client: TestClient) -> None:
    image = fetch_meta("cross-site", "no-cors", "image")
    assert client.get("/cover/1", headers=image).status_code == 403
    xhr = fetch_meta("cross-site", "cors", "empty")
    assert client.get("/api/search", headers=xhr).status_code == 403
    framed = fetch_meta("cross-site", "navigate", "iframe", user=True)
    assert client.get("/", headers=framed).status_code == 403
    scripted = fetch_meta("cross-site", "navigate", "document")  # location.replace(), no click
    assert client.get("/work/1", headers=scripted).status_code == 403


def test_allows_cross_site_links_to_pages_only(client: TestClient) -> None:
    link = fetch_meta("cross-site", "navigate", "document", user=True)
    assert client.get("/", headers=link).status_code == 200
    assert client.get("/work/1", headers=link).status_code == 200
    assert client.get("/api/status", headers=link).status_code == 403
    assert client.get("/cover/1", headers=link).status_code == 403  # would fetch from Bangumi
    form = client.post("/work/1", headers=link)  # a cross-site form still lacks X-Atlas
    assert form.status_code == 403


def test_same_origin_and_same_site_pass(client: TestClient) -> None:
    for site in ("same-origin", "same-site", "none"):
        meta = fetch_meta(site, "no-cors", "image")
        assert client.get("/cover/1", headers=meta).status_code == 404  # not built, not refused
