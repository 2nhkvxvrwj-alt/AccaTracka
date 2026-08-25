from __future__ import annotations

import base64
import hashlib
import io

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageOps


ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "application/pdf"}


def source_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def combined_source_hash(sources: list[tuple[str, str, bytes]]) -> str:
    digest = hashlib.sha256()
    for filename, mime_type, data in sources:
        digest.update(len(filename).to_bytes(4, "big"))
        digest.update(filename.encode("utf-8"))
        digest.update(mime_type.encode("ascii"))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def receipt_to_images(data: bytes, mime_type: str, max_pages: int = 5) -> list[Image.Image]:
    if mime_type not in ALLOWED_TYPES:
        raise ValueError("Upload a PNG, JPEG, WebP, or PDF receipt")
    if mime_type == "application/pdf":
        document = pdfium.PdfDocument(data)
        if len(document) == 0:
            raise ValueError("The PDF has no pages")
        return [
            document[index].render(scale=2).to_pil().convert("RGB")
            for index in range(min(len(document), max_pages))
        ]
    return [Image.open(io.BytesIO(data)).convert("RGB")]


def prepare_image(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    if max(image.size) > 2400:
        image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    return ImageEnhance.Contrast(image).enhance(1.08)


def image_data_url(image: Image.Image) -> str:
    output = io.BytesIO()
    prepare_image(image).save(output, format="JPEG", quality=90, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
