"""Красивые обложки для уроков и разборов (палитра: шалфей и крем)."""
import io
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
F_BOLD = os.path.join(BASE, "fonts", "Inter-Bold.otf")
F_SEMI = os.path.join(BASE, "fonts", "Inter-SemiBold.otf")
F_MED = os.path.join(BASE, "fonts", "Inter-Medium.otf")
F_SYM = os.path.join(BASE, "fonts", "DejaVuSans.ttf")

CREAM = (246, 241, 232)
CREAM_DARK = (236, 228, 214)
SAGE = (143, 169, 143)
SAGE_LIGHT = (196, 210, 192)
FOREST = (52, 78, 65)
CLAY = (214, 167, 138)

W, H = 1280, 720
SYMBOLS = ["π", "√x", "∫", "x²", "Σ", "sin", "Δ", "log", "∞", "f(x)", "%", "y=kx+b"]


def _font(path, size):
    return ImageFont.truetype(path, size)


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _blobs(img, seed, colors):
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    spots = [(W - 120, -60, 420), (W - 300, H - 80, 300), (-80, H + 40, 260)]
    for (x, y, r), c in zip(spots, colors):
        x += rnd.randint(-40, 40)
        y += rnd.randint(-40, 40)
        d.ellipse([x - r, y - r, x + r, y + r], fill=c + (255,))
    img.paste(img.filter(ImageFilter.GaussianBlur(4)))


def _symbols(img, seed, color):
    rnd = random.Random(seed * 7 + 3)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    syms = rnd.sample(SYMBOLS, 8)
    cells = [(c, r) for c in range(2) for r in range(4)]
    for s, (c, r) in zip(syms, cells):
        size = rnd.randint(36, 60)
        x = int(W * 0.64) + c * 210 + rnd.randint(-30, 30)
        y = 60 + r * 160 + rnd.randint(-25, 25)
        d.text((x, y), s, font=_font(F_SYM, size), fill=color)
    img.alpha_composite(layer)


def _pill(d, xy, text, font, bg, fg):
    x, y = xy
    tw = d.textlength(text, font=font)
    d.rounded_rectangle([x, y, x + tw + 44, y + 52], radius=26, fill=bg)
    d.text((x + 22, y + 9), text, font=font, fill=fg)


def _title(d, text, x, y, max_w, color, start=64, min_size=40, max_lines=3):
    size = start
    while size >= min_size:
        f = _font(F_BOLD, size)
        lines = _wrap(d, text, f, max_w)
        if len(lines) <= max_lines:
            break
        size -= 4
    lh = int(size * 1.18)
    for i, line in enumerate(lines[:max_lines]):
        d.text((x, y + i * lh), line, font=f, fill=color)
    return y + lh * min(len(lines), max_lines)


def _to_bytes(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def lesson_cover(num: int, title: str, brand: str) -> bytes:
    img = Image.new("RGBA", (W, H), CREAM + (255,))
    _blobs(img, num, [SAGE_LIGHT, CREAM_DARK, (230, 214, 198)])
    _symbols(img, num, SAGE + (150,))
    d = ImageDraw.Draw(img)
    _pill(d, (90, 110), f"УРОК {num}", _font(F_SEMI, 26), FOREST, CREAM)
    end = _title(d, title, 90, 200, 700, FOREST)
    d.line([90, end + 30, 170, end + 30], fill=CLAY, width=6)
    d.text((90, H - 100), brand, font=_font(F_MED, 26), fill=(110, 130, 115))
    d.text((90, H - 62), "видео урока скоро появится", font=_font(F_MED, 22), fill=(150, 165, 150))
    return _to_bytes(img)


def review_cover(num: int, title: str, brand: str) -> bytes:
    img = Image.new("RGBA", (W, H), FOREST + (255,))
    _blobs(img, num + 50, [(70, 100, 84), (62, 92, 77), (80, 110, 92)])
    _symbols(img, num + 50, SAGE_LIGHT + (70,))
    d = ImageDraw.Draw(img)
    _pill(d, (90, 110), f"РАЗБОР УРОКА {num}", _font(F_SEMI, 26), CREAM, FOREST)
    end = _title(d, title, 90, 200, 700, CREAM)
    d.line([90, end + 30, 170, end + 30], fill=CLAY, width=6)
    d.text((90, H - 100), "Сверь своё решение с разбором", font=_font(F_MED, 26), fill=SAGE_LIGHT)
    d.text((90, H - 62), "видеоразбор скоро появится", font=_font(F_MED, 22), fill=(150, 175, 155))
    return _to_bytes(img)


def welcome_cover(brand: str, teacher: str) -> bytes:
    img = Image.new("RGBA", (W, H), CREAM + (255,))
    _blobs(img, 999, [SAGE_LIGHT, CREAM_DARK, (230, 214, 198)])
    _symbols(img, 999, SAGE + (150,))
    d = ImageDraw.Draw(img)
    _pill(d, (90, 120), "КЛУБ ПОДГОТОВКИ", _font(F_SEMI, 26), FOREST, CREAM)
    end = _title(d, "ЕГЭ по математике без паники", 90, 210, 720, FOREST, start=72)
    d.line([90, end + 30, 170, end + 30], fill=CLAY, width=6)
    d.text((90, H - 100), f"с {teacher}", font=_font(F_MED, 28), fill=(110, 130, 115))
    d.text((90, H - 62), "урок  →  домашка  →  разбор", font=_font(F_MED, 22), fill=(150, 165, 150))
    return _to_bytes(img)


def finish_cover(brand: str) -> bytes:
    img = Image.new("RGBA", (W, H), SAGE + (255,))
    _blobs(img, 777, [SAGE_LIGHT, (160, 185, 160), (175, 196, 172)])
    _symbols(img, 777, CREAM + (90,))
    d = ImageDraw.Draw(img)
    _pill(d, (90, 120), "ТЫ МОЛОДЕЦ", _font(F_SEMI, 26), CREAM, FOREST)
    end = _title(d, "Все уроки на сегодня пройдены", 90, 210, 720, CREAM, start=68)
    d.line([90, end + 30, 170, end + 30], fill=CLAY, width=6)
    d.text((90, H - 100), "Новые уроки уже в работе", font=_font(F_MED, 26), fill=FOREST)
    return _to_bytes(img)
