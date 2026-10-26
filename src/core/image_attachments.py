"""Multimodal image attachments sent to NemApi as OpenAI ``image_url`` parts.

NemApi (>= 5.1) accepts images on the LAST user message only, as base64
``data:`` URLs, 20 MB per image and 8 images per request (see
``nemapi/utils/tools_format.py::extract_images``). Remote URLs are refused.

Every NEMESIS surface sends only the current message, so this module builds a
``content`` array for that single message: one ``text`` part plus the image
parts. Text files keep the classic plain-text path; image files never have
their bytes dumped into the prompt.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

MAX_IMAGES = 8
MAX_IMAGE_DATA_URL = 20 * 1024 * 1024
MAX_IMAGE_BYTES = (MAX_IMAGE_DATA_URL // 4) * 3 - 4096

_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

_EXT_MIME: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _read_header(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(32)
    except OSError:
        return b""


def detect_image_mime(path: str) -> Optional[str]:
    """Return the MIME type of a raster image, or ``None`` for other files."""
    header = _read_header(path)
    for magic, mime in _MAGIC:
        if header.startswith(magic):
            return mime
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    suffix = Path(str(path)).suffix.lower()
    if suffix in _EXT_MIME and header:
        looks_like_text = True
        try:
            header.decode("utf-8")
        except UnicodeDecodeError:
            looks_like_text = False
        if looks_like_text and not any(byte < 32 and byte not in (9, 10, 13) for byte in header):
            return None
        return _EXT_MIME[suffix]
    return None


def is_image_file(path: str) -> bool:
    return detect_image_mime(path) is not None


def image_placeholder(path: str, mime: str, size: int) -> str:
    """Text shown to the model in place of the unreadable binary bytes."""
    human = f"{size / 1024:.1f} Ko" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} Mo"
    return (
        f"[IMAGE ATTACHED] {path} ({mime}, {human}). "
        "This image is delivered to you as a multimodal attachment in this same turn. "
        "Look at it directly and answer from what you see. "
        "Do NOT try to read it as text, do NOT ask for a base64 dump and do NOT call read_file on it again."
    )


def image_spec(path: str) -> Optional[Dict[str, Any]]:
    """Structured attachment produced by the file tools."""
    resolved = os.path.abspath(os.path.expanduser(str(path)))
    mime = detect_image_mime(resolved)
    if not mime or not os.path.isfile(resolved):
        return None
    try:
        size = os.path.getsize(resolved)
    except OSError:
        return None
    return {
        "path": resolved,
        "mime": mime,
        "name": os.path.basename(resolved)[:120],
        "size": size,
        "placeholder": image_placeholder(resolved, mime, size),
    }


def collect_result_images(result: Any) -> List[Any]:
    """Extract attachment specs from a tool result (dict or dataclass)."""
    if result is None:
        return []
    if isinstance(result, dict):
        raw = result.get("images") or result.get("image_paths") or []
    else:
        raw = getattr(result, "images", None) or getattr(result, "image_paths", None) or []
    if isinstance(raw, (str, dict)):
        return [raw]
    try:
        return [item for item in raw if item]
    except TypeError:
        return []


def _normalise(spec: Any) -> Optional[Dict[str, Any]]:
    if isinstance(spec, str):
        if spec.startswith("data:"):
            return {"url": spec}
        return image_spec(spec) or {"path": str(spec)}
    if not isinstance(spec, dict):
        return None
    normalised = dict(spec)
    holder = normalised.get("image_url")
    if isinstance(holder, dict) and isinstance(holder.get("url"), str):
        normalised["url"] = holder["url"]
        normalised.setdefault("name", normalised.get("filename") or "")
    url = normalised.get("url") or normalised.get("data_url")
    if isinstance(url, str) and url:
        normalised["url"] = url
    elif normalised.get("path"):
        path = os.path.abspath(os.path.expanduser(str(normalised["path"])))
        normalised["path"] = path
        normalised.setdefault("mime", detect_image_mime(path))
        normalised.setdefault("name", os.path.basename(path)[:120])
    else:
        return None
    return normalised


def build_image_parts(specs: Iterable[Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Return (OpenAI content parts, notes about skipped/invalid images)."""
    parts: List[Dict[str, Any]] = []
    notes: List[str] = []
    candidates = list(specs or [])
    if len(candidates) > MAX_IMAGES:
        notes.append(
            f"[SYSTEM: {len(candidates)} images fournies, seules les {MAX_IMAGES} premières sont transmises.]"
        )
        candidates = candidates[:MAX_IMAGES]

    for index, raw in enumerate(candidates, start=1):
        spec = _normalise(raw)
        if spec is None:
            notes.append(f"[SYSTEM: image #{index} ignorée — spécification invalide.]")
            continue
        url = spec.get("url")
        if isinstance(url, str) and url:
            if not url.startswith("data:"):
                notes.append(
                    f"[SYSTEM: image #{index} refusée — NemApi n'accepte que les URL data: base64, pas une URL distante.]"
                )
                continue
            if len(url) > MAX_IMAGE_DATA_URL:
                notes.append(
                    f"[SYSTEM: image #{index} refusée — dépasse 20 Mo une fois encodée.]"
                )
                continue
        else:
            path = spec.get("path") or ""
            if not os.path.isfile(path):
                notes.append(f"[SYSTEM: image #{index} introuvable: {path}]")
                continue
            try:
                size = os.path.getsize(path)
                if size > MAX_IMAGE_BYTES:
                    notes.append(
                        f"[SYSTEM: image #{index} ({os.path.basename(path)}) refusée — "
                        f"{size / (1024 * 1024):.1f} Mo, limite NemApi ~15 Mo par fichier.]"
                    )
                    continue
                with open(path, "rb") as handle:
                    payload = handle.read()
            except OSError as exc:
                notes.append(f"[SYSTEM: image #{index} illisible: {exc}]")
                continue
            mime = spec.get("mime") or detect_image_mime(path)
            if not mime:
                notes.append(f"[SYSTEM: image #{index} ignorée — format non reconnu: {path}]")
                continue
            url = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
            if len(url) > MAX_IMAGE_DATA_URL:
                notes.append(f"[SYSTEM: image #{index} refusée — dépasse 20 Mo une fois encodée.]")
                continue

        part: Dict[str, Any] = {
            "type": "image_url",
            "image_url": {"url": url},
        }
        name = spec.get("filename") or spec.get("name")
        if isinstance(name, str) and name.strip():
            part["filename"] = name.strip()[:120]
        parts.append(part)

    return parts, notes


def _legacy_send(provider: Any, message: str, role: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Providers predating ``system_prefix`` / ``cancel_event``."""
    prefix = str(kwargs.get("system_prefix") or "").strip()
    if prefix and role == "user":
        merged = f"[SYSTEM INSTRUCTIONS]\n{prefix}\n\n[USER MESSAGE]\n{message}"
        return provider.send_message(merged, role=role)
    return provider.send_message(message)


def send_message_with_images(
    provider: Any,
    message: str,
    role: str = "user",
    images: Optional[Iterable[Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Send a message, attaching images as multimodal parts when supported.

    Non-multimodal providers keep working: they receive the plain text plus a
    note explaining that an image could not be delivered.
    """
    parts, notes = build_image_parts(images or [])
    text = message
    if notes:
        text = f"{text}\n\n" + "\n".join(notes)

    attachable = bool(parts) and bool(getattr(provider, "supports_images", False))
    if attachable and role not in ("user", "tool_result"):
        attachable = False
        text += "\n[SYSTEM: images ignorées — seul un message utilisateur peut porter des pièces jointes.]"
    elif parts and not getattr(provider, "supports_images", False):
        text += (
            f"\n[SYSTEM: {len(parts)} image(s) disponible(s) mais ce provider ne gère pas le multimodal ; "
            "elles n'ont pas été transmises au modèle.]"
        )

    if attachable:
        try:
            return provider.send_message(text, role=role, images=parts, **kwargs)
        except TypeError:
            text += "\n[SYSTEM: le provider a refusé les pièces jointes; message texte seul.]"

    try:
        return provider.send_message(text, role=role, **kwargs)
    except TypeError:
        return _legacy_send(provider, text, role, kwargs)
