"""
tools/make_icon.py
==================
Genera el icono de FUTURES-SCOPE (assets/icon.png y assets/icon.ico) con Pillow.
Diseño minimalista: tres velas sobre fondo oscuro, legible incluso a 16 px.

    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

BG = (14, 17, 23, 255)
BORDER = (31, 38, 51, 255)
GREEN = (0, 255, 136, 255)
RED = (255, 59, 48, 255)


def draw_icon(size: int = 512) -> Image.Image:
    """Dibuja el icono a `size` px (se reduce después para cada tamaño)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=BG, outline=BORDER, width=max(2, size // 64))

    # Tres velas: (centro x, cuerpo arriba, cuerpo abajo, mecha arriba, mecha abajo, color)
    s = size / 512
    candles = [
        (150, 300, 380, 260, 410, GREEN),
        (256, 250, 330, 210, 360, RED),
        (362, 130, 290, 95, 330, GREEN),
    ]
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for cx, top, bottom, wt, wb, color in candles:
        w = 62 * s
        g.rounded_rectangle([cx * s - w / 2, top * s, cx * s + w / 2, bottom * s], radius=10 * s, fill=color[:3] + (110,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18 * s)))
    d = ImageDraw.Draw(img)
    for cx, top, bottom, wt, wb, color in candles:
        w = 62 * s
        d.line([cx * s, wt * s, cx * s, wb * s], fill=color, width=max(2, int(12 * s)))
        d.rounded_rectangle([cx * s - w / 2, top * s, cx * s + w / 2, bottom * s], radius=10 * s, fill=color)
    return img


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    big = draw_icon(512)
    big.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon.png")
    big.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Iconos generados en {ASSETS}")


if __name__ == "__main__":
    main()
