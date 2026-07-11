"""
Spider pour scraper le profil d'un modèle Pornhub.

Usage:
    scrapy crawl model_profile -a url="https://www.pornhub.com/model/xxx"
    scrapy crawl model_profile -a url="https://www.pornhub.com/pornstar/yyy"
    scrapy crawl model_profile -a url="https://www.pornhub.com/channels/zzz"
"""

import json
import re
from datetime import datetime
from pathlib import Path

import scrapy

from ..items import ModelProfileItem


CREDENTIALS_DIR = Path(__file__).parent.parent.parent / "credentials"


class ModelProfileSpider(scrapy.Spider):
    name = "model_profile"
    allowed_domains = ["pornhub.com"]
    handle_httpstatus_list = [403, 429, 503]

    def __init__(self, url=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not url:
            raise ValueError("Paramètre 'url' requis. Ex: -a url=https://www.pornhub.com/model/xxx")
        self.target_url = url.strip()

    # ------------------------------------------------------------------
    # Chargement des credentials (même stratégie que le projet principal)
    # ------------------------------------------------------------------

    def _load_token(self) -> str:
        token_file = CREDENTIALS_DIR / "token.txt"
        if token_file.exists():
            token = token_file.read_text(encoding="utf-8").strip()
            if token:
                self.logger.info(f"[AUTH] Token chargé ({token[:20]}...)")
                return token
        self.logger.warning("[AUTH] token.txt absent ou vide")
        return ""

    def _load_cookies(self) -> dict:
        """Retourne {name: value} pour les headers HTTP Scrapy."""
        raw_data = self._load_cookies_raw()
        if isinstance(raw_data, list):
            return {c["name"]: c["value"] for c in raw_data}
        return raw_data  # ancien format dict

    def _load_cookies_raw(self):
        """Retourne les cookies bruts depuis cookies.json (list ou dict selon version)."""
        cookies_file = CREDENTIALS_DIR / "cookies.json"
        if cookies_file.exists():
            raw = cookies_file.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                self.logger.info(f"[AUTH] {len(data)} cookies chargés")
                return data
        self.logger.warning("[AUTH] cookies.json absent ou vide")
        return []

    def _playwright_cookies(self) -> list:
        """Retourne les cookies au format Playwright add_cookies() avec attributs complets."""
        raw = self._load_cookies_raw()
        if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "domain" in raw[0]:
            # Nouveau format : objets complets sauvegardés par run.py
            return [
                {k: v for k, v in c.items() if k in ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite")}
                for c in raw
            ]
        # Ancien format dict {name: value} — injecter avec domaine par défaut
        cookies_dict = raw if isinstance(raw, dict) else {c["name"]: c["value"] for c in raw}
        return [
            {"name": k, "value": v, "domain": ".pornhub.com", "path": "/"}
            for k, v in cookies_dict.items()
        ]

    def _build_headers(self, referer: str = "https://www.pornhub.com/") -> dict:
        return {
            "authority": "www.pornhub.com",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            # Exclure 'br' (brotli) — non installé, causerait une réponse non décodable
            "accept-encoding": "gzip, deflate",
            "cache-control": "no-cache",
            "dnt": "1",
            "pragma": "no-cache",
            "referer": referer,
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36"
            ),
        }

    # ------------------------------------------------------------------
    # Entrée
    # ------------------------------------------------------------------

    async def start(self):
        cookies = self._load_cookies()
        headers = self._build_headers()

        self.logger.info(f"Scraping: {self.target_url}")

        yield scrapy.Request(
            url=self.target_url,
            callback=self.parse_profile,
            cookies=cookies,
            headers=headers,
            dont_filter=True,
            meta={"url": self.target_url},
        )

    # ------------------------------------------------------------------
    # Parsing principal
    # ------------------------------------------------------------------

    def parse_profile(self, response):
        url = response.meta["url"]

        if response.status in [403, 429, 503]:
            self.logger.error(f"HTTP {response.status} — credentials invalides ou rate-limit")
            return

        try:
            page_text = response.text
        except AttributeError:
            self.logger.error("Réponse non décodable (encodage non supporté). Installer brotli: pip install brotlicffi")
            return

        self.logger.info(f"Page reçue: {response.status} ({len(page_text)} chars)")

        item = ModelProfileItem()
        item["url"] = url
        item["scraped_at"] = datetime.now().isoformat()

        # Détecter le type de page depuis l'URL
        item["model_type"] = self._detect_model_type(url)

        # --- Nom / username ---
        item["name"] = self._extract_name(response)
        item["username"] = self._extract_username(response, url)

        # --- Bio ---
        item["bio"] = self._extract_bio(response)

        # --- Images ---
        item["avatar_url"] = self._extract_avatar(response)
        item["cover_url"] = self._extract_cover(response)

        # --- Stats ---
        stats = self._extract_stats(response)
        item["views"] = stats.get("views")
        item["subscribers"] = stats.get("subscribers")
        item["videos_count"] = stats.get("videos")
        item["photos_count"] = stats.get("photos")
        item["rank"] = stats.get("rank")
        item["raw_stats"] = stats

        # --- Infos personnelles ---
        item["country"] = self._extract_country(response)
        item["gender"] = self._extract_gender(response)
        item["age"] = self._extract_age(response)
        item["tags"] = self._extract_tags(response)

        self.logger.info(
            f"Profil extrait: {item.get('name')} | "
            f"vues={item.get('views')} | "
            f"subs={item.get('subscribers')} | "
            f"vidéos={item.get('videos_count')}"
        )

        # Étape 2 : Playwright → /videos → lien modelShorties__redirect__link → userProfile XHR
        videos_url = self.target_url.rstrip("/") + "/videos"
        api_data = self._playwright_videos_and_userprofile(videos_url)

        item["videos_page_url"] = api_data.get("_shorties_url", "")
        item["videos"] = api_data.get("videoList", [])
        # Stocker uniquement les métadonnées légères, pas la videoList entière
        item["userprofile_api"] = {
            k: v for k, v in api_data.items()
            if k not in ("videoList",)
        }

        # Enrichir les stats avec les valeurs formatées de l'API si disponibles
        if "subscribersNumber" in api_data:
            item["subscribers_label"] = api_data["subscribersNumber"]  # ex: "78.9K Subscribers"
        if "videoCount" in api_data:
            item["videos_count_label"] = api_data["videoCount"]        # ex: "211 Videos"

        self.logger.info(
            f"userProfile capturé — {len(item['videos'])} vidéos | "
            f"{api_data.get('subscribersNumber', '')} | {api_data.get('videoCount', '')}"
        )

        yield item

    # ------------------------------------------------------------------
    # Playwright : /videos → a.modelShorties__redirect__link → userProfile XHR
    # ------------------------------------------------------------------

    def _playwright_videos_and_userprofile(self, videos_url: str) -> dict:
        """
        1. Navigue vers /videos (JS requis pour rendre a.modelShorties__redirect__link)
        2. Attend l'élément, lit son href (ex: /shorties/6a07cc4397f88#openProfile)
        3. Navigue vers ce href dans la même session
        4. Intercepte le XHR /shorties/userProfile déclenché par #openProfile
        5. Retourne le JSON capturé
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.logger.error(
                "Playwright non installé: pip install playwright && playwright install chromium"
            )
            return {}

        pw_cookies = self._playwright_cookies()
        captured = {}
        data_ready = __import__("threading").Event()

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

                # Injecter cookies + bypass age gate / cookie banner
                all_cookies = list(pw_cookies or [])
                if not any(c.get("name") == "pornhub_av" for c in all_cookies):
                    all_cookies += [
                        {"name": "pornhub_av",    "value": "1", "domain": ".pornhub.com", "path": "/"},
                        {"name": "cookieConsent", "value": "1", "domain": ".pornhub.com", "path": "/"},
                    ]
                await context.add_cookies(all_cookies)
                self.logger.info(f"[PLAYWRIGHT] {len(all_cookies)} cookies injectés")

                page = await context.new_page()

                # --- Étape A : /videos — attendre le lien rendu par JS ---
                self.logger.info(f"[PLAYWRIGHT] Navigation vers {videos_url}")
                await page.goto(videos_url, timeout=30000, wait_until="domcontentloaded")

                # Fallback age gate (si pornhub_av insuffisant)
                try:
                    ag = await page.query_selector(
                        "a.ageGateLink,a:has-text('I am 18'),button:has-text('I am 18')"
                    )
                    if ag:
                        self.logger.info("[PLAYWRIGHT] Age gate — clic + re-navigation")
                        await ag.click()
                        await page.goto(videos_url, timeout=30000, wait_until="domcontentloaded")
                except Exception:
                    pass

                # Cookie banner
                try:
                    btn = await page.query_selector("button:has-text('Accept All Cookies')")
                    if btn:
                        await btn.click()
                    else:
                        ok = await page.query_selector("button:has-text('Ok')")
                        if ok:
                            await ok.click()
                            await _aio.sleep(0.2)
                            accept = await page.query_selector("button:has-text('Accept All Cookies')")
                            if accept:
                                await accept.click()
                except Exception:
                    pass

                # Scroll pour déclencher le lazy-loading du composant mobile
                for _ in range(4):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await _aio.sleep(0.7)

                try:
                    await page.wait_for_selector("a.modelShorties__redirect__link", timeout=12000)
                    shorties_href = await page.get_attribute("a.modelShorties__redirect__link", "href")
                    self.logger.info(f"[PLAYWRIGHT] Lien trouvé: {shorties_href}")
                except Exception:
                    self.logger.error("[PLAYWRIGHT] a.modelShorties__redirect__link introuvable après 12s")
                    data_ready.set()
                    return

                captured["_shorties_url"] = shorties_href

                # --- Étape B : shorties/xxx#openProfile — intercepter userProfile POST ---
                async def on_response(resp):
                    if "shorties/userProfile" in resp.url and "data" not in captured:
                        try:
                            captured["data"] = await resp.json()
                            self.logger.info(f"[PLAYWRIGHT] userProfile capturé: {resp.url}")
                            data_ready.set()  # signaler immédiatement
                        except Exception as exc:
                            self.logger.warning(f"[PLAYWRIGHT] JSON parse error: {exc}")

                page.on("response", on_response)

                self.logger.info(f"[PLAYWRIGHT] Navigation vers {shorties_href}")
                await page.goto(shorties_href, timeout=30000, wait_until="domcontentloaded")

                # Poll jusqu'à réception des données, max 12s
                deadline = __import__("time").time() + 12
                while "data" not in captured and __import__("time").time() < deadline:
                    await _aio.sleep(0.1)

                data_ready.set()  # débloquer même si vide
                # Browser se ferme en arrière-plan (daemon thread)

        import asyncio
        import threading

        exc_box: dict = {}

        def thread_target():
            try:
                asyncio.run(run())
            except Exception as exc:
                exc_box["error"] = exc
            finally:
                data_ready.set()

        t = threading.Thread(target=thread_target, daemon=True)
        t.start()

        # Attendre uniquement jusqu'à ce que les données soient prêtes
        data_ready.wait(timeout=60)

        if "error" in exc_box:
            self.logger.error(f"[PLAYWRIGHT] Erreur thread: {exc_box['error']}")
            return {}

        result = dict(captured.get("data") or {})
        if "_shorties_url" in captured:
            result["_shorties_url"] = captured["_shorties_url"]
        return result

    # ------------------------------------------------------------------
    # Extracteurs individuels (avec fallbacks multiples)
    # ------------------------------------------------------------------

    def _detect_model_type(self, url: str) -> str:
        for segment in ["model", "pornstar", "channels", "user", "creator"]:
            if f"/{segment}/" in url:
                return segment
        return "unknown"

    def _extract_name(self, response) -> str:
        selectors = [
            "h1.modelName::text",
            "h1.pcVideoListItem::text",
            "span.username::text",
            "h1[itemprop='name']::text",
            "h1::text",
            ".nameSection h1::text",
            "#profileInformation h1::text",
            ".modelProfile h1::text",
        ]
        for sel in selectors:
            val = response.css(sel).get("").strip()
            if val:
                return val

        # Fallback meta og:title
        og_title = response.css("meta[property='og:title']::attr(content)").get("").strip()
        if og_title:
            return og_title.split("|")[0].strip()

        return ""

    def _extract_username(self, response, url: str) -> str:
        # Extraire depuis l'URL comme fallback fiable
        match = re.search(r"pornhub\.com/(?:model|pornstar|channels|user)/([^/?#]+)", url)
        if match:
            return match.group(1)

        sel = response.css("span.username::text, .usernameWrap::text").get("").strip()
        return sel or ""

    def _extract_bio(self, response) -> str:
        selectors = [
            ".aboutMeSection p::text",
            ".aboutSection::text",
            "#profileAboutMe::text",
            ".bio::text",
            "[data-qa='about-me']::text",
            ".profileInfoBlock .description::text",
        ]
        for sel in selectors:
            val = " ".join(response.css(sel).getall()).strip()
            if val:
                return val
        return ""

    def _extract_avatar(self, response) -> str:
        selectors = [
            "img.avatar::attr(src)",
            "img[itemprop='image']::attr(src)",
            ".profileAvatar img::attr(src)",
            ".modelAvatar img::attr(src)",
            "#profileAvatar img::attr(src)",
            "meta[property='og:image']::attr(content)",
        ]
        for sel in selectors:
            val = response.css(sel).get("").strip()
            if val and val.startswith("http"):
                return val
        return ""

    def _extract_cover(self, response) -> str:
        selectors = [
            ".coverImage img::attr(src)",
            ".profileCover img::attr(src)",
            "#profileCover img::attr(src)",
            ".bannerImage::attr(src)",
        ]
        for sel in selectors:
            val = response.css(sel).get("").strip()
            if val and val.startswith("http"):
                return val
        return ""

    def _extract_stats(self, response) -> dict:
        stats = {}

        # Sélecteurs réels Pornhub : div.tooltipTrig.infoBox
        # Chaque boîte contient [nombre, label] ex: ["16.6M", "Video views"] ou ["78.9K", "Subscribers"]
        for box in response.css("div.tooltipTrig.infoBox"):
            parts = [t.strip() for t in box.css("::text").getall() if t.strip()]
            if len(parts) < 2:
                continue
            number = parts[0]
            label = " ".join(parts[1:]).lower()

            if any(k in label for k in ["view", "vue"]):
                stats["views"] = self._parse_number(number)
            elif any(k in label for k in ["subscri", "abonn", "follower", "fan"]):
                stats["subscribers"] = self._parse_number(number)
            elif any(k in label for k in ["video"]):
                stats["videos"] = self._parse_number(number)
            elif any(k in label for k in ["photo", "image", "pic"]):
                stats["photos"] = self._parse_number(number)
            elif any(k in label for k in ["rank", "rang"]):
                stats["rank"] = number

        # Fallback: JSON embarqué dans les scripts
        if not stats:
            stats.update(self._extract_from_scripts(response))

        return stats

    def _extract_from_scripts(self, response) -> dict:
        stats = {}
        for script in response.css("script:not([src])::text").getall():
            # Chercher des patterns JSON courants dans les scripts
            for pattern, key in [
                (r'"subscribersCount"\s*:\s*(\d+)', "subscribers"),
                (r'"videosCount"\s*:\s*(\d+)', "videos"),
                (r'"viewsCount"\s*:\s*(\d+)', "views"),
                (r'"rank"\s*:\s*(\d+)', "rank"),
                (r'"totalVideos"\s*:\s*(\d+)', "videos"),
                (r'"totalViews"\s*:\s*"?(\d[\d,\.KkMm]*)"?', "views"),
            ]:
                match = re.search(pattern, script)
                if match and key not in stats:
                    stats[key] = self._parse_number(match.group(1))

            # Essayer de parser le JSON global si présent
            if "modelInfo" in script or "channelInfo" in script:
                try:
                    data = json.loads(script.strip().lstrip("var _data =").rstrip(";"))
                    if isinstance(data, dict):
                        stats.update({
                            k: v for k, v in data.items()
                            if k in ("views", "subscribers", "videos", "photos", "rank")
                        })
                except Exception:
                    pass

        return stats

    def _extract_country(self, response) -> str:
        selectors = [
            ".infoBlock .country::text",
            "[data-country]::attr(data-country)",
            ".location::text",
            ".flag + span::text",
        ]
        for sel in selectors:
            val = response.css(sel).get("").strip()
            if val:
                return val
        return ""

    def _extract_gender(self, response) -> str:
        selectors = [
            ".gender::text",
            "[itemprop='gender']::attr(content)",
            ".infoBlock .gender::text",
        ]
        for sel in selectors:
            val = response.css(sel).get("").strip()
            if val:
                return val
        return ""

    def _extract_age(self, response) -> str:
        selectors = [
            ".age::text",
            "[itemprop='age']::text",
            ".infoBlock .age::text",
        ]
        for sel in selectors:
            val = response.css(sel).get("").strip()
            if val:
                return val

        # Fallback: chercher un motif "X years old" dans la page
        match = re.search(r"(\d{2})\s*years?\s*old", response.text, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_tags(self, response) -> list:
        tags = []
        selectors = [
            ".categoriesWrapper a::text",
            ".tagsWrapper a::text",
            ".modelTags a::text",
            ".profileTags a::text",
            "[data-tag]::attr(data-tag)",
        ]
        for sel in selectors:
            found = [t.strip() for t in response.css(sel).getall() if t.strip()]
            if found:
                tags.extend(found)
        return list(dict.fromkeys(tags))  # dédupliqué, ordre préservé

    @staticmethod
    def _parse_number(value: str):
        """Convertit '1.2M', '456K', '1,234' en int."""
        if not value:
            return None
        clean = str(value).strip().replace(",", "").replace(" ", "")
        try:
            if clean.lower().endswith("m"):
                return int(float(clean[:-1]) * 1_000_000)
            if clean.lower().endswith("k"):
                return int(float(clean[:-1]) * 1_000)
            return int(float(clean))
        except (ValueError, TypeError):
            return value  # retourner la valeur brute si non parseable
