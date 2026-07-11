#!/usr/bin/env python3
"""
Service de téléchargement de Pornhub Shorties.

Usage:
    python3 downloader.py https://www.pornhub.com/shorties/xxx
    python3 downloader.py https://www.pornhub.com/shorties/xxx --output ./downloads
    python3 downloader.py https://www.pornhub.com/shorties/xxx --filename ma_video
"""

import sys
import json
import asyncio
import threading
import subprocess
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from credentials import ensure_fresh, refresh as refresh_credentials

PROJECT_DIR = Path(__file__).parent
CREDENTIALS_DIR = PROJECT_DIR / "credentials"
DOWNLOADS_DIR = PROJECT_DIR / "downloads"

YTDLP_BIN = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
FFMPEG_BIN = shutil.which("ffmpeg")

# Ressources inutiles à bloquer pour accélérer le chargement
_BLOCK_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                     ".woff", ".woff2", ".ttf", ".otf", ".css"}


def _extract_vkey(url: str) -> str:
    """
    Identifiant court pour nommer le fichier téléchargé.
    - shorties: .../shorties/68c29a3d3f77e        -> 68c29a3d3f77e
    - vidéo   : view_video.php?viewkey=68c29a...   -> 68c29a3d3f77e (pas le nom de fichier .php)
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "viewkey" in qs and qs["viewkey"][0]:
        return qs["viewkey"][0]
    return parsed.path.rstrip("/").split("/")[-1] or "video"


def is_supported_url(url: str) -> bool:
    return "pornhub.com/shorties/" in url or "viewkey=" in url


# ------------------------------------------------------------------
# Cookies
# ------------------------------------------------------------------

def _load_playwright_cookies() -> list:
    cookies_file = CREDENTIALS_DIR / "cookies.json"
    if not cookies_file.exists():
        return []
    raw = json.loads(cookies_file.read_text(encoding="utf-8"))
    if isinstance(raw, list) and raw and "domain" in raw[0]:
        return [
            {k: v for k, v in c.items()
             if k in ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite")}
            for c in raw
        ]
    d = raw if isinstance(raw, dict) else {c["name"]: c["value"] for c in raw}
    return [{"name": k, "value": v, "domain": ".pornhub.com", "path": "/"} for k, v in d.items()]


def _cookies_to_netscape() -> Path | None:
    cookies_file = CREDENTIALS_DIR / "cookies.json"
    if not cookies_file.exists():
        return None
    raw = json.loads(cookies_file.read_text(encoding="utf-8"))
    netscape = CREDENTIALS_DIR / "cookies_netscape.txt"
    lines = ["# Netscape HTTP Cookie File\n"]
    if isinstance(raw, list):
        for c in raw:
            domain = c.get("domain", ".pornhub.com")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure", False) else "FALSE"
            expires_val = int(c.get("expires") or 0)
            expires = str(max(expires_val, 0))  # -1 invalide → 0 (session cookie)
            lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{c['name']}\t{c['value']}\n")
    else:
        for name, value in raw.items():
            lines.append(f".pornhub.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
    netscape.write_text("".join(lines), encoding="utf-8")
    return netscape


# ------------------------------------------------------------------
# Fast path : yt-dlp direct (pas de browser)
# ------------------------------------------------------------------

def _try_ytdlp_direct(url: str, output_tmpl: str, concurrent_fragments: int = 8) -> bool:
    """
    Tente de télécharger directement via yt-dlp sans Playwright.
    Retourne True si réussi.
    """
    if not YTDLP_BIN or not Path(YTDLP_BIN).exists():
        return False

    cookies_file = _cookies_to_netscape()
    cmd = [
        YTDLP_BIN, url,
        "--output", output_tmpl,
        "--concurrent-fragments", str(concurrent_fragments),
        "--no-playlist",
        "--quiet", "--progress",
    ]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]

    print("[FAST] Tentative yt-dlp direct (sans browser)...")
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"[FAST] Succès en {time.time()-t0:.1f}s")
        return True

    # Détecter une erreur d'authentification (403, login required…)
    output = (result.stdout + result.stderr).lower()
    auth_error = any(k in output for k in ("403", "login", "premium", "private", "unauthorized"))
    if auth_error:
        print("[FAST] Erreur auth détectée — refresh cookies et retry...")
        if refresh_credentials():
            # Reconstruire la commande avec les nouveaux cookies
            cookies_file = _cookies_to_netscape()
            if cookies_file:
                # Remplacer l'ancien --cookies dans cmd
                try:
                    idx = cmd.index("--cookies")
                    cmd[idx + 1] = str(cookies_file)
                except ValueError:
                    cmd += ["--cookies", str(cookies_file)]
            result2 = subprocess.run(cmd, capture_output=True, text=True)
            if result2.returncode == 0:
                print(f"[FAST] Succès après refresh en {time.time()-t0:.1f}s")
                return True

    return False


# ------------------------------------------------------------------
# Fallback : Playwright — interception URL vidéo
# ------------------------------------------------------------------

def _intercept_via_playwright(shorties_url: str) -> dict:
    """
    Navigue vers la page shorties (viewport mobile), bloque les ressources
    inutiles, intercepte la première URL .m3u8/mp4 dès qu'elle est émise.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[ERROR] Playwright non installé")
        return {}

    pw_cookies = _load_playwright_cookies()
    captured: dict = {}
    t0_pw = time.time()
    url_ready = threading.Event()  # signalé dès que l'URL vidéo est capturée

    def _ts():
        return f"[+{time.time()-t0_pw:.2f}s]"

    async def run():
        import asyncio as _aio
        async with async_playwright() as p:
            print(f"{_ts()} [PLAYWRIGHT] Lancement navigateur")
            browser = await p.chromium.launch(headless=True)
            print(f"{_ts()} [PLAYWRIGHT] Navigateur prêt")
            context = await browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                ),
                is_mobile=True,
            )
            # Injecter les cookies de session (incluent pornhub_av si refresh a tourné)
            # + fallback hardcodé au cas où pornhub_av est absent des cookies sauvegardés
            all_cookies = list(pw_cookies or [])
            has_av = any(c.get("name") == "pornhub_av" for c in all_cookies)
            if not has_av:
                all_cookies += [
                    {"name": "pornhub_av",    "value": "1", "domain": ".pornhub.com", "path": "/"},
                    {"name": "cookieConsent", "value": "1", "domain": ".pornhub.com", "path": "/"},
                ]
            await context.add_cookies(all_cookies)

            page = await context.new_page()

            # Bloquer images / fonts / CSS — inutiles pour l'interception
            async def _block_useless(route):
                ext = Path(route.request.url.split("?")[0]).suffix.lower()
                if ext in _BLOCK_EXTENSIONS:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", _block_useless)

            # Listener d'interception vidéo
            async def on_request(req):
                if "video_url" in captured:
                    return
                u = req.url
                if "phncdn.com" in u and (".m3u8" in u or ".mp4" in u):
                    captured["video_url"] = u
                    captured["t_url"] = time.time() - t0_pw
                    print(f"{_ts()} [PLAYWRIGHT] URL vidéo: {u[:90]}...")
                    url_ready.set()  # signaler immédiatement au thread principal

            page.on("request", on_request)

            # Navigation principale
            print(f"{_ts()} [PLAYWRIGHT] Navigation vers {shorties_url}")
            await page.goto(shorties_url, timeout=30000, wait_until="domcontentloaded")
            print(f"{_ts()} [PLAYWRIGHT] domcontentloaded")

            # Fallback age gate (si pornhub_av absent des cookies)
            try:
                ag = await page.query_selector(
                    "a.ageGateLink,a:has-text('I am 18'),"
                    "button:has-text('I am 18'),.ageGate a[href*='enter']"
                )
                if ag:
                    print(f"{_ts()} [PLAYWRIGHT] Age gate — clic + re-navigation")
                    await ag.click()
                    # Pas de wait_for_load_state — goto gère la navigation directement
                    await page.goto(shorties_url, timeout=30000, wait_until="domcontentloaded")
                    print(f"{_ts()} [PLAYWRIGHT] Page chargée après age gate")
            except Exception:
                pass

            # Fallback cookie banner
            try:
                btn = await page.query_selector("button:has-text('Accept All Cookies')")
                if btn:
                    await btn.click()
                    print(f"{_ts()} [PLAYWRIGHT] Cookie banner accepté")
                else:
                    ok = await page.query_selector("button:has-text('Ok')")
                    if ok:
                        await ok.click()
                        await _aio.sleep(0.2)
                        accept = await page.query_selector("button:has-text('Accept All Cookies')")
                        if accept:
                            await accept.click()
                            print(f"{_ts()} [PLAYWRIGHT] Cookie banner accepté (2 étapes)")
            except Exception:
                pass

            print(f"{_ts()} [PLAYWRIGHT] En attente URL vidéo…")

            # Titre
            try:
                captured["title"] = (await page.title()).split("|")[0].strip()
            except Exception:
                captured["title"] = shorties_url.rstrip("/").split("/")[-1]

            # Poll 100ms — résout dès que l'URL est capturée, max 12s
            deadline = time.time() + 12
            while "video_url" not in captured and time.time() < deadline:
                await _aio.sleep(0.1)

            # Fallback DOM si poll timeout sans URL
            if "video_url" not in captured:
                try:
                    src = await page.evaluate(
                        "() => { const v = document.querySelector('video'); "
                        "return v ? (v.src || v.currentSrc || '') : ''; }"
                    )
                    if src:
                        captured["video_url"] = src
                        captured["t_url"] = time.time() - t0_pw
                        url_ready.set()
                        print(f"{_ts()} [PLAYWRIGHT] Video src DOM: {src[:90]}...")
                except Exception:
                    pass

            url_ready.set()  # même si vide, débloquer le thread principal
            # Laisser le browser se fermer en arrière-plan (thread daemon)

    exc_box: dict = {}

    def _thread():
        try:
            asyncio.run(run())
        except Exception as e:
            exc_box["error"] = e
        finally:
            url_ready.set()  # garantir le déblocage en cas d'exception

    t = threading.Thread(target=_thread, daemon=True)
    t.start()

    # Attendre UNIQUEMENT jusqu'à ce que l'URL soit capturée (max 30s)
    # Ne pas attendre la fermeture du browser — le thread daemon s'en charge
    url_ready.wait(timeout=30)

    elapsed = time.time() - t0_pw
    print(f"[PLAYWRIGHT] Handoff à yt-dlp après {elapsed:.2f}s")

    if "error" in exc_box:
        print(f"[ERROR] Playwright: {exc_box['error']}")

    return captured


# ------------------------------------------------------------------
# Trim
# ------------------------------------------------------------------

# def _trim_start(video_path: Path, seconds: int = 3) -> Path | None:
#     if not FFMPEG_BIN:
#         print("[WARN] ffmpeg introuvable — trim ignoré")
#         return None

#     trimmed = video_path.with_stem(video_path.stem + "_trim")
#     # -ss AVANT -i = input seeking : coupe au keyframe le plus proche,
#     # évite les frames noires causées par l'absence de frames de référence.
#     cmd = [FFMPEG_BIN, "-ss", str(seconds), "-i", str(video_path),
#            "-c", "copy", str(trimmed), "-y", "-loglevel", "error"]

#     print(f"Trim des {seconds} premières secondes...")
#     if subprocess.run(cmd).returncode == 0:
#         video_path.unlink()
#         trimmed.rename(video_path)
#         print(f"Fichier final : {video_path}")
#         return video_path

#     print("[WARN] ffmpeg a échoué — fichier original conservé")
#     trimmed.unlink(missing_ok=True)
#     return None


# ------------------------------------------------------------------
# Point d'entrée principal
# ------------------------------------------------------------------

def download(url: str, output_dir: Path = DOWNLOADS_DIR, filename: str | None = None, concurrent_fragments: int = 8) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)

    vkey = _extract_vkey(url)
    safe_title = filename or vkey
    output_tmpl = str(output_dir / f"{safe_title}.%(ext)s")

    print(f"\n{'='*60}")
    print(f"Vidéo  : {url}")
    print(f"Dossier: {output_dir}")
    print(f"{'='*60}\n")

    t_start = time.time()

    # Garantir des cookies frais avant tout téléchargement
    ensure_fresh()

    # --- Fast path : yt-dlp sans browser ---
    if _try_ytdlp_direct(url, output_tmpl, concurrent_fragments):
        matches = sorted(output_dir.glob(f"{safe_title}.*"), key=lambda f: f.stat().st_mtime, reverse=True)
        if matches:
            # _trim_start(matches[0])
            # print(f"\nTerminé en {time.time()-t_start:.1f}s")
            return matches[0]

    # --- Fallback : Playwright ---
    print("[FALLBACK] yt-dlp direct échoué — lancement Playwright...")
    result = _intercept_via_playwright(url)
    video_url = result.get("video_url")

    if not video_url:
        print("[ERROR] URL vidéo introuvable")
        return None

    title = result.get("title") or vkey
    safe_title = filename or "".join(c for c in title if c.isalnum() or c in " -_").strip() or vkey
    output_tmpl = str(output_dir / f"{safe_title}.%(ext)s")

    print(f"\nTitre  : {title}")
    print(f"Stream : {video_url[:80]}...\n")

    cookies_file = _cookies_to_netscape()
    cmd = [
        YTDLP_BIN, video_url,
        "--output", output_tmpl,
        "--referer", url,
        "--add-header", "Origin:https://www.pornhub.com",
        "--concurrent-fragments", str(concurrent_fragments),
        "--no-playlist", "--progress",
    ]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]

    print("Téléchargement en cours...")
    if subprocess.run(cmd).returncode != 0:
        print("[ERROR] yt-dlp a échoué")
        return None

    matches = sorted(output_dir.glob(f"{safe_title}.*"), key=lambda f: f.stat().st_mtime, reverse=True)
    if matches:
        # _trim_start(matches[0])
        print(f"\nTerminé en {time.time()-t_start:.1f}s")
        return matches[0]

    return None


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts: dict = {}
    i = 1
    while i < len(sys.argv):
        if sys.argv[i].startswith("--") and i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
            opts[sys.argv[i]] = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not args:
        print(__doc__)
        sys.exit(1)

    result = download(
        url=args[0],
        output_dir=Path(opts.get("--output", DOWNLOADS_DIR)),
        filename=opts.get("--filename"),
        concurrent_fragments=int(opts.get("--fragments", 8)),
    )
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
