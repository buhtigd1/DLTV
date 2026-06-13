#!/usr/bin/env python3
"""
dltv.py
Fetches live channel data from dulo.tv, produces:
  - dltv.m3u       (M3U playlist with EPG header)
  - dltv.xml.gz    (merged XMLTV EPG, gzip-compressed)

Authentication: Supabase access_token + refresh_token
"""

import gzip
import os
import re
import sys
import time
import requests
import cloudscraper
from xml.etree import ElementTree as ET

# ── Config ───────────────────────────────────────────────────────────────
CHANNELS_API = "https://dulo.tv/api/live-tv/channels"
PLAY_URL     = "https://dulo.tv/api/live-tv/play/{channel_id}"
EPG_API      = "https://epg.pw/api/epg.xml?channel_id={channel_id}"

SUPABASE_URL = "https://bppkbjyfrtjuvrwrayop.supabase.co"
ACCESS_TOKEN = os.getenv("SUPABASE_TOKEN")
REFRESH_TOKEN = os.getenv("SUPABASE_REFRESH")

M3U_OUT = "dltv.m3u"
EPG_OUT = "dltv.xml.gz"

# ── Auth Helpers ─────────────────────────────────────────────────────────
def get_session():
    global ACCESS_TOKEN
    if not ACCESS_TOKEN:
        raise RuntimeError("Missing SUPABASE_TOKEN")
    session = cloudscraper.create_scraper()
    session.headers.update({"Authorization": f"Bearer {ACCESS_TOKEN}"})
    return session

def refresh_access_token():
    global ACCESS_TOKEN
    if not REFRESH_TOKEN:
        raise RuntimeError("Missing SUPABASE_REFRESH")
    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token"
    r = requests.post(url, json={"refresh_token": REFRESH_TOKEN})
    r.raise_for_status()
    data = r.json()
    ACCESS_TOKEN = data["access_token"]
    print("Access token refreshed.")
    return ACCESS_TOKEN

def safe_request(session, url, **kwargs):
    r = session.get(url, **kwargs)
    if r.status_code == 401:
        print("Token expired, refreshing…")
        refresh_access_token()
        session.headers.update({"Authorization": f"Bearer {ACCESS_TOKEN}"})
        r = session.get(url, **kwargs)
    return r

# ── Channel & Stream ─────────────────────────────────────────────────────
def fetch_channels(session):
    print("Fetching channel list from dulo.tv …")
    r = safe_request(session, CHANNELS_API, timeout=30)
    if r.status_code != 200:
        print(f"[error] HTTP {r.status_code}")
        print(r.text[:300])
        sys.exit(1)
    data = r.json()
    channels = data.get("channels", data) if isinstance(data, dict) else data
    print(f"→ {len(channels)} channels")
    return channels

def fetch_stream_url(session, channel_id: str) -> str | None:
    url = PLAY_URL.format(channel_id=channel_id)
    r = safe_request(session, url, timeout=20)
    print(f"[debug] play API {channel_id} → {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        data = r.json()
        return data.get("stream_url") or data.get("url") or data.get("playback_url")
    return None

def build_m3u(session, channels):
    lines = ['#EXTM3U\n']
    for ch in channels:
        ch_id = ch.get("id", "")
        name  = ch.get("name", "Unknown")
        logo  = ch.get("logo_url", "")
        group = ch.get("category", "General").title()
        stream = fetch_stream_url(session, ch_id)

        if not stream:
            print(f"Skipping channel {name}: no stream URL found")
            continue

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" tvg-name="{name}" '
            f'tvg-logo="{logo}" group-title="{group}",{name}\n'
            f'{stream}\n'
        )
    return "".join(lines)

# ── Main ─────────────────────────────────────────────────────────────────
def main():
    session = get_session()
    channels = fetch_channels(session)

    print("\nBuilding M3U playlist …")
    m3u_content = build_m3u(session, channels)
    with open(M3U_OUT, "w", encoding="utf-8") as f:
        f.write(m3u_content)
    print(f"  → wrote {M3U_OUT} ({len(m3u_content):,} bytes)")

    print("\nDone.")

if __name__ == "__main__":
    main()
