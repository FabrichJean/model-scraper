#!/usr/bin/env python3
"""
Serveur HTTP — Pornhub Shorties Downloader
Usage: python3 server.py [--port 8080]
"""

import sys
import json
import uuid
import queue
import asyncio
import threading
import subprocess
import shutil
import re
import time
from pathlib import Path
from flask import Flask, request, Response, stream_with_context

PROJECT_DIR = Path(__file__).parent
DOWNLOADS_DIR = PROJECT_DIR / "downloads"
WEB_DIR = PROJECT_DIR / "web"
YTDLP_BIN = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
FFMPEG_BIN = shutil.which("ffmpeg")
FFPROBE_BIN = shutil.which("ffprobe")


def _has_videotoolbox() -> bool:
    if not FFMPEG_BIN:
        return False
    try:
        out = subprocess.run([FFMPEG_BIN, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=10).stdout
        return "h264_videotoolbox" in out
    except Exception:
        return False


HW_ENCODE = _has_videotoolbox()


def _source_bitrate(src: Path, default: int = 4_000_000) -> int:
    """Bitrate vidéo de la source (bits/s), pour ré-encoder sans gonfler le fichier."""
    if not FFPROBE_BIN:
        return default
    try:
        out = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=bit_rate", "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        bitrate = int(out)
        return max(bitrate, 800_000)  # plancher raisonnable pour rester lisible
    except Exception:
        return default


def _video_duration(src: Path) -> float:
    if not FFPROBE_BIN:
        return 0.0
    try:
        out = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


# Durée sous laquelle on ré-encode intégralement (coût négligeable, quelques
# secondes) plutôt que de faire un simple stream copy.
_REENCODE_MAX_DURATION = 180  # secondes


def _trim_video(src: Path, dst: Path, seconds: int = 3) -> bool:
    """
    Coupe les `seconds` premières secondes.

    - Clips courts (shorts, <= _REENCODE_MAX_DURATION) : RÉ-ENCODAGE complet.
      Un simple stream copy avec -ss en entrée tombe en théorie pile sur la
      keyframe déclarée, mais si la source utilise des GOP "ouverts" (B-frames
      référençant la dernière image du GOP précédent), les premières frames
      après la coupe peuvent perdre leur référence -> vidéo figée en début de
      lecture malgré la keyframe correcte. Le ré-encodage élimine ce risque,
      et pour un clip court c'est quasi instantané.
    - Vidéos longues : stream copy (rapide). Un ré-encodage complet d'une
      vidéo de 15+ minutes peut prendre plusieurs minutes sans aucun retour de
      progression, ce qui bloque le job côté UI ("le log se fige"). -ss placé
      AVANT -i (input seeking) aligne déjà correctement la coupe sur la
      keyframe la plus proche, ce qui suffit dans l'immense majorité des cas.
    """
    duration = _video_duration(src)

    if 0 < duration <= _REENCODE_MAX_DURATION:
        attempts = []
        if HW_ENCODE:
            attempts.append(["-c:v", "h264_videotoolbox", "-b:v", str(_source_bitrate(src))])
        attempts.append(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
        for vcodec in attempts:
            cmd = [FFMPEG_BIN, "-ss", str(seconds), "-i", str(src), *vcodec,
                   "-c:a", "aac", "-b:a", "160k", str(dst), "-y", "-loglevel", "error"]
            if subprocess.run(cmd).returncode == 0:
                return True
        return False

    cmd = [FFMPEG_BIN, "-ss", str(seconds), "-i", str(src), "-c", "copy",
           str(dst), "-y", "-loglevel", "error"]
    return subprocess.run(cmd).returncode == 0


app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
jobs: dict = {}        # job_id → download job
model_jobs: dict = {}  # job_id → model scan job


# Le frontend (web/) peut être servi depuis une autre origine que cette API
# (autre port, autre domaine) — sans ces en-têtes, fetch()/EventSource seraient
# bloqués par le navigateur en cross-origin.
@app.after_request
def _add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ─────────────────────────────────────────────────────────────
# Helpers communs
# ─────────────────────────────────────────────────────────────

def _new_job(extra=None):
    j = {"status": "pending", "progress": 0, "logs": [], "queue": queue.Queue()}
    if extra:
        j.update(extra)
    return j


def _sse_stream(job, terminal_types=("done", "error")):
    """Générateur SSE : ping immédiat pour forcer le flush, puis replay + nouveaux events."""
    yield 'data: {"type":"ping"}\n\n'  # force flush dès la connexion
    for entry in list(job["logs"]):
        yield f"data: {json.dumps(entry)}\n\n"
    if job["progress"] > 0:
        yield f"data: {json.dumps({'type':'progress','value':job['progress']})}\n\n"
    if job["status"] in ("done", "error"):
        payload = {"type": job["status"]}
        # Inclure tous les champs utiles pour la reconnexion
        for k in ("file", "videos", "msg", "subscribers", "video_count"):
            if job.get(k):
                payload[k] = job[k]
        yield f"data: {json.dumps(payload)}\n\n"
        return
    q: queue.Queue = job["queue"]
    while True:
        try:
            ev = q.get(timeout=25)
            yield f"data: {json.dumps(ev)}\n\n"
            if ev.get("type") in terminal_types:
                break
        except queue.Empty:
            yield 'data: {"type":"ping"}\n\n'


def _sanitize_log_line(line: str) -> str:
    """Retire les chemins absolus locaux (contiennent le nom d'utilisateur système,
    l'arborescence de la machine…) des lignes de sortie yt-dlp/ffmpeg avant envoi au
    navigateur — ne garder que le nom de fichier."""
    for base in (DOWNLOADS_DIR, PROJECT_DIR):
        line = line.replace(str(base) + "/", "")
    return line


def _stream_cmd(cmd, log_fn, progress_fn):
    """Lance un subprocess, streame chaque ligne. Retourne (success, auth_error)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    auth_error = playlist_empty = False
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        line = _sanitize_log_line(line)
        log_fn(line)
        m = re.search(r'(\d+\.?\d*)%\s+of', line)
        if m:
            progress_fn(min(float(m.group(1)), 94))
        low = line.lower()
        if any(k in low for k in ("403", "login required", "premium", "unauthorized")):
            auth_error = True
        if "downloading 0 items" in low or "0 items of" in low:
            playlist_empty = True
    proc.wait()
    return proc.returncode == 0 and not playlist_empty, auth_error


# ─────────────────────────────────────────────────────────────
# Download job
# ─────────────────────────────────────────────────────────────

def _run_download_job(url: str, job_id: str):
    job = jobs[job_id]
    q = job["queue"]

    def log(msg, level="info"):
        e = {"type": "log", "level": level, "msg": msg, "t": time.strftime("%H:%M:%S")}
        job["logs"].append(e); q.put(e)

    def progress(val):
        job["progress"] = val
        q.put({"type": "progress", "value": val})

    def done(filename):
        job.update(status="done", file=filename)
        progress(100)
        q.put({"type": "done", "file": filename})

    def error(msg):
        job["status"] = "error"
        q.put({"type": "error", "msg": msg})

    try:
        job["status"] = "running"
        DOWNLOADS_DIR.mkdir(exist_ok=True)

        log("Vérification des credentials…")
        from credentials import ensure_fresh, refresh as do_refresh
        ensure_fresh()
        log("Credentials OK ✓")

        from downloader import _cookies_to_netscape, _extract_vkey
        vkey = _extract_vkey(url)
        output_tmpl = str(DOWNLOADS_DIR / f"{vkey}.%(ext)s")

        cookies_file = _cookies_to_netscape()

        # ── yt-dlp direct ──────────────────────────────────────
        log(f"Téléchargement: {url}")
        cmd = [YTDLP_BIN, url, "--output", output_tmpl,
               "--concurrent-fragments", "8", "--no-playlist", "--newline"]
        if cookies_file:
            cmd += ["--cookies", str(cookies_file)]

        success, auth_err = _stream_cmd(cmd, log, progress)

        if not success and auth_err:
            log("Erreur auth — refresh…", "warn")
            if do_refresh():
                cookies_file = _cookies_to_netscape()
                if cookies_file:
                    try: cmd[cmd.index("--cookies") + 1] = str(cookies_file)
                    except ValueError: cmd += ["--cookies", str(cookies_file)]
                log("Retry…")
                success, _ = _stream_cmd(cmd, log, progress)

        # ── Fallback Playwright ─────────────────────────────────
        if not success:
            log("yt-dlp direct échoué — Playwright…", "warn")
            from downloader import _intercept_via_playwright
            result = _intercept_via_playwright(url)
            video_url = result.get("video_url")
            if not video_url:
                error("URL vidéo introuvable"); return

            log(f"URL interceptée: {video_url[:70]}…")
            title = result.get("title") or vkey
            safe = "".join(c for c in title if c.isalnum() or c in " -_").strip() or vkey
            output_tmpl = str(DOWNLOADS_DIR / f"{safe}.%(ext)s")
            vkey = safe

            cmd2 = [YTDLP_BIN, video_url, "--output", output_tmpl,
                    "--referer", url, "--add-header", "Origin:https://www.pornhub.com",
                    "--concurrent-fragments", "8", "--no-playlist", "--newline"]
            if cookies_file:
                cmd2 += ["--cookies", str(cookies_file)]
            success, _ = _stream_cmd(cmd2, log, progress)

        if not success:
            error("Téléchargement échoué"); return

        matches = sorted(DOWNLOADS_DIR.glob(f"{vkey}.*"),
                         key=lambda f: f.stat().st_mtime, reverse=True)
        if not matches:
            error("Fichier introuvable"); return

        dl = matches[0]
        log(f"Fichier: {dl.name} ({dl.stat().st_size // 1024} Ko)")
        progress(95)

        if FFMPEG_BIN:
            duration = _video_duration(dl)
            if 0 < duration <= _REENCODE_MAX_DURATION:
                log("Trim 3 secondes (pornhub intro)…")
            else:
                log("Trim 3 secondes (pornhub intro, copie rapide — vidéo longue)…")
            trimmed = dl.with_stem(dl.stem + "_trim")
            if _trim_video(dl, trimmed, seconds=3):
                dl.unlink(); trimmed.rename(dl)
                log("Trim OK ✓")
            else:
                log("Trim échoué — original conservé", "warn")
                trimmed.unlink(missing_ok=True)

        log(f"Terminé ✓  →  {dl.name}", "success")
        done(dl.name)

    except Exception as exc:
        log(f"Exception: {exc}", "error")
        error(str(exc))


# ─────────────────────────────────────────────────────────────
# Model scan job
# ─────────────────────────────────────────────────────────────

def _run_model_scan_job(model_url: str, job_id: str):
    job = model_jobs[job_id]
    q = job["queue"]

    def log(msg, level="info"):
        e = {"type": "log", "level": level, "msg": msg, "t": time.strftime("%H:%M:%S")}
        job["logs"].append(e); q.put(e)

    try:
        job["status"] = "running"
        log("Vérification des credentials…")
        from credentials import ensure_fresh
        ensure_fresh()
        log("Credentials OK ✓")

        from playwright.async_api import async_playwright
        from downloader import _load_playwright_cookies, _BLOCK_EXTENSIONS
        pw_cookies = _load_playwright_cookies()
        captured = {}

        async def run():
            import asyncio as _aio
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 390, "height": 844},
                    user_agent=(
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/17.0 Mobile/15E148 Safari/604.1"
                    ),
                    is_mobile=True,
                )
                bypass = [
                    {"name": "pornhub_av",    "value": "1", "domain": ".pornhub.com", "path": "/"},
                    {"name": "cookieConsent", "value": "1", "domain": ".pornhub.com", "path": "/"},
                ]
                await context.add_cookies(bypass + (pw_cookies or []))
                page = await context.new_page()

                async def block(route):
                    ext = Path(route.request.url.split("?")[0]).suffix.lower()
                    await route.abort() if ext in _BLOCK_EXTENSIONS else await route.continue_()
                await page.route("**/*", block)

                # Intercept userProfile POST
                async def on_response(resp):
                    if "shorties/userProfile" in resp.url and "data" not in captured:
                        try:
                            captured["data"] = await resp.json()
                            log(f"userProfile reçu: {len(captured['data'].get('videoList',[]))} vidéos")
                        except Exception as e:
                            log(f"Parse error: {e}", "warn")

                page.on("response", on_response)

                # Step A: /videos pour trouver le lien shorties
                videos_url = model_url.rstrip("/") + "/videos"
                log(f"Navigation: {videos_url}")
                await page.goto(videos_url, timeout=30000, wait_until="domcontentloaded")

                try:
                    btn = await page.query_selector("button:has-text('Accept All Cookies')")
                    if btn: await btn.click()
                except Exception: pass

                await page.wait_for_load_state("networkidle")

                for _ in range(4):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await _aio.sleep(0.7)

                try:
                    await page.wait_for_selector("a.modelShorties__redirect__link", timeout=12000)
                    shorties_href = await page.get_attribute("a.modelShorties__redirect__link", "href")
                    log(f"Lien trouvé: {shorties_href}")
                except Exception:
                    log("modelShorties__redirect__link introuvable", "error")
                    await browser.close(); return

                captured["shorties_url"] = shorties_href

                # Step B: page shorties#openProfile → userProfile XHR
                log(f"Navigation: {shorties_href}")
                await page.goto(shorties_href, timeout=30000, wait_until="networkidle")
                await _aio.sleep(3)
                await browser.close()

        exc_box = {}
        def thread_fn():
            try: asyncio.run(run())
            except Exception as e: exc_box["error"] = e

        t = threading.Thread(target=thread_fn, daemon=True)
        t.start(); t.join(timeout=120)

        if "error" in exc_box:
            log(f"Erreur: {exc_box['error']}", "error")
            job["status"] = "error"
            q.put({"type": "error"}); return

        api = captured.get("data", {})
        videos = api.get("videoList", [])
        job.update(
            status="done",
            videos=videos,
            subscribers=api.get("subscribersNumber", ""),
            video_count=api.get("videoCount", ""),
            shorties_url=captured.get("shorties_url", ""),
        )
        log(f"Terminé ✓  {len(videos)} vidéos trouvées", "success")
        q.put({"type": "done", "count": len(videos),
               "videos": videos,
               "subscribers": api.get("subscribersNumber", ""),
               "video_count": api.get("videoCount", "")})

    except Exception as exc:
        log(f"Exception: {exc}", "error")
        job["status"] = "error"
        q.put({"type": "error"})


# ─────────────────────────────────────────────────────────────
# Frontend statique (web/)
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")


# ─────────────────────────────────────────────────────────────
# API — santé (le frontend l'utilise pour choisir quelle base d'API
# utiliser quand plusieurs sont configurées, cf. web/app.js)
# ─────────────────────────────────────────────────────────────

@app.route("/api/ping")
def api_ping():
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# API — download
# ─────────────────────────────────────────────────────────────

@app.route("/api/start", methods=["POST"])
def api_start():
    """Démarre un téléchargement (short ou vidéo complète) et retourne le job_id."""
    url = request.form.get("url", "").strip()
    from downloader import is_supported_url
    if not url or not is_supported_url(url):
        return {"error": "URL invalide"}, 400
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = _new_job({"url": url, "file": None})
    threading.Thread(target=_run_download_job, args=(url, job_id), daemon=True).start()
    return {"job_id": job_id}


@app.route("/stream/<job_id>")
def stream(job_id):
    if job_id not in jobs:
        return Response("", mimetype="text/event-stream")
    return Response(stream_with_context(_sse_stream(jobs[job_id])),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/file/<filename>")
def serve_file(filename):
    from flask import send_from_directory
    return send_from_directory(DOWNLOADS_DIR, filename, as_attachment=True)


# ─────────────────────────────────────────────────────────────
# API — scan des shorts d'un modèle
# ─────────────────────────────────────────────────────────────

@app.route("/api/model/scan", methods=["POST"])
def api_model_scan():
    """Démarre le scan des shorts d'un modèle et retourne le job_id."""
    url = request.form.get("url", "").strip()
    if not url or "pornhub.com/" not in url:
        return {"error": "URL invalide"}, 400
    job_id = str(uuid.uuid4())[:8]
    model_jobs[job_id] = _new_job({"url": url, "videos": [], "subscribers": "", "video_count": ""})
    threading.Thread(target=_run_model_scan_job, args=(url, job_id), daemon=True).start()
    return {"job_id": job_id}


@app.route("/stream/model/<job_id>")
def stream_model(job_id):
    if job_id not in model_jobs:
        return Response("", mimetype="text/event-stream")
    return Response(stream_with_context(_sse_stream(model_jobs[job_id])),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ─────────────────────────────────────────────────────────────
# API — recherche de modèle par mot-clé
# ─────────────────────────────────────────────────────────────

@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    if not query:
        return {"models": [], "videos": []}
    try:
        from model_search import search_models, search_videos
        return {"models": search_models(query), "videos": search_videos(query)}
    except Exception as exc:
        return {"error": str(exc)}, 500


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = 8080
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    print(f"\n  Pornhub Downloader  →  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
