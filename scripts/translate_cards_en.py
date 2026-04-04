#!/usr/bin/env python3

import argparse
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont


ROOT = Path("/Users/balbi/Documents/work/TCGArena/Battle Spirits")
IMAGES_ROOT = ROOT / "images" / "cards"

BOUNDS = {
    "Families": (250, 475, 425, 510),
    "Name": (50, 510, 625, 560),
}
CARD_EFFECT_BOUNDS = (35, 560, 635, 930)
SPECIAL_KEYWORD_BOUNDS = (45, 600, 625, 640)
CORE_SYMBOL_BOUNDS = (550, 850, 635, 930)
SOUL_CORE_CARD_PATH = ROOT / "images" / "cards" / "soul-core.png"

LEFT_PADDING_BY_TYPE = {
    "Spirit": 145,  # absolute x
    "Nexus": 140,   # absolute x
    "Magic": 100,   # absolute x
    "X": 145,       # absolute x
}

FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
FONT_INDEX_REGULAR = 0
FONT_INDEX_BOLD = 1

STYLE = {
    "family_bg": (41, 47, 40, 255),   # #292f28
    "name_bg": (10, 12, 12, 255),     # #0a0c0c
    "effect_bg_fallback": (7, 10, 20, 255),
    "text_main": (245, 245, 245, 255),
    "text_family": (236, 236, 236, 255),
    "stroke": (0, 0, 0, 210),
    "keyword_bg": (46, 99, 180, 255),
}

SOUL_COLOR_MAP = {
    "r": "Red",
    "red": "Red",
    "p": "Purple",
    "purple": "Purple",
    "g": "Green",
    "green": "Green",
    "w": "White",
    "white": "White",
    "y": "Yellow",
    "yellow": "Yellow",
    "b": "Blue",
    "blue": "Blue",
}


def load_font(size: int, bold: bool = False):
    index = FONT_INDEX_BOLD if bold else FONT_INDEX_REGULAR
    try:
        return ImageFont.truetype(FONT_PATH, size, index=index)
    except Exception:
        # Fallback if collection index is unavailable on the current system.
        return ImageFont.truetype(FONT_PATH, size)


def api_wikitext(page_title: str) -> str:
    url = (
        "https://battle-spirits.fandom.com/api.php?action=query&titles="
        + quote(page_title)
        + "&prop=revisions&rvprop=content&format=json&formatversion=2&redirects=1"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        obj = json.loads(resp.read().decode("utf-8", "replace"))
    page = obj["query"]["pages"][0]
    if page.get("missing"):
        raise ValueError(f"Missing wiki page: {page_title}")
    revisions = page.get("revisions", [])
    if not revisions:
        raise ValueError(f"No revision content for page: {page_title}")
    return revisions[0].get("content", "")


def api_parsed_html(page_title: str) -> str:
    url = (
        "https://battle-spirits.fandom.com/api.php?action=parse&page="
        + quote(page_title)
        + "&prop=text&format=json"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        obj = json.loads(resp.read().decode("utf-8", "replace"))
    return obj.get("parse", {}).get("text", {}).get("*", "")


def parse_set_cards(set_wikitext: str):
    marker = "Set Card List"
    i = set_wikitext.find(marker)
    if i < 0:
        raise ValueError("Could not locate 'Set Card List' in set page wikitext.")
    section = set_wikitext[i:]

    pattern = re.compile(
        r"\|\s*(\d{2}RSD\d{2}-(?:\d{3}|X\d{2}))\s*\n"
        r"\|\s*\[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*\n"
        r"\|[^\n]*\n"
        r"\|\s*([A-Za-z]+)\s*\n"
        r"\|\s*([^\n]+)\n",
        re.MULTILINE,
    )

    cards = {}
    for m in pattern.finditer(section):
        card_id = m.group(1).strip()
        page_title = m.group(2).strip()
        card_type = m.group(3).strip()
        rarity = m.group(4).strip()
        cards[card_id] = {
            "card_id": card_id,
            "page_title": page_title,
            "type": card_type,
            "rarity": rarity,
        }
    return cards


def parse_template_params(card_wikitext: str):
    start = card_wikitext.find("{{CardTable")
    if start < 0:
        return {}
    tail = card_wikitext[start:]

    # Find matching end of CardTable template with brace-depth tracking.
    depth = 0
    end_idx = None
    i = 0
    while i < len(tail) - 1:
        two = tail[i : i + 2]
        if two == "{{":
            depth += 1
            i += 2
            continue
        if two == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                end_idx = i
                break
            continue
        i += 1
    block = tail[:end_idx] if end_idx is not None else tail

    params = {}
    current_key = None
    for raw in block.splitlines():
        line = raw.rstrip()
        if line.startswith("|") and "=" in line:
            left, right = line[1:].split("=", 1)
            current_key = left.strip().lower()
            params[current_key] = right.strip()
        elif current_key is not None:
            params[current_key] += "\n" + line.strip()
    return params


def wiki_to_text(value: str) -> str:
    if not value:
        return ""
    v = value
    v = re.sub(r"<br\s*/?>", "\n", v, flags=re.IGNORECASE)
    v = v.replace("'''", "").replace("''", "")

    # Convert templates used for keyword-like tags to plain text.
    v = re.sub(r"\{\{[A-Za-z0-9_]+\|([^{}]+)\}\}", r"\1", v)
    v = re.sub(r"\{\{[^{}]+\}\}", "", v)

    # Links
    v = re.sub(r"\[\[File:[^\]]+\]\]", "", v, flags=re.IGNORECASE)
    v = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", v)
    v = re.sub(r"\[\[([^\]]+)\]\]", r"\1", v)

    v = html.unescape(v)
    v = v.replace("&nbsp;", " ")
    v = re.sub(r"[ \t]*\n[ \t]*", "\n", v)
    v = re.sub(r"[ \t]+", " ", v)
    v = re.sub(r"\n+", "\n", v)
    return v.strip()


def html_to_text(value: str) -> str:
    if not value:
        return ""
    v = value
    v = re.sub(r"<br\s*/?>", "\n", v, flags=re.IGNORECASE)
    v = re.sub(r"<[^>]+>", "", v)
    v = html.unescape(v)
    v = re.sub(r"[ \t]*\n[ \t]*", "\n", v)
    v = re.sub(r"[ \t]+", " ", v)
    v = re.sub(r"\n+", "\n", v)
    return v.strip()


def extract_card_effect_cell(parsed_html: str) -> str:
    if not parsed_html:
        return ""
    patterns = [
        # Header row then content row (common on this wiki).
        r"<th[^>]*>\s*(?:<b>\s*)?Card Effects\s*(?:</b>\s*)?</th>[\s\S]*?<td[^>]*>([\s\S]*?)</td>",
        # Typical table-header row.
        r"<th[^>]*>\s*(?:<b>\s*)?Card Effects\s*(?:</b>\s*)?</th>\s*<td[^>]*>([\s\S]*?)</td>",
        # Some pages use td/td instead of th/td.
        r"<td[^>]*>\s*(?:<b>\s*)?Card Effects\s*(?:</b>\s*)?</td>\s*<td[^>]*>([\s\S]*?)</td>",
    ]
    for pat in patterns:
        m = re.search(pat, parsed_html, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def strip_special_block(cell_html: str, special_kind: str) -> str:
    if special_kind not in {"legacy", "soul_magic"}:
        return cell_html
    # Legacy/Soul intro is the first centered block in this cell.
    return re.sub(
        r"^\s*<center>[\s\S]*?</center>\s*(?:<br\s*/?>\s*)*",
        "",
        cell_html,
        count=1,
        flags=re.IGNORECASE,
    )


def _parse_style_dict(style: str):
    out = {}
    for part in (style or "").split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip().lower()] = v.strip()
    return out


def _parse_css_color(raw: str):
    if not raw:
        return None
    v = raw.strip().lower()
    if v in {"transparent", "none"}:
        return None
    named = {
        "white": (255, 255, 255, 255),
        "black": (0, 0, 0, 255),
        "red": (255, 0, 0, 255),
        "blue": (0, 102, 204, 255),
        "yellow": (255, 255, 0, 255),
        "green": (0, 128, 0, 255),
        "purple": (153, 51, 204, 255),
    }
    if v in named:
        return named[v]

    m = re.match(r"^#([0-9a-f]{3,8})$", v)
    if m:
        hx = m.group(1)
        if len(hx) == 3:
            r, g, b = [int(ch * 2, 16) for ch in hx]
            return (r, g, b, 255)
        if len(hx) == 6:
            return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16), 255)
        if len(hx) == 8:
            return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16), int(hx[6:8], 16))

    m = re.match(r"^rgba?\(([^)]+)\)$", v)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) >= 3:
            try:
                r = int(float(parts[0]))
                g = int(float(parts[1]))
                b = int(float(parts[2]))
                a = 255
                if len(parts) >= 4:
                    alpha = float(parts[3])
                    a = int(max(0.0, min(1.0, alpha)) * 255)
                return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), a)
            except ValueError:
                return None
    return None


def _background_from_style(style: str):
    props = _parse_style_dict(style)
    bg = props.get("background-color") or props.get("background")
    if not bg:
        return None
    # For gradients, pick the center stop color for an approximate flat chip.
    if "gradient" in bg.lower():
        stops = re.findall(r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))", bg)
        if not stops:
            return None
        return _parse_css_color(stops[len(stops) // 2])
    m = re.search(r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|[a-zA-Z]+)", bg)
    return _parse_css_color(m.group(1)) if m else None


def _foreground_from_style(style: str):
    props = _parse_style_dict(style)
    return _parse_css_color(props.get("color", ""))


class EffectRunsHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = [{"fg": None, "bg": None, "bold": False}]
        self.runs = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "br":
            self.runs.append({"kind": "break"})
            return

        attr_map = {k.lower(): v for k, v in attrs}
        base = dict(self.stack[-1])
        style = attr_map.get("style", "")
        bg = _background_from_style(style)
        fg = _foreground_from_style(style)
        if bg is not None:
            base["bg"] = bg
        if fg is not None:
            base["fg"] = fg
        props = _parse_style_dict(style)
        weight = props.get("font-weight", "").strip().lower()
        if weight:
            if weight in {"bold", "bolder"}:
                base["bold"] = True
            elif weight in {"normal", "lighter"}:
                base["bold"] = False
            else:
                try:
                    base["bold"] = int(weight) >= 600
                except ValueError:
                    pass
        if t == "font":
            font_color = _parse_css_color(attr_map.get("color", ""))
            if font_color is not None:
                base["fg"] = font_color
        if t in {"b", "strong"}:
            base["bold"] = True
        self.stack.append(base)

    def handle_endtag(self, _tag):
        if len(self.stack) > 1:
            self.stack.pop()

    def handle_data(self, data):
        txt = html.unescape(data or "").replace("\xa0", " ")
        txt = re.sub(r"\s+", " ", txt).strip()
        if not txt:
            return
        cur = self.stack[-1]
        self.runs.append(
            {
                "kind": "text",
                "text": txt,
                "fg": cur.get("fg"),
                "bg": cur.get("bg"),
                "bold": bool(cur.get("bold")),
            }
        )


def extract_effect_runs_from_parsed_html(parsed_html: str, special_kind: str):
    cell = extract_card_effect_cell(parsed_html)
    if not cell:
        return []
    body = strip_special_block(cell, special_kind)
    parser = EffectRunsHTMLParser()
    parser.feed(body)

    runs = []
    for r in parser.runs:
        if r["kind"] == "break":
            if runs and runs[-1]["kind"] != "break":
                runs.append({"kind": "break"})
            continue

        if runs and runs[-1]["kind"] == "text":
            prev = runs[-1]
            if prev.get("bg") == r.get("bg") and prev.get("fg") == r.get("fg") and prev.get("bold") == r.get("bold"):
                prev["text"] += " " + r["text"]
                continue
        runs.append(r)

    while runs and runs[0]["kind"] == "break":
        runs.pop(0)
    while runs and runs[-1]["kind"] == "break":
        runs.pop()
    return runs


def extract_special_text_from_parsed_html(parsed_html: str, kind: str) -> str:
    if not parsed_html:
        return ""

    # Isolate the Card Effects cell block to avoid false positives.
    cell = extract_card_effect_cell(parsed_html)

    if kind == "legacy":
        pat = (
            r"Legacy\s*</b>\s*</span>\s*<br\s*/?>\s*"
            r"<span[^>]*>([\s\S]*?)</span>"
        )
    elif kind == "soul_magic":
        pat = (
            r"Soul\s*Magic[\s\S]*?</b>\s*</span>\s*<br\s*/?>\s*"
            r"<span[^>]*>([\s\S]*?)</span>"
        )
    else:
        return ""

    m = re.search(pat, cell, flags=re.IGNORECASE)
    if not m:
        return ""
    return html_to_text(m.group(1))


def parse_effect(raw_effect: str):
    if not raw_effect:
        return {"level": "", "keyword": "", "body": "", "special": None}
    raw = raw_effect
    special = None

    soul_match = re.search(r"\{\{\s*Soul Magic\s*\|\s*([a-zA-Z]+)\s*\}\}", raw, flags=re.IGNORECASE)
    if soul_match:
        code = soul_match.group(1).strip().lower()
        special = {"kind": "soul_magic", "color": SOUL_COLOR_MAP.get(code, code.title())}
        raw = raw.replace(soul_match.group(0), "", 1)

    legacy_match = re.search(r"\{\{\s*ST\s*\|\s*Legacy\s*\}\}", raw, flags=re.IGNORECASE)
    if legacy_match and special is None:
        special = {"kind": "legacy"}
        raw = raw.replace(legacy_match.group(0), "", 1)

    # Only treat [LV...] blocks as level headers; ignore wiki links like [[Windfang]].
    level_match = re.search(r"\[LV[^\]]*\]", raw, flags=re.IGNORECASE)
    level = level_match.group(0).strip() if level_match else ""
    if level:
        raw = raw.replace(level, "", 1)

    kw_match = re.search(r"\{\{\s*(?:ST|tk|Invoke)\s*\|\s*([^{}|]+)\s*\}\}", raw, flags=re.IGNORECASE)
    keyword = wiki_to_text(kw_match.group(1)).strip() if kw_match else ""
    if kw_match:
        raw = raw.replace(kw_match.group(0), "", 1)

    plain = wiki_to_text(raw)
    body = plain
    body = re.sub(r"^[\s:,-]+", "", body)
    return {"level": level, "keyword": keyword, "body": body, "special": special}


def fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, stroke=1):
    size = start_size
    while size > 8:
        font = load_font(size, bold=False)
        l, t, r, b = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        if (r - l) <= max_width:
            return font
        size -= 1
    return load_font(8, bold=False)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, stroke=1):
    words = text.split()
    if not words:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        test = f"{current} {word}"
        l, t, r, b = draw.textbbox((0, 0), test, font=font, stroke_width=stroke)
        if (r - l) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _text_width(draw: ImageDraw.ImageDraw, text: str, font, stroke=1):
    if not text:
        return 0
    l, t, r, b = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return r - l


def _split_long_token(draw: ImageDraw.ImageDraw, token: str, font, max_width: int, stroke=1):
    if not token:
        return "", ""
    if _text_width(draw, token, font, stroke=stroke) <= max_width:
        return token, ""
    lo, hi = 1, len(token)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        part = token[:mid]
        if _text_width(draw, part, font, stroke=stroke) <= max_width:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best <= 0:
        return "", token
    return token[:best], token[best:]


def _tokenize_with_newlines(text: str):
    text = re.sub(r"\n+", "\n", text.strip())
    tokens = []
    parts = text.split("\n")
    for i, part in enumerate(parts):
        words = part.split()
        tokens.extend(words)
        if i < len(parts) - 1:
            tokens.append("\n")
    return tokens


def wrap_text_flow_regions(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    start_x: int,
    start_y: int,
    top_right_x: int,
    lower_right_x: int,
    lower_start_y: int,
    max_bottom_y: int,
    stroke=1,
    line_spacing=5,
    break_gap=4,
):
    tokens = _tokenize_with_newlines(text)
    if not tokens:
        return []

    line_h = font.size + line_spacing
    lines = []
    y = start_y
    current = ""

    def width_for_y(y):
        # Use narrower width when line is in or crossing the lower symbol zone.
        if y >= lower_start_y:
            right = lower_right_x
        elif (y + line_h) > lower_start_y:
            right = min(top_right_x, lower_right_x)
        else:
            right = top_right_x
        return max(40, right - start_x)

    def push_current(force=False):
        nonlocal current, y
        if not current and not force:
            return True
        if (y + line_h) > max_bottom_y:
            return False
        lines.append((current, y))
        y += line_h
        current = ""
        return True

    for token in tokens:
        if token == "\n":
            if not push_current(force=bool(current)):
                return None
            y += break_gap
            continue

        max_w = width_for_y(y)
        candidate = token if not current else f"{current} {token}"
        if _text_width(draw, candidate, font, stroke=stroke) <= max_w:
            current = candidate
            continue

        if current:
            if not push_current():
                return None
            max_w = width_for_y(y)

        remaining = token
        while remaining:
            part, rem = _split_long_token(draw, remaining, font, max_w, stroke=stroke)
            if not part:
                return None
            if rem:
                current = part
                if not push_current():
                    return None
                max_w = width_for_y(y)
                remaining = rem
            else:
                current = part
                remaining = ""

    if current:
        if not push_current():
            return None
    if not lines:
        if (y + line_h) > max_bottom_y:
            return None
        lines.append(("", y))
    return lines


def fit_flow_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    start_x: int,
    start_y: int,
    top_right_x: int,
    lower_right_x: int,
    lower_start_y: int,
    max_bottom_y: int,
    start_size: int,
    min_size: int = 10,
    stroke: int = 1,
    line_spacing: int = 5,
    break_gap: int = 4,
):
    size = start_size
    best = None
    while size >= min_size:
        font = load_font(size, bold=False)
        lines = wrap_text_flow_regions(
            draw,
            text,
            font=font,
            start_x=start_x,
            start_y=start_y,
            top_right_x=top_right_x,
            lower_right_x=lower_right_x,
            lower_start_y=lower_start_y,
            max_bottom_y=max_bottom_y,
            stroke=stroke,
            line_spacing=line_spacing,
            break_gap=break_gap,
        )
        if lines is not None:
            line_h = size + line_spacing
            return font, lines, line_h
        best = (font, [(text, start_y)], size + line_spacing)
        size -= 1
    return best


def runs_to_tokens(effect_runs):
    tokens = []
    for run in effect_runs or []:
        if run.get("kind") == "break":
            if tokens and tokens[-1]["kind"] != "break":
                tokens.append({"kind": "break"})
            continue
        for word in run.get("text", "").split():
            tokens.append(
                {
                    "kind": "word",
                    "text": word,
                    "fg": run.get("fg"),
                    "bg": run.get("bg"),
                    "bold": bool(run.get("bold")),
                }
            )
    while tokens and tokens[0]["kind"] == "break":
        tokens.pop(0)
    while tokens and tokens[-1]["kind"] == "break":
        tokens.pop()
    return tokens


def _token_font(token, font_regular, font_bold):
    return font_bold if token.get("bold") else font_regular


def _line_token_width(draw: ImageDraw.ImageDraw, token, font_regular, font_bold, stroke=1):
    font = _token_font(token, font_regular, font_bold)
    return _text_width(draw, token["text"], font, stroke=stroke)


def _line_width(draw: ImageDraw.ImageDraw, line_tokens, font_regular, font_bold, space_w, stroke=1):
    if not line_tokens:
        return 0
    return (
        sum(_line_token_width(draw, t, font_regular, font_bold, stroke=stroke) for t in line_tokens)
        + space_w * (len(line_tokens) - 1)
    )


def _split_styled_token(draw: ImageDraw.ImageDraw, token, font_regular, font_bold, max_width: int, stroke=1):
    font = _token_font(token, font_regular, font_bold)
    part, rem = _split_long_token(draw, token["text"], font, max_width, stroke=stroke)
    if not part:
        return None, token
    left = dict(token)
    left["text"] = part
    right = None
    if rem:
        right = dict(token)
        right["text"] = rem
    return left, right


def layout_styled_tokens_flow(
    draw: ImageDraw.ImageDraw,
    tokens,
    font_regular,
    font_bold,
    start_x: int,
    start_y: int,
    top_right_x: int,
    lower_right_x: int,
    lower_start_y: int,
    max_bottom_y: int,
    stroke=1,
    line_spacing=5,
    break_gap=4,
):
    if not tokens:
        return []
    line_h = max(font_regular.size, font_bold.size) + line_spacing
    y = start_y
    lines = []
    cur = []
    space_w = max(
        _text_width(draw, " ", font_regular, stroke=stroke),
        _text_width(draw, " ", font_bold, stroke=stroke),
    )

    def width_for_y(cy):
        if cy >= lower_start_y:
            right = lower_right_x
        elif (cy + line_h) > lower_start_y:
            right = min(top_right_x, lower_right_x)
        else:
            right = top_right_x
        return max(40, right - start_x)

    def push_current(force=False):
        nonlocal y, cur
        if not cur and not force:
            return True
        if (y + line_h) > max_bottom_y:
            return False
        lines.append((y, cur))
        y += line_h
        cur = []
        return True

    for tok in tokens:
        if tok["kind"] == "break":
            if not push_current(force=bool(cur)):
                return None
            y += break_gap
            continue

        max_w = width_for_y(y)
        candidate = cur + [tok]
        if _line_width(draw, candidate, font_regular, font_bold, space_w, stroke=stroke) <= max_w:
            cur = candidate
            continue

        if cur:
            if not push_current():
                return None
            max_w = width_for_y(y)

        working = tok
        while working is not None:
            room_w = max_w - _line_width(draw, cur, font_regular, font_bold, space_w, stroke=stroke)
            if cur:
                room_w -= space_w
            if room_w <= 10:
                if not push_current():
                    return None
                max_w = width_for_y(y)
                room_w = max_w

            part, rem = _split_styled_token(
                draw,
                working,
                font_regular,
                font_bold,
                room_w,
                stroke=stroke,
            )
            if part is None:
                return None
            cur = cur + [part]
            if rem is None:
                working = None
            else:
                if not push_current():
                    return None
                max_w = width_for_y(y)
                working = rem

    if cur:
        if not push_current():
            return None
    return lines


def fit_styled_tokens_flow(
    draw: ImageDraw.ImageDraw,
    tokens,
    start_x: int,
    start_y: int,
    top_right_x: int,
    lower_right_x: int,
    lower_start_y: int,
    max_bottom_y: int,
    start_size: int,
    min_size: int = 10,
    stroke: int = 1,
    line_spacing: int = 5,
    break_gap: int = 4,
):
    size = start_size
    best = None
    while size >= min_size:
        font_regular = load_font(size, bold=False)
        font_bold = load_font(size, bold=True)
        lines = layout_styled_tokens_flow(
            draw,
            tokens=tokens,
            font_regular=font_regular,
            font_bold=font_bold,
            start_x=start_x,
            start_y=start_y,
            top_right_x=top_right_x,
            lower_right_x=lower_right_x,
            lower_start_y=lower_start_y,
            max_bottom_y=max_bottom_y,
            stroke=stroke,
            line_spacing=line_spacing,
            break_gap=break_gap,
        )
        if lines is not None:
            return font_regular, font_bold, lines
        best = (font_regular, font_bold, [])
        size -= 1
    return best


def draw_styled_token_lines(
    draw: ImageDraw.ImageDraw,
    lines,
    font_regular,
    font_bold,
    start_x: int,
    default_fg,
    stroke=1,
):
    space_w = max(
        _text_width(draw, " ", font_regular, stroke=stroke),
        _text_width(draw, " ", font_bold, stroke=stroke),
    )
    line_h = max(font_regular.size, font_bold.size)
    for y, tokens in lines:
        x = start_x
        for i, tok in enumerate(tokens):
            txt = tok["text"]
            fg = tok.get("fg") or default_fg
            bg = tok.get("bg")
            font = _token_font(tok, font_regular, font_bold)
            w = _text_width(draw, txt, font, stroke=stroke)
            if bg is not None:
                draw.rounded_rectangle(
                    (x - 3, y - 2, x + w + 3, y + line_h + 2),
                    radius=3,
                    fill=bg,
                )
            draw.text(
                (x, y),
                txt,
                fill=fg,
                font=font,
                stroke_width=stroke,
                stroke_fill=STYLE["stroke"],
            )
            x += w
            if i < len(tokens) - 1:
                x += space_w


def fit_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    start_size: int,
    min_size: int = 11,
    stroke: int = 1,
    line_spacing: int = 5,
):
    size = start_size
    best = None
    while size >= min_size:
        font = load_font(size, bold=False)
        lines = wrap_text(draw, text, font, max_width=max_width, stroke=stroke)
        line_h = size + line_spacing
        needed = line_h * max(1, len(lines))
        if needed <= max_height:
            return font, lines, line_h
        best = (font, lines, line_h)
        size -= 1
    return best


def sample_median_color(img: Image.Image, box, fallback):
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return fallback
    crop = img.crop((x1, y1, x2, y2)).convert("RGB")
    pixels = list(crop.getdata())
    if not pixels:
        return fallback
    rs = sorted(p[0] for p in pixels)
    gs = sorted(p[1] for p in pixels)
    bs = sorted(p[2] for p in pixels)
    m = len(pixels) // 2
    return (rs[m], gs[m], bs[m], 255)


def effect_box_for_card(card_id: str, card_type: str):
    fx_x1, fx_y1, fx_x2, fx_y2 = CARD_EFFECT_BOUNDS

    is_x_card = "-X" in card_id
    abs_x = LEFT_PADDING_BY_TYPE["X"] if is_x_card else LEFT_PADDING_BY_TYPE.get(card_type, LEFT_PADDING_BY_TYPE["Spirit"])
    local_pad = max(0, abs_x - fx_x1)
    return (fx_x1 + local_pad, fx_y1, fx_x2, fx_y2)


def cleanup_effect_bg(draw: ImageDraw.ImageDraw, ex1, text_top, ex2, ey2, fill):
    # Opaque cleanup where translated effect text is drawn.
    # Use split geometry so we can continue below symbol zone on the left side.
    strip_top = text_top
    sym_x1, sym_y1, sym_x2, sym_y2 = CORE_SYMBOL_BOUNDS
    top_end = min(sym_y1 - 6, ey2 - 6)
    if top_end > strip_top:
        draw.rounded_rectangle((ex1, strip_top, ex2, top_end), radius=5, fill=fill)

    lower_start = max(strip_top, sym_y1 - 6)
    lower_end = ey2 - 6
    if lower_end > lower_start:
        draw.rounded_rectangle((ex1, lower_start, sym_x1 - 8, lower_end), radius=5, fill=fill)
    return strip_top, ey2 - 6


def load_soul_core_icon(size=22):
    if not SOUL_CORE_CARD_PATH.exists():
        return None
    # Crop gem center from soul-core card art.
    base = Image.open(SOUL_CORE_CARD_PATH).convert("RGBA")
    w, h = base.size
    crop = base.crop((int(w * 0.34), int(h * 0.34), int(w * 0.66), int(h * 0.66)))
    return crop.resize((size, size), Image.Resampling.LANCZOS)


def render_card_translation(src_image: Path, out_image: Path, card_data: dict):
    img = Image.open(src_image).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Name/family cleanup
    family_box = BOUNDS["Families"]
    name_box = BOUNDS["Name"]
    draw.rounded_rectangle(family_box, radius=4, fill=STYLE["family_bg"])
    draw.rounded_rectangle(name_box, radius=6, fill=STYLE["name_bg"])

    # Effects cleanup (tone sampled from source image)
    effect_box = effect_box_for_card(
        card_data["card_id"],
        card_data["type"],
    )
    ex1, ey1, ex2, ey2 = effect_box
    sample_box = (
        ex1 + 20,
        min(ey1 + 180, ey2 - 80),
        min(ex2 - 20, CORE_SYMBOL_BOUNDS[0] - 12),
        min(ey2 - 20, CORE_SYMBOL_BOUNDS[1] - 20),
    )
    effect_bg = sample_median_color(img, sample_box, STYLE["effect_bg_fallback"])

    # Optional special region (Legacy / Soul Magic) is fixed bounds.
    special = card_data["effect"].get("special")
    text_top = ey1 + 8
    if special:
        sx1, sy1, sx2, sy2 = SPECIAL_KEYWORD_BOUNDS
        draw.rounded_rectangle((sx1, sy1, sx2, sy2), radius=5, fill=effect_bg)
        special_text = card_data.get("special_text", "").strip()
        if special["kind"] == "soul_magic":
            soul_text = special_text or (
                f"If you control any {special.get('color', 'Red')} symbol, "
                "you can use it with just the Stan Soul Core."
            )
            icon = load_soul_core_icon(22)
            icon_w = icon.width if icon else 0
            text_max_w = (sx2 - sx1 - 16 - icon_w - (8 if icon else 0))
            special_font = fit_font(draw, soul_text, max_width=text_max_w, start_size=17, stroke=1)
            draw.text(
                (sx1 + 8, sy1 + (sy2 - sy1) // 2),
                soul_text,
                fill=STYLE["text_main"],
                font=special_font,
                anchor="lm",
                stroke_width=1,
                stroke_fill=STYLE["stroke"],
            )
            if icon:
                ix = sx2 - 8 - icon.width
                iy = sy1 + (sy2 - sy1 - icon.height) // 2
                img.paste(icon, (ix, iy), icon)
        elif special["kind"] == "legacy":
            legacy_text = special_text or "Legacy"
            legacy_font = fit_font(draw, legacy_text, max_width=(sx2 - sx1 - 16), start_size=17, stroke=1)
            draw.text(
                ((sx1 + sx2) // 2, sy1 + (sy2 - sy1) // 2),
                legacy_text,
                fill=STYLE["text_main"],
                font=legacy_font,
                anchor="mm",
                stroke_width=1,
                stroke_fill=STYLE["stroke"],
            )
        text_top = sy2 + 6

    strip_top, strip_bottom = cleanup_effect_bg(draw, ex1, text_top, ex2, ey2, effect_bg)

    # Family
    family_font = fit_font(
        draw, card_data["family"], max_width=(family_box[2] - family_box[0] - 16), start_size=28, stroke=1
    )
    draw.text(
        ((family_box[0] + family_box[2]) // 2, (family_box[1] + family_box[3]) // 2),
        card_data["family"],
        fill=STYLE["text_family"],
        font=family_font,
        anchor="mm",
        stroke_width=1,
        stroke_fill=(0, 0, 0, 190),
    )

    # Name
    name_font = fit_font(
        draw, card_data["name"], max_width=(name_box[2] - name_box[0] - 24), start_size=46, stroke=2
    )
    draw.text(
        ((name_box[0] + name_box[2]) // 2, (name_box[1] + name_box[3]) // 2 + 3),
        card_data["name"],
        fill=STYLE["text_main"],
        font=name_font,
        anchor="mm",
        stroke_width=2,
        stroke_fill=STYLE["stroke"],
    )

    # Effects
    header_x = ex1 + 10
    header_y = strip_top + 6
    level = card_data["effect"]["level"]
    keyword = card_data["effect"]["keyword"]
    body = card_data["effect"]["body"]
    rich_runs = card_data.get("effect_runs") or []

    text_start_x = ex1 + 10
    text_right_top = ex2 - 10
    text_right_lower = CORE_SYMBOL_BOUNDS[0] - 10
    symbol_top = CORE_SYMBOL_BOUNDS[1] - 6
    body_y = header_y + 38
    body_bottom = max(body_y + 24, strip_bottom - 2)

    if rich_runs:
        rich_tokens = runs_to_tokens(rich_runs)
        fit_rich = fit_styled_tokens_flow(
            draw,
            tokens=rich_tokens,
            start_x=text_start_x,
            start_y=header_y,
            top_right_x=text_right_top,
            lower_right_x=text_right_lower,
            lower_start_y=symbol_top,
            max_bottom_y=body_bottom,
            start_size=26,
            min_size=9,
            stroke=1,
            line_spacing=5,
            break_gap=4,
        )
        if fit_rich is not None:
            rich_font_regular, rich_font_bold, rich_lines = fit_rich
            draw_styled_token_lines(
                draw,
                lines=rich_lines,
                font_regular=rich_font_regular,
                font_bold=rich_font_bold,
                start_x=text_start_x,
                default_fg=STYLE["text_main"],
                stroke=1,
            )
        else:
            # Fallback to plain text rendering if styled layout cannot fit.
            rich_runs = []

    if not rich_runs:
        lv_font = fit_font(draw, level or "[LV]", max_width=84, start_size=24, stroke=1)
        if level:
            draw.text(
                (header_x, header_y),
                level,
                fill=STYLE["text_main"],
                font=lv_font,
                stroke_width=1,
                stroke_fill=STYLE["stroke"],
            )
            level_box = draw.textbbox((header_x, header_y), level, font=lv_font, stroke_width=1)
        else:
            level_box = (header_x, header_y, header_x, header_y)

        if keyword:
            kw_font = fit_font(draw, keyword, max_width=230, start_size=23, stroke=1)
            kw_x = level_box[2] + 10
            kw_y = header_y
            kw_box = draw.textbbox((kw_x, kw_y), keyword, font=kw_font, stroke_width=1)
            draw.rounded_rectangle(
                (kw_box[0] - 6, kw_box[1] - 3, kw_box[2] + 6, kw_box[3] + 3),
                radius=5,
                fill=STYLE["keyword_bg"],
            )
            draw.text(
                (kw_x, kw_y),
                keyword,
                fill=STYLE["text_main"],
                font=kw_font,
                stroke_width=1,
                stroke_fill=(0, 0, 0, 175),
            )

        fit_res = fit_flow_text(
            draw,
            body,
            start_x=text_start_x,
            start_y=body_y,
            top_right_x=text_right_top,
            lower_right_x=text_right_lower,
            lower_start_y=symbol_top,
            max_bottom_y=body_bottom,
            start_size=26,
            min_size=9,
            stroke=1,
            line_spacing=5,
            break_gap=4,
        )
        if fit_res is None:
            body_font = load_font(9, bold=False)
            lines = [(body, body_y)]
        else:
            body_font, lines, _line_h = fit_res
        for text_line, y in lines:
            if not text_line:
                continue
            draw.text(
                (text_start_x, y),
                text_line,
                fill=STYLE["text_main"],
                font=body_font,
                stroke_width=1,
                stroke_fill=STYLE["stroke"],
            )

    out_image.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_image, format="PNG", optimize=True)


def build_card_data(card_id: str, set_row: dict, wikitext: str):
    params = parse_template_params(wikitext)
    page_title = set_row["page_title"]
    card_type = wiki_to_text(params.get("type", set_row.get("type", "Spirit"))).strip() or "Spirit"
    family = wiki_to_text(params.get("family", "")).strip() or card_type

    effect_raw = params.get("effect", "")
    effect = parse_effect(effect_raw)

    keyword_param = wiki_to_text(params.get("keyword", "")).strip().lower()
    has_legacy = (
        (effect.get("special", {}) or {}).get("kind") == "legacy"
        or "legacy" in keyword_param
    )
    has_soul = (
        (effect.get("special", {}) or {}).get("kind") == "soul_magic"
        or "soul magic" in keyword_param
    )
    if has_soul and not effect.get("special"):
        color_param = wiki_to_text(params.get("color", "")).strip()
        effect["special"] = {"kind": "soul_magic", "color": color_param or "Red"}
    if has_legacy and not effect.get("special"):
        effect["special"] = {"kind": "legacy"}

    special_text = ""
    parsed_html = ""
    special_kind = (effect.get("special") or {}).get("kind")
    try:
        parsed_html = api_parsed_html(page_title)
    except Exception:
        parsed_html = ""

    if special_kind in {"legacy", "soul_magic"} and parsed_html:
        try:
            special_text = extract_special_text_from_parsed_html(parsed_html, special_kind)
        except Exception:
            special_text = ""
    effect_runs = extract_effect_runs_from_parsed_html(parsed_html, special_kind) if parsed_html else []

    return {
        "card_id": card_id,
        "type": card_type,
        "name": page_title,
        "family": family,
        "effect": effect,
        "has_legacy": has_legacy,
        "has_soul_magic": has_soul,
        "special_text": special_text,
        "effect_runs": effect_runs,
    }


def untranslated_images_for_set(set_id: str):
    set_dir = IMAGES_ROOT / set_id
    if not set_dir.exists():
        raise FileNotFoundError(f"Set folder not found: {set_dir}")
    src_images = sorted(
        p
        for p in set_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".webp", ".png", ".jpg", ".jpeg"}
        and "-en" not in p.stem
        and re.match(rf"^{re.escape(set_id)}-(?:\d{{3}}|X\d{{2}})$", p.stem)
    )
    for src in src_images:
        out = src.with_name(f"{src.stem}-en.png")
        yield src, out


def find_source_for_card(set_id: str, card_id: str):
    set_dir = IMAGES_ROOT / set_id
    if not set_dir.exists():
        raise FileNotFoundError(f"Set folder not found: {set_dir}")
    for ext in (".webp", ".png", ".jpg", ".jpeg"):
        candidate = set_dir / f"{card_id}{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Source image not found for card {card_id} in {set_dir}")


def process_set(set_id: str, force: bool = False):
    set_wikitext = api_wikitext(set_id)
    set_cards = parse_set_cards(set_wikitext)

    total = 0
    done = 0
    skipped = 0
    for src, out in untranslated_images_for_set(set_id):
        total += 1
        card_id = src.stem
        if out.exists() and not force:
            skipped += 1
            continue

        row = set_cards.get(card_id)
        if not row:
            print(f"Skip {card_id}: missing card row in set page")
            skipped += 1
            continue

        try:
            card_wikitext = api_wikitext(row["page_title"])
            card_data = build_card_data(card_id, row, card_wikitext)
            render_card_translation(src, out, card_data)
            done += 1
            print(f"Translated {card_id} -> {out.name}")
        except Exception as exc:
            print(f"Failed {card_id}: {exc}")
            skipped += 1

    print(f"Set {set_id}: translated={done}, skipped={skipped}, checked={total}")


def process_single_card(card_id: str):
    m = re.match(r"^(\d{2}RSD\d{2})-(?:\d{3}|X\d{2})$", card_id)
    if not m:
        raise ValueError(f"Invalid card id format: {card_id}")
    set_id = m.group(1)

    set_wikitext = api_wikitext(set_id)
    set_cards = parse_set_cards(set_wikitext)
    row = set_cards.get(card_id)
    if not row:
        raise ValueError(f"Card {card_id} not found in set page {set_id}")

    src = find_source_for_card(set_id, card_id)
    out = src.with_name(f"{card_id}-en.png")

    card_wikitext = api_wikitext(row["page_title"])
    card_data = build_card_data(card_id, row, card_wikitext)
    render_card_translation(src, out, card_data)
    print(f"Translated {card_id} -> {out.name} (overwrite enabled)")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate English translation overlays for Battle Spirits card images."
    )
    parser.add_argument("--set", dest="set_id", help="Set ID, e.g. 26RSD01")
    parser.add_argument(
        "--card",
        dest="card_id",
        help="Single card id, e.g. 26RSD01-014 (always overwrites existing -en image)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing -en outputs")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.card_id:
        process_single_card(args.card_id)
        return
    if not args.set_id:
        raise SystemExit("Provide --set <SET_ID> or --card <CARD_ID>.")
    process_set(args.set_id, force=args.force)


if __name__ == "__main__":
    main()
