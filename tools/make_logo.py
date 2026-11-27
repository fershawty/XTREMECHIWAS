"""
tools/make_logo.py
==================
Genera el logo minimalista de FG VIEW en todos los formatos:

    assets/logo.svg      (interfaz, nítido a cualquier tamaño)
    assets/logo.png      (320 px, lanzador)
    assets/icon.png      (256 px, icono de ventana)
    assets/icon.ico      (Windows, 16–256 px)
    assets/icon.icns     (macOS, 128–1024 px, escrito a mano: no necesita iconutil)
    assets/fgview_logo.png (1024 px, vista previa)

Diseño: anillo dorado abierto (la «G» y el ojo que «ve»), una «F» geométrica dentro y un punto
de luz en la apertura (el «VIEW»). Fondo negro con borde sutil.

    python tools/make_logo.py
"""

from __future__ import annotations

import io
import math
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
GOLD = (212, 175, 55)
GOLD_LIGHT = (242, 210, 122)
BG_TOP, BG_BOTTOM = (16, 19, 27), (5, 7, 10)

# Geometría en un lienzo de 1000 × 1000
C, R, W = 500, 300, 44  # Centro, radio del anillo, grosor del trazo
GAP_START, GAP_END = -62, -22  # Apertura del anillo (grados, 0 = derecha, sentido horario en pantalla)
DOT_ANGLE = -42


def _pt(angle_deg: float, radius: float = R) -> tuple[float, float]:
    a = math.radians(angle_deg)
    return C + radius * math.cos(a), C + radius * math.sin(a)


def svg() -> str:
    x0, y0 = _pt(GAP_END)
    x1, y1 = _pt(GAP_START)
    dx, dy = _pt(DOT_ANGLE)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" role="img" aria-label="FG VIEW">
  <defs>
    <linearGradient id="fgbg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#10131B"/><stop offset="1" stop-color="#05070A"/>
    </linearGradient>
    <linearGradient id="fggold" gradientUnits="userSpaceOnUse" x1="200" y1="200" x2="800" y2="800">
      <stop offset="0" stop-color="#F7DC8B"/><stop offset=".55" stop-color="#D4AF37"/><stop offset="1" stop-color="#9C7A1E"/>
    </linearGradient>
    <radialGradient id="fgglow"><stop offset="0" stop-color="#FFF4C8"/><stop offset=".45" stop-color="#F2D27A" stop-opacity=".9"/>
      <stop offset="1" stop-color="#D4AF37" stop-opacity="0"/></radialGradient>
  </defs>
  <rect x="20" y="20" width="960" height="960" rx="220" fill="url(#fgbg)" stroke="#D4AF37" stroke-opacity=".22" stroke-width="6"/>
  <path d="M {x0:.1f} {y0:.1f} A {R} {R} 0 1 1 {x1:.1f} {y1:.1f}" fill="none" stroke="url(#fggold)"
        stroke-width="{W}" stroke-linecap="round"/>
  <path d="M 440 650 V 350 H 610 M 440 492 H 570" fill="none" stroke="url(#fggold)" stroke-width="{W}"
        stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="70" fill="url(#fgglow)"/>
  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="22" fill="#FFF4C8"/>
</svg>
'''


def raster(size: int) -> Image.Image:
    """Dibuja el logo con supermuestreo (4×) para bordes suaves."""
    s = 4 * size
    k = s / 1000
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    bg = Image.new("RGBA", (s, s))
    px = ImageDraw.Draw(bg)
    for y in range(s):  # Degradado vertical del fondo
        t = y / (s - 1)
        px.line([(0, y), (s, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)) + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([20 * k, 20 * k, 980 * k, 980 * k], radius=220 * k, fill=255)
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([20 * k, 20 * k, 980 * k, 980 * k], radius=220 * k, outline=GOLD + (60,), width=max(1, int(6 * k)))
    w = int(W * k)
    box = [(C - R) * k, (C - R) * k, (C + R) * k, (C + R) * k]
    d.arc(box, start=GAP_END, end=360 + GAP_START, fill=GOLD, width=w)  # Anillo «G»
    for ang in (GAP_END, GAP_START):  # Extremos redondeados del anillo
        x, y = _pt(ang)
        d.ellipse([(x * k - w / 2), (y * k - w / 2), (x * k + w / 2), (y * k + w / 2)], fill=GOLD)

    def bar(x0, y0, x1, y1):
        d.line([(x0 * k, y0 * k), (x1 * k, y1 * k)], fill=GOLD, width=w)
        for x, y in ((x0, y0), (x1, y1)):
            d.ellipse([x * k - w / 2, y * k - w / 2, x * k + w / 2, y * k + w / 2], fill=GOLD)

    bar(440, 350, 440, 650)  # «F»
    bar(440, 350, 610, 350)
    bar(440, 492, 570, 492)
    dx, dy = _pt(DOT_ANGLE)
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([(dx - 70) * k, (dy - 70) * k, (dx + 70) * k, (dy + 70) * k], fill=GOLD_LIGHT + (170,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(28 * k)))
    d = ImageDraw.Draw(img)
    d.ellipse([(dx - 22) * k, (dy - 22) * k, (dx + 22) * k, (dy + 22) * k], fill=(255, 244, 200))
    return img.resize((size, size), Image.LANCZOS)


def write_icns(path: Path) -> None:
    """ICNS con PNG embebidos (ic07 128, ic08 256, ic09 512, ic10 1024)."""
    chunks = b""
    for code, size in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)):
        buf = io.BytesIO()
        raster(size).save(buf, "PNG")
        data = buf.getvalue()
        chunks += code + struct.pack(">I", len(data) + 8) + data
    path.write_bytes(b"icns" + struct.pack(">I", len(chunks) + 8) + chunks)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "logo.svg").write_text(svg(), encoding="utf-8")
    big = raster(1024)
    big.save(ASSETS / "fgview_logo.png")
    raster(320).save(ASSETS / "logo.png")
    raster(256).save(ASSETS / "icon.png")
    big.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    write_icns(ASSETS / "icon.icns")
    print("Logo generado en", ASSETS)


if __name__ == "__main__":
    main()
