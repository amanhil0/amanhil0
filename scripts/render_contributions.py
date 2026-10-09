#!/usr/bin/env python3
"""Render GitHub's actual public contribution calendar as a haunted pixel GIF.

Requires Pillow. Fetch errors, missing counts, or a changed calendar structure
abort before touching the output. The ghost is decoration; tile colors always
use the levels reported by GitHub. No credentials or third-party APIs are used.
"""

import argparse
import datetime as dt
from html.parser import HTMLParser
import math
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw


BG = (8, 12, 18)
PANEL = (17, 25, 35)
MUTED = (100, 127, 133)
BONE = (217, 217, 203)
TEAL = (109, 147, 146)
GOLD = (185, 145, 97)
LEVELS = [PANEL, (39, 62, 64), (59, 90, 91), (84, 122, 122), TEAL]

# Small original bitmap alphabet keeps the rendering identical on every host.
GLYPHS = {
    "A":"01110/10001/10001/11111/10001/10001/10001", "B":"11110/10001/10001/11110/10001/10001/11110",
    "C":"01111/10000/10000/10000/10000/10000/01111", "D":"11110/10001/10001/10001/10001/10001/11110",
    "E":"11111/10000/10000/11110/10000/10000/11111", "F":"11111/10000/10000/11110/10000/10000/10000",
    "G":"01111/10000/10000/10111/10001/10001/01111", "H":"10001/10001/10001/11111/10001/10001/10001",
    "I":"11111/00100/00100/00100/00100/00100/11111", "J":"00111/00010/00010/00010/10010/10010/01100",
    "K":"10001/10010/10100/11000/10100/10010/10001", "L":"10000/10000/10000/10000/10000/10000/11111",
    "M":"10001/11011/10101/10101/10001/10001/10001", "N":"10001/11001/10101/10011/10001/10001/10001",
    "O":"01110/10001/10001/10001/10001/10001/01110", "P":"11110/10001/10001/11110/10000/10000/10000",
    "Q":"01110/10001/10001/10001/10101/10010/01101", "R":"11110/10001/10001/11110/10100/10010/10001",
    "S":"01111/10000/10000/01110/00001/00001/11110", "T":"11111/00100/00100/00100/00100/00100/00100",
    "U":"10001/10001/10001/10001/10001/10001/01110", "V":"10001/10001/10001/10001/10001/01010/00100",
    "W":"10001/10001/10001/10101/10101/10101/01010", "X":"10001/10001/01010/00100/01010/10001/10001",
    "Y":"10001/10001/01010/00100/00100/00100/00100", "Z":"11111/00001/00010/00100/01000/10000/11111",
    "0":"01110/10001/10011/10101/11001/10001/01110", "1":"00100/01100/00100/00100/00100/00100/01110",
    "2":"01110/10001/00001/00010/00100/01000/11111", "3":"11110/00001/00001/01110/00001/00001/11110",
    "4":"00010/00110/01010/10010/11111/00010/00010", "5":"11111/10000/10000/11110/00001/00001/11110",
    "6":"01110/10000/10000/11110/10001/10001/01110", "7":"11111/00001/00010/00100/01000/01000/01000",
    "8":"01110/10001/10001/01110/10001/10001/01110", "9":"01110/10001/10001/01111/00001/00001/01110",
    "-":"00000/00000/00000/11111/00000/00000/00000", "/":"00001/00001/00010/00100/01000/10000/10000",
    ".":"00000/00000/00000/00000/00000/00110/00110", ":":"00000/00110/00110/00000/00110/00110/00000",
    "+":"00000/00100/00100/11111/00100/00100/00000", "|":"00100/00100/00100/00100/00100/00100/00100",
    ",":"00000/00000/00000/00000/00110/00110/00100", "=":"00000/00000/11111/00000/11111/00000/00000",
}


def label(draw, xy, value, color=MUTED, scale=1):
    x, y = xy
    for char in value.upper():
        for row, pattern in enumerate(GLYPHS.get(char, "").split("/")):
            for col, pixel in enumerate(pattern):
                if pixel == "1":
                    px, py = x + col * scale, y + row * scale
                    draw.rectangle((px, py, px + scale - 1, py + scale - 1), fill=color)
        x += 6 * scale


class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = {}
        self.tooltips = {}
        self.tip_id = None
        self.tip_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-date" in attrs and "data-level" in attrs:
            cell_id = attrs["id"]
            if cell_id in self.cells:
                raise ValueError("Duplicate GitHub calendar cell")
            self.cells[cell_id] = (dt.date.fromisoformat(attrs["data-date"]), int(attrs["data-level"]))
        if tag == "tool-tip":
            self.tip_id = attrs.get("for")
            self.tip_text = []

    def handle_data(self, data):
        if self.tip_id:
            self.tip_text.append(data)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.tip_id:
            self.tooltips[self.tip_id] = "".join(self.tip_text).strip()
            self.tip_id = None

    def days(self):
        parsed = []
        for cell_id, (day, level) in self.cells.items():
            match = re.match(r"(No|[\d,]+) contributions? on ", self.tooltips.get(cell_id, ""))
            if not match or level not in range(5):
                raise ValueError(f"Missing or invalid contribution data for {day}")
            count = 0 if match[1] == "No" else int(match[1].replace(",", ""))
            if (count == 0) != (level == 0):
                raise ValueError(f"Inconsistent contribution count and level for {day}")
            parsed.append((day, level, count))
        parsed.sort()
        if not 350 <= len(parsed) <= 378:
            raise ValueError(f"Expected roughly one year of calendar data, got {len(parsed)} days")
        expected = [parsed[0][0] + dt.timedelta(days=i) for i in range(len(parsed))]
        if [day for day, _, _ in parsed] != expected:
            raise ValueError("Calendar has duplicate dates or missing dates")
        return parsed


def fetch_calendar(username):
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,38})", username):
        raise ValueError("Invalid GitHub username")
    source = f"https://github.com/users/{username}/contributions"
    request = Request(source, headers={"User-Agent": "contribution-graveyard/1.0", "Accept-Language": "en-US"})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=45) as response:
                return response.read().decode("utf-8")
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def ghost(draw, x, y, phase):
    # A sprite, never part of the contribution data or legend.
    tail = 1 if math.sin(phase * 2) > 0 else 0
    draw.ellipse((x - 5, y + 9, x + 7, y + 11), fill=(26, 37, 44))
    draw.rectangle((x - 2, y - 5, x + 3, y - 5), fill=TEAL)
    draw.rectangle((x - 4, y - 4, x + 5, y + 5), fill=TEAL)
    draw.rectangle((x - 5, y - 2, x + 6, y + 5), fill=TEAL)
    draw.rectangle((x - 3, y - 3, x + 4, y + 5), fill=BONE)
    draw.rectangle((x - 4, y - 1, x + 5, y + 3), fill=BONE)
    for dx in (-4, 0, 4):
        draw.rectangle((x + dx, y + 4, x + dx + 1, y + 6 + tail), fill=BONE)
    draw.rectangle((x - 2, y - 1, x - 1, y + 1), fill=BG)
    draw.rectangle((x + 2, y - 1, x + 3, y + 1), fill=BG)
    draw.point((x, y + 3), fill=MUTED)


def render(days, output, username="amanhil0"):
    width, height = 600, 156
    x0, y0, step, tile = 46, 49, 10, 8
    start, end = days[0][0], days[-1][0]
    sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    total = sum(count for _, _, count in days)
    frames = []
    # 96 frames at 80 ms: a calm seamless loop, without flashing.
    for frame in range(96):
        phase = frame / 96 * math.tau
        canvas = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(canvas)
        draw.line((14, 1, 585, 1), fill=PANEL)
        label(draw, (16, 12), "THE COMMIT CEMETERY", BONE, 2)
        counter = f"{total:,} CONTRIBUTIONS"
        label(draw, (584 - len(counter) * 6, 16), counter, TEAL)
        label(draw, (16, 61), "MON")
        label(draw, (16, 81), "WED")
        label(draw, (16, 101), "FRI")
        previous_month = None
        last_label_x = -100
        for day, level, count in days:
            col = (day - sunday).days // 7
            row = (day.weekday() + 1) % 7
            x, y = x0 + col * step, y0 + row * step
            # Keep the true level color unchanged throughout the animation.
            draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill=LEVELS[level])
            if level:
                # Breathing highlights within occupied tiles do not change level.
                glow = (math.sin(phase + col * 0.19) + 1) / 2
                top = tuple(round(v + (255 - v) * 0.10 * glow) for v in LEVELS[level])
                draw.line((x + 1, y, x + tile - 2, y), fill=top)
            if day.month != previous_month:
                if x - last_label_x >= 24:
                    label(draw, (x, 36), day.strftime("%b"))
                    last_label_x = x
                previous_month = day.month
        # Wisps glide in the empty path below the actual calendar.
        for i in range(4):
            wx = 48 + round((i * 127 + 20 * math.sin(phase + i)) % 526)
            wy = 122 + (i % 2) * 2
            draw.line((wx, wy, wx + 17, wy), fill=(23, 35, 42))
            draw.line((wx + 8, wy + 2, wx + 29, wy + 2), fill=(19, 30, 38))
        # The ghost follows a smooth loop, always clear of calendar cells.
        ghost(draw, 310 + round(238 * math.cos(phase)), 124 + round(2 * math.sin(phase * 2)), phase)
        # Miniature grave and candle underline the theme.
        draw.rectangle((18, 119, 28, 133), fill=(47, 62, 67))
        draw.rectangle((20, 117, 26, 120), fill=(47, 62, 67))
        draw.line((23, 120, 23, 127), fill=MUTED)
        draw.line((21, 122, 25, 122), fill=MUTED)
        draw.rectangle((36, 129, 38, 133), fill=BONE)
        draw.point((37, 125 + (frame // 12) % 2), fill=GOLD)
        draw.line((37, 127, 37, 128), fill=GOLD)
        label(draw, (46, 140), f"{start:%Y-%m-%d} / {end:%Y-%m-%d}")
        # Native GitHub semantics: less to more, level 0 through 4.
        label(draw, (381, 140), "LESS")
        for level, color in enumerate(LEVELS):
            x = 415 + level * 10
            draw.rectangle((x, 139, x + 7, 146), fill=color)
        label(draw, (470, 140), "MORE")
        draw.line((14, 154, 585, 154), fill=PANEL)
        frames.append(canvas.resize((1200, 312), Image.Resampling.NEAREST))
    # A shared palette prevents flicker between identical colors in each frame.
    palette_source = Image.new("RGB", (1200, 312 * 4))
    for i in range(4):
        palette_source.paste(frames[i * 24], (0, i * 312))
    palette = palette_source.quantize(colors=128, dither=Image.Dither.NONE)
    frames = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp.gif")
    frames[0].save(temp, save_all=True, append_images=frames[1:], duration=80,
                   loop=0, disposal=2, optimize=True,
                   comment=(f"Source: https://github.com/users/{username}/contributions; "
                            f"{start} through {end}; {total} contributions. "
                            "Ghost is decorative; tile levels are GitHub data.").encode("ascii"))
    with Image.open(temp) as check:
        if check.n_frames != 96 or check.size != (1200, 312):
            raise ValueError("Animated output verification failed")
    temp.replace(output)
    print(f"Verified {len(days)} dates, {start} through {end}; {total} contributions; "
          f"{sum(count > 0 for _, _, count in days)} active days. Saved {output}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="amanhil0")
    parser.add_argument("--output", type=Path, default=Path("assets/contribution-graveyard.gif"))
    parser.add_argument("--html", type=Path, help="Use a saved GitHub calendar response for a reproducible preview")
    args = parser.parse_args()
    source = args.html.read_text(encoding="utf-8") if args.html else fetch_calendar(args.username)
    calendar = CalendarParser()
    calendar.feed(source)
    render(calendar.days(), args.output, args.username)


if __name__ == "__main__":
    main()
