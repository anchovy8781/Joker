"""deploy/phone/address_watcher.py: address detection and GitHub publishing (mocked)."""

import base64
import importlib.util
import json
from pathlib import Path

import httpx

WATCHER = Path(__file__).resolve().parents[2] / "deploy" / "phone" / "address_watcher.py"


def load(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("YTX_DATA_DIR", str(tmp_path))
    for k in ("NGROK_DOMAIN", "NGROK_AUTHTOKEN"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("address_watcher", WATCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ignores_error_lines_and_picks_latest_tunnel(monkeypatch, tmp_path):
    w = load(monkeypatch, tmp_path)
    (tmp_path / "logs").mkdir()
    log = tmp_path / "logs" / "tunnel.log"
    log.write_text('failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": dial tcp\n')
    assert w.current_address() is None
    log.write_text(log.read_text() + "|  https://tall-river-sun-bay.trycloudflare.com  |\n"
                   "restart\n|  https://new-cool-dog-cat.trycloudflare.com  |\n")
    assert w.current_address() == "https://new-cool-dog-cat.trycloudflare.com"


def test_fixed_ngrok_domain_wins(monkeypatch, tmp_path):
    w = load(monkeypatch, tmp_path, NGROK_DOMAIN="my-app.ngrok-free.app", NGROK_AUTHTOKEN="t")
    assert w.current_address() == "https://my-app.ngrok-free.app"


def test_publish_creates_branch_then_updates_file(monkeypatch, tmp_path):
    w = load(monkeypatch, tmp_path, YTX_GITHUB_REPO="me/repo")
    calls = []
    state = {"branch": False, "sha": None}

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        path = req.url.path
        if path == "/repos/me/repo/git/ref/heads/server-address":
            return httpx.Response(200 if state["branch"] else 404, json={"object": {"sha": "b"}})
        if path == "/repos/me/repo":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/me/repo/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "abc"}})
        if path == "/repos/me/repo/git/refs":
            state["branch"] = True
            assert json.loads(req.content) == {"ref": "refs/heads/server-address", "sha": "abc"}
            return httpx.Response(201, json={})
        if path == "/repos/me/repo/contents/server.json" and req.method == "GET":
            return httpx.Response(200, json={"sha": state["sha"]}) if state["sha"] else httpx.Response(404)
        if path == "/repos/me/repo/contents/server.json" and req.method == "PUT":
            body = json.loads(req.content)
            assert body["branch"] == "server-address"
            assert ("sha" in body) == bool(state["sha"])
            state["content"] = json.loads(base64.b64decode(body["content"]))
            state["sha"] = "s1"
            return httpx.Response(200, json={})
        raise AssertionError(path)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    w.publish(client, "https://a-b-c.trycloudflare.com")
    assert state["content"]["url"] == "https://a-b-c.trycloudflare.com"
    assert ("POST", "/repos/me/repo/git/refs") in calls
    w.publish(client, "https://d-e-f.trycloudflare.com")  # update existing file (sends sha)
    assert state["content"]["url"] == "https://d-e-f.trycloudflare.com"
