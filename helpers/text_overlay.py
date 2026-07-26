"""Render reusable, stylized text-overlay PNG assets with Pillow.

This helper deliberately does one job: turn a JSON manifest of text items into
full-frame transparent PNGs. 

Each output is a full-size RGBA canvas so it can be passed to ``render.py`` as
an overlay without relying on an implicit x/y placement contract.

Example manifest (paths are relative to this JSON file):

{
  "canvas": {"width": 1920, "height": 1080},
  "items": [
    {
      "id": "opening-stat",
      "text": "22人",
      "style": "stat",
      "x": 960,
      "y": 250,
      "anchor": "mm",
      "output": "overlays/opening-stat.png"
    }
  ]
}

"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FONTS_CONFIG = json.loads((_REPO_ROOT / "config" / "fonts.json").read_text(encoding="utf-8"))
_STYLE_PRESETS: dict[str, dict[str, Any]] = json.loads(
    (_REPO_ROOT / "config" / "styles.json").read_text(encoding="utf-8")
)


def _scaled(value: float | int, scale: int) -> int:
    return round(float(value) * scale)


def _resolve_path(value: str | Path, base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _load_font(font_value: str | None, size: int, base: Path) -> ImageFont.FreeTypeFont:
    if font_value:
        font_path = _resolve_path(font_value, base)
    else:
        font_path = _REPO_ROOT / _FONTS_CONFIG["cjk"]
    if not font_path.exists():
        raise FileNotFoundError(f"font not found: {font_path}")
    return ImageFont.truetype(str(font_path), size=size)


def _merge_style(item: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    style_name = item.get("style", manifest.get("style", "label"))
    if style_name not in _STYLE_PRESETS:
        available = ", ".join(sorted(_STYLE_PRESETS))
        raise ValueError(f"unknown style '{style_name}'; choose one of: {available}")
    style = dict(_STYLE_PRESETS[style_name])
    for source in (manifest.get("style_overrides", {}), item.get("style_overrides", {})):
        if not isinstance(source, dict):
            raise ValueError("style_overrides must be a JSON object")
        style.update(source)
    return style


def _draw_text(
    canvas: Image.Image,
    text: str,
    xy: tuple[int, int],
    anchor: str,
    font: ImageFont.FreeTypeFont,
    style: dict[str, Any],
    scale: int,
) -> None:
    """Draw soft shadow, optional glow, then crisp outlined text."""
    stroke = _scaled(style.get("stroke_width", 0), scale)
    base_draw = ImageDraw.Draw(canvas)
    background = style.get("background")
    if background:
        padding = style.get("background_padding", [0, 0])
        if not isinstance(padding, list) or len(padding) != 2:
            raise ValueError("background_padding must be a two-item array")
        left, top, right, bottom = base_draw.textbbox(
            xy, text, font=font, anchor=anchor, stroke_width=stroke
        )
        pad_x, pad_y = _scaled(padding[0], scale), _scaled(padding[1], scale)
        base_draw.rounded_rectangle(
            (left - pad_x, top - pad_y, right + pad_x, bottom + pad_y),
            radius=_scaled(style.get("background_radius", 0), scale),
            fill=background,
        )

    shadow_offset = style.get("shadow_offset", [0, 0])
    if not isinstance(shadow_offset, list) or len(shadow_offset) != 2:
        raise ValueError("shadow_offset must be a two-item array")
    shadow_xy = (
        xy[0] + _scaled(shadow_offset[0], scale),
        xy[1] + _scaled(shadow_offset[1], scale),
    )

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_color = style.get("shadow")
    if shadow_color:
        shadow_draw.text(
            shadow_xy,
            text,
            font=font,
            anchor=anchor,
            fill=shadow_color,
            stroke_width=stroke,
            stroke_fill=shadow_color,
        )
        blur = _scaled(style.get("shadow_blur", 0), scale)
        if blur:
            shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
        canvas.alpha_composite(shadow)

    glow_color = style.get("glow")
    if glow_color:
        glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.text(
            xy,
            text,
            font=font,
            anchor=anchor,
            fill=glow_color,
            stroke_width=stroke,
            stroke_fill=glow_color,
        )
        glow = glow.filter(ImageFilter.GaussianBlur(_scaled(style.get("glow_blur", 8), scale)))
        canvas.alpha_composite(glow)

    draw = ImageDraw.Draw(canvas)
    draw.text(
        xy,
        text,
        font=font,
        anchor=anchor,
        fill=style["fill"],
        stroke_width=stroke,
        stroke_fill=style["outline"],
    )


def render_item(item: dict[str, Any], manifest: dict[str, Any], base: Path) -> Path:
    canvas_data = manifest.get("canvas", {})
    width = int(canvas_data.get("width", 1920))
    height = int(canvas_data.get("height", 1080))
    if width <= 0 or height <= 0:
        raise ValueError("canvas width and height must be positive")

    text = str(item.get("text", "")).strip()
    if not text:
        raise ValueError("each item needs non-empty text")
    if "output" not in item:
        raise ValueError(f"text overlay '{text}' is missing an output path")

    scale = int(item.get("render_scale", manifest.get("render_scale", 2)))
    if scale < 1 or scale > 4:
        raise ValueError("render_scale must be between 1 and 4")
    style = _merge_style(item, manifest)
    font_size = _scaled(item.get("font_size", manifest.get("font_size", 112)), scale)
    font = _load_font(item.get("font", manifest.get("font")), font_size, base)

    x = _scaled(item.get("x", width / 2), scale)
    y = _scaled(item.get("y", height * 0.22), scale)
    anchor = str(item.get("anchor", manifest.get("anchor", "mm")))
    canvas = Image.new("RGBA", (_scaled(width, scale), _scaled(height, scale)), (0, 0, 0, 0))
    _draw_text(canvas, text, (x, y), anchor, font, style, scale)

    if scale > 1:
        canvas = canvas.resize((width, height), Image.Resampling.LANCZOS)
    output = _resolve_path(str(item["output"]), base)
    if output.suffix.lower() != ".png":
        raise ValueError(f"text-overlay output must be a .png file: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, "PNG")

    alpha = canvas.getchannel("A")
    if alpha.getbbox() is None:
        raise RuntimeError(f"rendered PNG has no visible pixels: {output}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Render stylized text overlays as transparent full-frame PNGs")
    parser.add_argument("--manifest", type=Path, required=True, help="JSON manifest of text-overlay items")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    if not manifest_path.exists():
        sys.exit(f"manifest not found: {manifest_path}")
    try:
        # ``utf-8-sig`` accepts both normal UTF-8 and the BOM PowerShell writes
        # by default, which is common for Windows-side edit manifests.
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        sys.exit(f"invalid JSON manifest: {exc}")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
        sys.exit("manifest must be an object with an 'items' array")

    try:
        outputs = [render_item(item, manifest, manifest_path.parent) for item in manifest["items"]]
    except (TypeError, ValueError, FileNotFoundError, RuntimeError) as exc:
        sys.exit(f"text-overlay render failed: {exc}")

    print(f"rendered {len(outputs)} text overlay PNG(s):")
    for output in outputs:
        print(f"  {output}")


if __name__ == "__main__":
    main()
