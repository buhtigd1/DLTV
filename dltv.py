#!/usr/bin/env python3
"""
dltv — dltv.py
Fetches live channel data from dulo.tv, produces:
  - dltv.m3u       (M3U playlist with EPG header)
  - dltv.xml.gz    (merged XMLTV EPG, gzip-compressed)

EPG data sourced from epg.pw per-channel XML API.
Run every 4 hours via GitHub Actions to handle tokenised stream URLs.
"""

import gzip
import re
import sys
import time
import os
from xml.etree import ElementTree as ET

import cloudscraper
import requests

# ── Config ────────────────────────────────────────────────────────────────────
REPO        = "buhtigd1/DLTV"
BRANCH      = "main"
BASE_RAW    = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
EPG_URL     = f"{BASE_RAW}/dltv.xml.gz"
M3U_OUT     = "dltv.m3u"
EPG_OUT     = "dltv.xml.gz"

LOGIN_URL    = "https://dulo.tv/api/auth/login"
CHANNELS_API = "https://dulo.tv/api/live-tv/channels"
PLAY_URL     = "https://dulo.tv/api/live-tv/play/{channel_id}"
EPG_API      = "https://epg.pw/api/epg.xml?channel_id={channel_id}"

EPG_FETCH_DELAY = 0.5

EPG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml, text/xml, */*",
    "Referer": "https://epg.pw/",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def login():
    username = os.getenv("DULO_USER")
    password = os.getenv("DULO_PASS")
    if not username or not password:
        raise RuntimeError("Missing DULO_USER or DULO_PASS environment variables")

    print("Logging in to dulo.tv …")
    session = cloudscraper.create_scraper()
    r = session.post(LOGIN_URL, json={"username": username, "password": password})
    if r.status_code != 200:
        print(f"[error] Login failed: HTTP {r.status_code}")
        print(r.text[:300])
        sys.exit(1)
    print("Login successful.")
    return session


def extract_epg_channel_id(epg_source_url: str) -> str | None:
    if not epg_source_url:
        return None
    m = re.search(r"channel_id=(\d+)", epg_source_url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d+)\.html", epg_source_url)
    if m:
        return m.group(1)
    return None


def fetch_channels(session) -> list[dict]:
    print("Fetching channel list from dulo.tv …")
    r = session.get(CHANNELS_API, timeout=30)
    if r.status_code != 200:
        print(f"[error] HTTP {r.status_code}")
        print(r.text[:300])
        sys.exit(1)

    try:
        data = r.json()
    except Exception as e:
        print("JSON decode failed:", e)
        print(r.text[:500])
        sys.exit(1)

    if isinstance(data, dict):
        channels = data.get("channels") or data.get("data") or []
    elif isinstance(data, list):
        channels = data
    else:
        channels = []

    print(f"→ {len(channels)} channels")
    if channels:
        print("Sample channel:", channels[0])
    return channels


def fetch_stream_url(session, channel_id: str) -> str | None:
    r = session.get(PLAY_URL.format(channel_id=channel_id), timeout=20)
    if r.status_code == 200:
        data = r.json()
        return data.get("stream_url") or data.get("url")
    return None


def build_m3u(session, channels: list[dict]) -> str:
    lines = [f'#EXTM3U url-tvg="{EPG_URL}"\n']
    for ch in channels:
        ch_id   = ch.get("id", "")
        name    = ch.get("name", "Unknown")
        logo    = ch.get("logo_url", "")
        group   = ch.get("category", "General").title()
        stream  = fetch_stream_url(session, ch_id)
        epg_cid = extract_epg_channel_id(ch.get("epg_source_url", "")) or ch_id

        if not stream:
            print(f"Skipping channel {name}: no stream URL found")
            continue

        lines.append(
            f'#EXTINF:-1 tvg-id="{epg_cid}" tvg-name="{name}" '
            f'tvg-logo="{logo}" group-title="{group}",{name}\n'
            f'{stream}\n'
        )
    return "".join(lines)


def fetch_epg_xml(session: requests.Session, channel_id: str) -> ET.Element | None:
    url = EPG_API.format(channel_id=channel_id)
    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.content)
        return root
    except Exception as e:
        print(f"    [warn] EPG fetch failed for channel_id={channel_id}: {e}")
        return None


def build_epg(channels: list[dict]) -> bytes:
    session = requests.Session()
    session.headers.update(EPG_HEADERS)

    tv = ET.Element("tv", attrib={
        "source-info-name": "epg.pw",
        "generator-info-name": f"github.com/{REPO}",
    })

    seen_channels: set[str] = set()
    programme_elements: list[ET.Element] = []

    total = len(channels)
    for i, ch in enumerate(channels, 1):
        ch_id = extract_epg_channel_id(ch.get("epg_source_url", "")) or ch.get("id")
        if not ch_id:
            continue

        print(f"  [{i}/{total}] EPG for {ch.get('name', ch_id)} (id={ch_id})")
        root = fetch_epg_xml(session, str(ch_id))
        if root is None:
            time.sleep(EPG_FETCH_DELAY)
            continue

        for chan_el in root.findall("channel"):
            cid = chan_el.get("id", "")
            if cid and cid not in seen_channels:
                seen_channels.add(cid)
                tv.append(chan_el)

        for prog_el in root.findall("programme"):
            programme_elements.append(prog_el)

        time.sleep(EPG_FETCH_DELAY)

    for prog_el in programme_elements:
        tv.append(prog_el)

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode").encode()
    return xml_bytes


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    session = login()
    channels = fetch_channels(session)

    print("\nBuilding M3U playlist …")
    m3u_content = build_m3u(session, channels)
    with open(M3U_OUT, "w", encoding="utf-8") as f:
        f.write(m3u_content)
    print(f"  → wrote {M3U_OUT} ({len(m3u_content):,} bytes)")

    print("\nFetching EPG data from epg.pw …")
    xml_bytes = build_epg(channels)
    with gzip.open(EPG_OUT, "wb") as f:
        f.write(xml_bytes)
    print(f"  → wrote {EPG_OUT} ({len(xml_bytes):,} bytes uncompressed)")

    print("\nDone.")


if __name__ == "__main__":
    main()
