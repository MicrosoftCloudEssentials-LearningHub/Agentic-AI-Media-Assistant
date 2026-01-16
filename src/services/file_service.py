import os
import re
import uuid
from pathlib import Path
from typing import Optional, Tuple

import aiofiles
from fastapi import UploadFile


def _safe_filename(name: str) -> str:
    name = (name or "").strip() or "file"
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:160] or "file"


class FileService:
    def __init__(self, uploads_dir: str = "app/static/uploads"):
        self.uploads_dir = Path(uploads_dir)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    async def save_upload(self, file: UploadFile, max_bytes: int) -> Tuple[str, Path, int]:
        original_name = _safe_filename(file.filename or "file")
        file_id = uuid.uuid4().hex
        stored_name = f"{file_id}_{original_name}"
        dest_path = self.uploads_dir / stored_name

        total = 0
        async with aiofiles.open(dest_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    try:
                        dest_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise ValueError(f"File too large. Max is {max_bytes} bytes.")
                await out.write(chunk)

        return stored_name, dest_path, total

    @staticmethod
    def is_image(content_type: Optional[str], filename: Optional[str]) -> bool:
        ct = (content_type or "").lower()
        if ct.startswith("image/"):
            return True
        ext = (Path(filename or "").suffix or "").lower()
        return ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
