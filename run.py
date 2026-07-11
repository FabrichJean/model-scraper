#!/usr/bin/env python
"""
Launcher simple pour scraper le profil d'un modèle Pornhub.

Usage:
    python run.py <url_du_modele>
    python run.py https://www.pornhub.com/model/xxx
    python run.py https://www.pornhub.com/pornstar/yyy

Options:
    --refresh   Rafraîchit les credentials Pornhub via Playwright avant le scrape
"""

import sys
import subprocess
from pathlib import Path
from credentials import refresh as refresh_credentials

PROJECT_DIR = Path(__file__).parent


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    if not args:
        print(__doc__)
        sys.exit(1)

    model_url = args[0]

    if "--refresh" in flags:
        if not refresh_credentials():
            print("[AUTH] Avertissement: credentials non rafraîchis, utilisation des fichiers existants")

    print(f"\nScraping: {model_url}\n")

    result = subprocess.run(
        [sys.executable, "-m", "scrapy", "crawl", "model_profile", "-a", f"url={model_url}"],
        cwd=PROJECT_DIR,
    )

    if result.returncode == 0:
        output_dir = PROJECT_DIR / "output"
        files = sorted(output_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if files:
            print(f"\nRésultat sauvegardé: {files[0]}")
    else:
        print(f"\nErreur (code {result.returncode})")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
