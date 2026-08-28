"""Generate assets/hemsa.ico (multi-size) from the same drawing as the tray icon.
Run after a palette change: .venv\\Scripts\\python.exe scripts\\make_icon.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from hemsa import palette as P  # noqa: E402


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = max(1, size // 32)
    d.ellipse([m, m, size - m, size - m], fill=P.DEEP)
    try:
        font = ImageFont.truetype("segoeui.ttf", int(size * 0.6))
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size * 0.45), "h", font=font, fill=P.PAPER, anchor="mm")
    return img


def main() -> None:
    out = ROOT / "assets" / "hemsa.ico"
    out.parent.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    draw(256).save(out, sizes=[(s, s) for s in sizes])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
