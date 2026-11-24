"""icon-1024.png → packaging/jarvis.icns + jarvis-256.png, and the phone's home-screen icons."""
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
src = Image.open(HERE / "icon-1024.png").convert("RGBA")
src.save(ROOT / "packaging/jarvis.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
src.resize((256, 256), Image.LANCZOS).save(ROOT / "packaging/jarvis-256.png")
square = src.crop((100, 100, 924, 924))          # iOS rounds the corners itself: full-bleed square
bg = Image.new("RGBA", square.size, (3, 6, 11, 255))
bg.alpha_composite(square)
static = ROOT / "jarvis/hud/static/vendor"
bg.convert("RGB").resize((180, 180), Image.LANCZOS).save(static / "apple-touch-icon.png")
bg.convert("RGB").resize((192, 192), Image.LANCZOS).save(static / "icon-192.png")
print("icons written")
