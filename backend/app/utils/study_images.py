"""Bounded, verified raster inputs shared by studies and the plan adapter."""

import base64
import binascii
from io import BytesIO
import re

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def inspect_image(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Choose a PNG, JPEG or WebP screenshot smaller than 5 MB.")
    try:
        with Image.open(BytesIO(data)) as image:
            mime = MIME_TYPES.get(image.format)
            width, height = image.size
            if not mime or width * height > MAX_IMAGE_PIXELS or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Use a still PNG, JPEG or WebP screenshot up to 16 million pixels.")
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("This screenshot could not be read. Upload a valid image.") from error
    return {"mime": mime, "width": width, "height": height}


def image_data_url(data):
    info = inspect_image(data)
    return f"data:{info['mime']};base64," + base64.b64encode(data).decode("ascii")


def validate_image_url(value):
    if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 128:
        raise ValueError("Screenshot image data is missing or too large.")
    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)", value)
    if not match:
        raise ValueError("Images must be inline PNG, JPEG or WebP data; remote image URLs are not accepted.")
    try:
        data = base64.b64decode(match[2], validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Screenshot image data is not valid base64.") from error
    if inspect_image(data)["mime"] != match[1]:
        raise ValueError("Screenshot content does not match its image type.")
    return value
