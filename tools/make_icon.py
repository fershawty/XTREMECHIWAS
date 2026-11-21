"""
tools/make_icon.py
==================
Genera los iconos de FG VIEW a partir del logo (assets/fgview_logo.jpg):
    assets/icon.png   -> ventana de arranque y app (256 px)
    assets/logo.png   -> portada circular con borde dorado dentro de la app (320 px)
    assets/icon.ico   -> icono del .exe de Windows
    assets/icon.icns  -> icono de la app de macOS

    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SOURCE = ASSETS / "fgview_logo.jpg"
GOLD = (212, 175, 55, 255)


def rounded(img: Image.Image, radius_ratio: float = 0.18) -> Image.Image:
    """Esquinas redondeadas (estilo icono de app)."""
    size = img.size[0]
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def circular(img: Image.Image, size: int = 320, border: int = 8) -> Image.Image:
    """Portada circular con borde dorado."""
    face = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([border, border, size - border - 1, size - border - 1], fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(face, (0, 0), mask)
    ImageDraw.Draw(out).ellipse([border // 2, border // 2, size - border // 2 - 1, size - border // 2 - 1],
                                outline=GOLD, width=border)
    return out


def main() -> None:
    src = Image.open(SOURCE).convert("RGB")
    icon = rounded(src.resize((512, 512), Image.LANCZOS))
    icon.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon.png")
    circular(src).save(ASSETS / "logo.png")
    icon.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(ASSETS / "icon.icns", format="ICNS")
    print(f"Iconos generados en {ASSETS}")


if __name__ == "__main__":
    main()
