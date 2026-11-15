"""Compose control_sheet.js crops into one PNG per screen: each control at 3x with its name."""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

out = Path(sys.argv[1])
font = ImageFont.load_default(size=18)
for idx in sorted(out.glob("*.json")):
    items = json.loads(idx.read_text())
    cells = []
    for e in items:
        im = Image.open(out / f"{idx.stem}-{e['i']}.png").convert("RGB")
        im.thumbnail((440, 200))
        cell = Image.new("RGB", (460, 260), (24, 28, 36))
        cell.paste(im, ((460 - im.width) // 2, 10))
        d = ImageDraw.Draw(cell)
        d.text((10, 216), e["name"][:40], fill=(230, 235, 245), font=font)
        d.text((10, 238), f"{e['tag']}#{e['id']} {e['w']}x{e['h']}{' icon-only' if e['iconOnly'] else ''}"[:46],
               fill=(150, 160, 175), font=ImageFont.load_default(size=14))
        cells.append(cell)
    cols = 4
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 470 + 10, rows * 270 + 10), (12, 14, 18))
    for k, c in enumerate(cells):
        sheet.paste(c, (10 + (k % cols) * 470, 10 + (k // cols) * 270))
    sheet.save(out / f"sheet-{idx.stem}.png")
    print(idx.stem, len(cells))
