import json
from datetime import datetime
from pathlib import Path

from scrapy.utils.project import get_project_settings

PROJECT_DIR = Path(__file__).parent.parent


class JsonOutputPipeline:
    def __init__(self):
        settings = get_project_settings()
        raw = settings.get("OUTPUT_DIR", "./output")
        # Résoudre en absolu par rapport à la racine du projet
        p = Path(raw)
        self.output_dir = p if p.is_absolute() else PROJECT_DIR / p
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_item(self, item, spider):
        item_dict = dict(item)

        username = item_dict.get("username") or item_dict.get("name") or "unknown"
        safe_name = "".join(c for c in username if c.isalnum() or c in "-_").lower()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.output_dir / f"{safe_name}_{timestamp}.json"

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(item_dict, f, ensure_ascii=False, indent=2, default=str)

        spider.logger.info(f"Profil sauvegardé: {filename}")
        return item
