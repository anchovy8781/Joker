"""Keeps the app's auto-connect address up to date.

Every 20 s: read the current public address (ngrok fixed domain, or the Cloudflare quick
tunnel URL from its log), write it to run/public-url, and — when it changes — publish it to
GitHub (branch `server-address`, file `server.json`). The Android app reads that file at
launch, so users never type an address. Publishing needs YTX_GITHUB_TOKEN (fine-grained
token with Contents read/write on this repository only); without it the address is only
shown locally.
"""

import base64
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

DATA = Path(os.environ.get("YTX_DATA_DIR", "/opt/ytx-data"))
REPO = os.environ.get("YTX_GITHUB_REPO", "anchovy8781/Joker")
BRANCH = "server-address"
FILE = "server.json"
API = "https://api.github.com"
# Quick-tunnel hosts are random hyphenated words (e.g. https://tall-river-sun-bay.trycloudflare.com).
# Requiring a hyphen excludes api.trycloudflare.com, which appears in error messages.
TUNNEL_RE = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com")


def current_address() -> str | None:
    fixed = os.environ.get("YTX_PUBLIC_URL", "").strip()  # e.g. Render's RENDER_EXTERNAL_URL
    if fixed:
        return fixed.rstrip("/")
    domain = os.environ.get("NGROK_DOMAIN", "").strip()
    if domain and os.environ.get("NGROK_AUTHTOKEN", "").strip():
        return f"https://{domain}"
    log = DATA / "logs" / "tunnel.log"
    if not log.exists():
        return None
    # Only the tail matters: the newest URL wins after a tunnel restart.
    with log.open("rb") as f:
        f.seek(max(0, log.stat().st_size - 200_000))
        found = TUNNEL_RE.findall(f.read().decode("utf-8", "replace"))
    return found[-1] if found else None


def publish(client: httpx.Client, url: str) -> None:
    ref = client.get(f"{API}/repos/{REPO}/git/ref/heads/{BRANCH}")
    if ref.status_code == 404:
        default = client.get(f"{API}/repos/{REPO}").raise_for_status().json()["default_branch"]
        sha = client.get(f"{API}/repos/{REPO}/git/ref/heads/{default}").raise_for_status().json()["object"]["sha"]
        client.post(f"{API}/repos/{REPO}/git/refs", json={"ref": f"refs/heads/{BRANCH}", "sha": sha}).raise_for_status()
    else:
        ref.raise_for_status()
    existing = client.get(f"{API}/repos/{REPO}/contents/{FILE}", params={"ref": BRANCH})
    sha = existing.json()["sha"] if existing.status_code == 200 else None
    body = {"url": url, "updated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    payload = {
        "message": f"server address: {url}",
        "content": base64.b64encode((json.dumps(body, indent=2) + "\n").encode()).decode(),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    client.put(f"{API}/repos/{REPO}/contents/{FILE}", json=payload).raise_for_status()


def main() -> None:
    token = os.environ.get("YTX_GITHUB_TOKEN", "").strip()
    client = None
    if token:
        client = httpx.Client(timeout=20, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ytx-phone-server"})
    else:
        print("YTX_GITHUB_TOKEN not set: address is shown locally only (app needs manual entry)", flush=True)
    published = None
    run = DATA / "run"
    run.mkdir(parents=True, exist_ok=True)
    while True:
        url = current_address()
        if url:
            (run / "public-url").write_text(url + "\n")
            if client is not None and url != published:
                try:
                    publish(client, url)
                    published = url
                    print(f"published {url}", flush=True)
                except httpx.HTTPError as exc:  # never log the token
                    status = getattr(getattr(exc, "response", None), "status_code", "network")
                    print(f"publish failed ({status}); retrying", flush=True)
        if "--once" in sys.argv:
            return
        time.sleep(20)


if __name__ == "__main__":
    main()
