"""File upload/download helpers (zip + base64)."""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path


_ZIP_EXCLUDE = ("output", "docgen")


def upload_dir(path: str | Path) -> str:
    """Zip a directory and return a base64-encoded string."""
    directory = Path(path).resolve()
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(directory.rglob("*")):
            if f.is_file():
                rel = f.relative_to(directory)
                if rel.parts[:2] == _ZIP_EXCLUDE:
                    continue
                zf.write(f, rel)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def upload_file(path: str | Path) -> str:
    """Zip a single file and return a base64-encoded string."""
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise ValueError(f"Not a file: {file_path}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(file_path, file_path.name)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def save_download(data: str, dest: str | Path) -> Path:
    """Decode a base64 zip and extract it to dest. Returns the dest path."""
    dest_path = Path(dest).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)

    zip_bytes = base64.b64decode(data)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(dest_path)

    return dest_path


def save_download_bytes(data: bytes, dest: str | Path) -> Path:
    """Extract a zip from raw bytes to dest. Returns the dest path."""
    dest_path = Path(dest).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest_path)
    return dest_path
