"""
OPS CONTROL - Welcome Image Service

Generates custom welcome images using Pillow.
Renders member name (with custom character spacing), date, and time
onto the welcome template at specified coordinates and font sizes.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bot.config import config

logger = logging.getLogger("ops_control.services.welcome_image")

# Paths: use ASSETS_DIR env var in Docker (defaults to project-relative for local dev).
# In Docker: ASSETS_DIR=/app/assets
_ASSETS = Path(os.getenv("ASSETS_DIR", str(Path(__file__).resolve().parents[3] / "assets")))

TEMPLATE_PATH = _ASSETS / "welcome.png"
FONT_REGULAR = _ASSETS / "fonts" / "Sanchez-Regular.ttf"
OUTPUT_DIR = _ASSETS / "generated"

# Rendering coordinates
NAME_POSITION = (1375, 150)
DATE_POSITION = (1375, 425)
TIME_POSITION = (1625, 425)

# Font sizes
NAME_FONT_SIZE = 42
DATE_TIME_FONT_SIZE = 30

# Styling
TEXT_COLOR = "#f4f6fc"
BOLD_STROKE_WIDTH = 1.5
CHAR_SPACING = 3  # extra pixels between characters for the display name


class WelcomeImageGenerator:
    """
    Generates a welcome image for a new Discord member.

    Usage:
        gen = WelcomeImageGenerator()
        path = gen.generate(name="John Doe")
        # send the image to Discord, then:
        gen.cleanup(path)
    """

    def __init__(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        if not TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"Welcome template not found: {TEMPLATE_PATH}")
        if not FONT_REGULAR.exists():
            raise FileNotFoundError(f"Font not found: {FONT_REGULAR}")

        logger.info("Assets directory: %s", _ASSETS)
        logger.info("Generated images directory: %s", OUTPUT_DIR)

    def generate(self, name: str) -> Path:
        """
        Generate a welcome image for the given member name.

        Returns the path to the generated PNG.
        """
        now = datetime.now(timezone.utc)

        # Open template
        img = Image.open(TEMPLATE_PATH).convert("RGBA")
        draw = ImageDraw.Draw(img)

        # Fonts
        try:
            name_font = ImageFont.truetype(str(FONT_REGULAR), size=NAME_FONT_SIZE)
            dt_font = ImageFont.truetype(str(FONT_REGULAR), size=DATE_TIME_FONT_SIZE)
        except OSError:
            logger.warning("Could not load custom font; falling back to default.")
            name_font = ImageFont.load_default()
            dt_font = name_font

        # Shared text kwargs for bold simulation
        bold_kwargs = {
            "fill": TEXT_COLOR,
            "stroke_width": BOLD_STROKE_WIDTH,
            "stroke_fill": TEXT_COLOR,
        }

        # --- Render NAME with custom character spacing ---
        # Draw each character individually with horizontal offsets to
        # simulate letter-spacing.
        x, y = NAME_POSITION
        for ch in name:
            char_bbox = name_font.getbbox(ch)
            char_width = char_bbox[2] - char_bbox[0] if char_bbox else 0
            draw.text((x, y), ch, font=name_font, anchor="la", **bold_kwargs)
            x += char_width + CHAR_SPACING

        # --- Render DATE — format: DD MMM YYYY (e.g. 31 JUL 2026) ---
        date_str = now.strftime("%d %b %Y").upper()
        draw.text(DATE_POSITION, date_str, font=dt_font, anchor="la", **bold_kwargs)

        # --- Render TIME — format: HH:MMZ (e.g. 16:25Z) ---
        time_str = now.strftime("%H:%M") + "Z"
        draw.text(TIME_POSITION, time_str, font=dt_font, anchor="la", **bold_kwargs)

        # Save to generated/ directory
        safe_name = "".join(c for c in name if c.isalnum() or c in " _-").rstrip()
        output_path = OUTPUT_DIR / f"welcome_{safe_name}_{now.strftime('%Y%m%d_%H%M%S')}.png"
        img.save(output_path, "PNG")
        logger.info("Generated welcome image: %s", output_path.name)

        return output_path

    @staticmethod
    def cleanup(filepath: Path) -> None:
        """Remove the generated image after it has been sent."""
        try:
            filepath.unlink(missing_ok=True)
            logger.debug("Cleaned up %s", filepath.name)
        except OSError:
            logger.warning("Failed to clean up %s", filepath)
