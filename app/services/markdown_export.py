"""최종 기획서를 Markdown 파일로 저장 (9일 차 UI '저장' 버튼용)."""
from __future__ import annotations

import re
from pathlib import Path

OUTPUT_DIR = Path("outputs")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", name).strip("-")
    return slug or "plan"


def save_markdown(project_name: str, content: str) -> str:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{_slugify(project_name)}.md"
    path.write_text(content, encoding="utf-8")
    return str(path)
