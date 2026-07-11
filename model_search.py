#!/usr/bin/env python3
"""
Recherche de modèles/pornstars Pornhub par mot-clé.

Utilise l'API d'autocomplete officielle du champ de recherche
(/api/v1/video/search_autocomplete) — pas besoin de Playwright ici,
un simple GET avec les cookies de session + un token frais suffit.

Usage:
    python3 model_search.py <mot-clé>
"""

import html
import json
import re
import sys

import requests

_unescape_html = html.unescape

from credentials import CREDENTIALS_DIR, ensure_fresh

SEARCH_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)


def _load_cookies() -> dict:
    cookies_file = CREDENTIALS_DIR / "cookies.json"
    if not cookies_file.exists():
        return {}
    raw = json.loads(cookies_file.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {c["name"]: c["value"] for c in raw}
    return raw


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": SEARCH_UA, "Accept-Language": "en-US,en;q=0.9"})
    session.cookies.update(_load_cookies())
    return session


def _fetch_search_token(session: requests.Session) -> str:
    """Le champ de recherche embarque un token frais dans data-token à chaque page."""
    resp = session.get("https://www.pornhub.com/", timeout=15)
    resp.raise_for_status()
    match = re.search(r'data-token="([^"]+)"', resp.text)
    if not match:
        raise RuntimeError("Token de recherche introuvable (cookies invalides ou page modifiée)")
    return match.group(1)


def search_models(query: str) -> list[dict]:
    """
    Recherche les modèles/pornstars Pornhub correspondant au mot-clé.

    Retourne une liste de dicts triés par rang :
    {name, slug, url, type ('model'|'pornstar'), rank}
    """
    query = query.strip()
    if not query:
        return []

    ensure_fresh()
    session = _new_session()
    token = _fetch_search_token(session)

    resp = session.get(
        "https://www.pornhub.com/api/v1/video/search_autocomplete",
        params={"token": token, "orientation": "straight", "q": query, "alt": 0, "pornstars": "true"},
        headers={"Referer": "https://www.pornhub.com/video/search"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, dict):
        raise RuntimeError(f"Réponse inattendue de l'API de recherche: {data}")

    results = []
    for entry in data.get("models", []):
        slug = entry.get("slug", "")
        results.append({
            "name": entry.get("name", ""), "slug": slug, "rank": entry.get("rank"),
            "type": "model", "url": f"https://www.pornhub.com/model/{slug}",
        })
    for entry in data.get("pornstars", []):
        slug = entry.get("slug", "")
        results.append({
            "name": entry.get("name", ""), "slug": slug, "rank": entry.get("rank"),
            "type": "pornstar", "url": f"https://www.pornhub.com/pornstar/{slug}",
        })

    results.sort(key=lambda r: r["rank"] if r["rank"] is not None else 999_999)
    return results


_VIDEO_CARD_RE = re.compile(r'<li class="pcVideoListItem.*?(?=<li class="pcVideoListItem|\Z)', re.S)


def _parse_video_cards(html: str) -> list[dict]:
    videos = []
    for block in _VIDEO_CARD_RE.findall(html):
        vkey_m = re.search(r'data-video-vkey="([^"]+)"', block)
        if not vkey_m:
            continue
        vkey = vkey_m.group(1)

        # Le titre est le plus fiable sur l'attribut title= du lien miniature
        # (présent dans toutes les variantes de carte, contrairement à alt=).
        title_m = re.search(r'class="[^"]*linkVideoThumb[^"]*"[^>]*title="([^"]+)"', block) \
            or re.search(r'title="([^"]+)"[^>]*class="[^"]*linkVideoThumb', block) \
            or re.search(r'alt="([^"]+)"', block)

        # data-mediumthumb sur les cartes "premium", simple src= sur les autres.
        thumb_m = re.search(r'data-mediumthumb="([^"]+)"', block) \
            or re.search(r'<img\s[^>]*\bsrc="([^"]+)"', block)

        dur_m = re.search(r'data-title="Video Duration">([^<]+)<', block)
        views_m = re.search(r'class="views">.*?<var>([^<]+)</var>', block, re.S)
        videos.append({
            "vkey": vkey,
            "title": _unescape_html(title_m.group(1)) if title_m else "",
            "thumbnail": thumb_m.group(1) if thumb_m else "",
            "duration": dur_m.group(1) if dur_m else "",
            "views": views_m.group(1) if views_m else "",
            "url": f"https://www.pornhub.com/view_video.php?viewkey={vkey}",
        })
    return videos


def search_videos(query: str, limit: int = 16) -> list[dict]:
    """
    Recherche des vidéos Pornhub correspondant au mot-clé (scrape /video/search).
    Retourne une liste de dicts : {vkey, title, thumbnail, duration, views, url}.
    """
    query = query.strip()
    if not query:
        return []

    ensure_fresh()
    session = _new_session()
    resp = session.get(
        "https://www.pornhub.com/video/search",
        params={"search": query},
        timeout=15,
    )
    resp.raise_for_status()
    return _parse_video_cards(resp.text)[:limit]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    results = search_models(query)

    if not results:
        print(f"Aucun résultat pour « {query} »")
        return 0

    print(f"\n{len(results)} résultat(s) pour « {query} » :\n")
    for r in results:
        print(f"  [{r['type']:8}] {r['name']:30}  {r['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
