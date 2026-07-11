"""
Gestion des credentials Pornhub (token + cookies).
Partagé entre run.py et downloader.py.
"""

import json
import asyncio
import threading
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
CREDENTIALS_DIR = PROJECT_DIR / "credentials"

# Durée max avant de considérer les cookies comme périmés (secondes)
COOKIE_MAX_AGE = 6 * 3600  # 6 heures


def are_fresh(max_age: int = COOKIE_MAX_AGE) -> bool:
    """Retourne True si cookies.json existe et a moins de max_age secondes."""
    cookies_file = CREDENTIALS_DIR / "cookies.json"
    if not cookies_file.exists():
        return False
    age = time.time() - cookies_file.stat().st_mtime
    return age < max_age


def ensure_fresh(max_age: int = COOKIE_MAX_AGE) -> bool:
    """Refresh les cookies s'ils sont absents ou périmés. Retourne True si OK."""
    if are_fresh(max_age):
        return True
    age_str = ""
    cookies_file = CREDENTIALS_DIR / "cookies.json"
    if cookies_file.exists():
        age_min = int((time.time() - cookies_file.stat().st_mtime) / 60)
        age_str = f" (âge: {age_min}min)"
    print(f"[AUTH] Cookies périmés{age_str} — refresh automatique...")
    return refresh()


def refresh() -> bool:
    """Récupère un token et des cookies frais via Playwright. Retourne True si réussi."""
    print("[AUTH] Rafraîchissement des credentials via Playwright...")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[AUTH] Playwright non installé: pip install playwright && playwright install chromium")
        return False

    captured: dict = {}

    async def fetch():
        import asyncio as _aio
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/142.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            def on_response(response):
                if "shorties/get" in response.url and "token" not in captured:
                    if "token=" in response.url:
                        try:
                            captured["token"] = response.url.split("token=")[1].split("&")[0]
                            print("[AUTH] Token intercepté")
                        except Exception:
                            pass

            page.on("response", on_response)

            print("[AUTH] Navigation vers Pornhub shorties...")
            await page.goto("https://www.pornhub.com/shorties/", timeout=30000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)

            # Gérer l'age gate pour obtenir le cookie pornhub_av dans la session
            try:
                ag = await page.query_selector(
                    "a.ageGateLink, a:has-text('I am 18'), button:has-text('I am 18')"
                )
                if ag:
                    print("[AUTH] Age gate détecté — clic pour obtenir pornhub_av cookie")
                    await ag.click()
                    await page.wait_for_load_state("domcontentloaded")
                    # Re-naviguer vers shorties pour déclencher le token
                    await page.goto("https://www.pornhub.com/shorties/", timeout=30000)
                    await page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass

            # Gérer le cookie banner
            try:
                btn = await page.query_selector("button:has-text('Accept All Cookies')")
                if btn:
                    await btn.click()
            except Exception:
                pass

            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await _aio.sleep(2)

            captured["cookies"] = await context.cookies()

            # Vérifier que pornhub_av est bien présent
            av = next((c for c in captured["cookies"] if c["name"] == "pornhub_av"), None)
            if av:
                print(f"[AUTH] Cookie pornhub_av capturé ✓ (value={av['value']})")
            else:
                print("[AUTH] Warning: pornhub_av absent — age gate sera toujours visible")

            await browser.close()

    exc_box: dict = {}

    def _thread():
        try:
            asyncio.run(fetch())
        except Exception as e:
            exc_box["error"] = e

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    t.join(timeout=60)

    if "error" in exc_box:
        print(f"[AUTH] Erreur Playwright: {exc_box['error']}")
        return False

    CREDENTIALS_DIR.mkdir(exist_ok=True)

    token = captured.get("token")
    cookies_list = captured.get("cookies", [])

    if token:
        (CREDENTIALS_DIR / "token.txt").write_text(token, encoding="utf-8")
        print(f"[AUTH] Token sauvegardé ({token[:25]}...)")

    if cookies_list:
        (CREDENTIALS_DIR / "cookies.json").write_text(
            json.dumps(cookies_list, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[AUTH] {len(cookies_list)} cookies sauvegardés")

    return bool(cookies_list)
