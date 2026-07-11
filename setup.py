#!/usr/bin/env python3
"""
Prépare l'environnement complet du projet : venv, dépendances Python,
navigateur Playwright, vérification de ffmpeg, dossiers nécessaires.

Usage:
    python3 setup.py            # crée .venv/ et installe tout dedans
    python3 setup.py --no-venv  # installe dans l'interpréteur courant
"""

import shutil
import subprocess
import sys
import venv
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
VENV_DIR = PROJECT_DIR / ".venv"
REQUIRED_DIRS = ["credentials", "downloads", "output"]


def log(msg: str) -> None:
    print(f"[setup] {msg}")


def venv_python(venv_dir: Path) -> Path:
    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    exe = "python.exe" if sys.platform == "win32" else "python3"
    return venv_dir / bin_dir / exe


def check_python_version() -> None:
    if sys.version_info < (3, 10):
        sys.exit(
            f"[setup] Python 3.10+ requis (trouvé {sys.version_info.major}.{sys.version_info.minor}). "
            "Le code utilise la syntaxe 'str | None'."
        )
    log(f"Python {sys.version_info.major}.{sys.version_info.minor} OK")


def create_venv() -> Path:
    if VENV_DIR.exists():
        log(f"venv déjà présent ({VENV_DIR}), réutilisation")
    else:
        log(f"Création du venv ({VENV_DIR})…")
        venv.create(VENV_DIR, with_pip=True)
        log("venv créé")
    return venv_python(VENV_DIR)


def install_requirements(python_bin: Path) -> None:
    log("Installation des dépendances Python (requirements.txt)…")
    subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--upgrade", "pip"],
        check=True, cwd=PROJECT_DIR,
    )
    subprocess.run(
        [str(python_bin), "-m", "pip", "install", "-r", "requirements.txt"],
        check=True, cwd=PROJECT_DIR,
    )
    log("Dépendances Python installées")


def install_playwright_browser(python_bin: Path) -> None:
    log("Installation du navigateur Playwright (chromium)…")
    subprocess.run(
        [str(python_bin), "-m", "playwright", "install", "chromium"],
        check=True, cwd=PROJECT_DIR,
    )
    log("Navigateur Playwright installé")


def check_ffmpeg() -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        log(f"ffmpeg OK ({ffmpeg})")
        return
    log("⚠ ffmpeg/ffprobe introuvable dans le PATH — requis pour le trim vidéo.")
    if sys.platform == "darwin":
        log("  Installer avec: brew install ffmpeg")
    else:
        log("  Installer via le gestionnaire de paquets de votre système.")


def create_directories() -> None:
    for name in REQUIRED_DIRS:
        d = PROJECT_DIR / name
        d.mkdir(exist_ok=True)
        log(f"Dossier prêt: {name}/")


def print_next_steps(python_bin: Path, used_venv: bool) -> None:
    print()
    log("Setup terminé ✓")
    print()
    if used_venv:
        activate = (
            f"{VENV_DIR}\\Scripts\\activate" if sys.platform == "win32"
            else f"source {VENV_DIR}/bin/activate"
        )
        print(f"  1. Activer le venv       : {activate}")
        print(f"  2. Rafraîchir les cookies: python3 credentials.py")
        print(f"  3. Lancer le serveur web : python3 server.py")
    else:
        print(f"  1. Rafraîchir les cookies: python3 credentials.py")
        print(f"  2. Lancer le serveur web : python3 server.py")


def main() -> None:
    no_venv = "--no-venv" in sys.argv

    check_python_version()

    if no_venv:
        python_bin = Path(sys.executable)
        log("Installation dans l'interpréteur courant (--no-venv)")
    else:
        python_bin = create_venv()

    install_requirements(python_bin)
    install_playwright_browser(python_bin)
    check_ffmpeg()
    create_directories()
    print_next_steps(python_bin, used_venv=not no_venv)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(f"[setup] Échec: {exc}")
