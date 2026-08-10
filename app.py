import socket
import ssl
import threading
import time
import json
import os
import random
import math
import re
import urllib.parse
from datetime import datetime, timezone, timedelta
from flask import Flask

try:
    import requests
except ImportError:
    requests = None

try:
    from youtubesearchpython import VideosSearch
except ImportError:
    VideosSearch = None

# ============================================================
# CONFIGURATION
# ============================================================
SERVER = os.environ.get("IRC_SERVER", "irc.hybridirc.com")
PORT = int(os.environ.get("IRC_PORT", "6697"))          # SSL port
CHANNELS = ["#ChatWithWorld", "#Games", "#CWWHelp"]
YT_CHANNEL = os.environ.get("YT_CHANNEL", "#ChatWithWorld")
DEFAULT_ADMIN = "Antonio"

NICKSERV_PASS = os.environ.get("NICKSERV_PASS", "").strip()
COMMAND_COOLDOWN = 10  

# ============================================================
# WEB SERVER
# ============================================================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bots are active and running 24/7.", 200

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ============================================================
# BASE IRC BOT ENGINE (UPDATED WITH FLOOD PROTECTION)
# ============================================================
class IRCBot:
    def __init__(self, nickname, realname, channels=None, send_delay=0.5):
        self.nickname = nickname
        self.realname = realname
        self.admin = DEFAULT_ADMIN
        self.channels = channels if channels is not None else list(CHANNELS)
        self.send_delay = send_delay  
        self._send_lock = threading.Lock() # Prevents multiple threads from flooding
        self.only_admin_mode = False
        self.functional_mode = True
        self.animations_enabled = True
        self.running = False
        self.irc = None
        self._registered = False
        self._cap_ended = False

    def send(self, msg):
        if self.irc is None:
            return
        try:
            self.irc.sendall((msg + "\r\n").encode("utf-8", errors="replace"))
            print(f"[{self.nickname}] >>> {msg}")
        except Exception as e:
            print(f"[{self.nickname}] Send error: {e}")
            self.running = False

    def privmsg(self, target, msg):
        """Thread-safe messaging with forced anti-flood delay."""
        if not msg:
            return
        # The lock ensures only ONE message is processed at a time across all threads
        with self._send_lock:
            self.send(f"PRIVMSG {target} :{msg}")
            if self.send_delay:
                time.sleep(self.send_delay)

    def is_admin(self, prefix):
        nick = prefix.split('!')[0].lstrip(':')
        return nick.lower() == self.admin.lower()

    def _create_socket(self):
        raw_socket = socket.create_connection((SERVER, PORT), timeout=30)
        context = ssl.create_default_context()
        if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
            context.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
        wrapped = context.wrap_socket(raw_socket, server_hostname=SERVER)
        return wrapped

    def idle_prevention(self):
        while self.running:
            time.sleep(180)
            if self.running:
                self.send(f"PING :{SERVER}")

    def connect_once(self):
        print(f"[{self.nickname}] Connecting to {SERVER}:{PORT} (SSL)...")
        self.running = True
        self._registered = False
        self._cap_ended = False

        self.irc = self._create_socket()
        self.irc.settimeout(300) 
        print(f"[{self.nickname}] TLS connection established.")

        self.send("CAP LS 302")
        self.send(f"NICK {self.nickname}")
        self.send(f"USER {self.nickname} 0 * :{self.realname}")

        threading.Thread(target=self.idle_prevention, daemon=True).start()

        buffer = ""
        while self.running:
            try:
                data = self.irc.recv(4096)
            except socket.timeout:
                break
            except Exception:
                break

            if not data:
                break

            buffer += data.decode("utf-8", errors="replace")
            while "\r\n" in buffer:
                line, buffer = buffer.split("\r\n", 1)
                if not line: continue
                self._handle_protocol_line(line)
                try:
                    self.handle_message(line)
                except Exception as err:
                    print(f"[{self.nickname}] Message Error: {err}")

        self.running = False
        try:
            if self.irc: self.irc.close()
        except: pass

    def _handle_protocol_line(self, line):
        parts = line.split(' ')
        if line.startswith("PING"):
            payload = line[5:] if len(line) > 5 else ""
            self.send(f"PONG {payload}")
            return
        if len(parts) < 2: return
        if parts[1] == "CAP" and not self._cap_ended:
            self.send("CAP END")
            self._cap_ended = True
            return
        if parts[1] == "001" and not self._registered:
            self._registered = True
            self.send(f"MODE {self.nickname} +B")
            if NICKSERV_PASS:
                self.send(f"PRIVMSG NickServ :IDENTIFY {NICKSERV_PASS}")
                time.sleep(1)
            for chan in self.channels:
                self.send(f"JOIN {chan}")
                time.sleep(0.5)

    def handle_message(self, line):
        pass

    def run_forever(self):
        backoff = 5
        while True:
            try:
                self.connect_once()
            except Exception as e:
                print(f"[{self.nickname}] Loop error: {e}")
            was_reg = self._registered
            self.running = False
            self._registered = False
            wait = 5 if was_reg else backoff
            time.sleep(wait)
            backoff = 5 if was_reg else min(backoff * 2, 300)

# ============================================================
# TEXT LAB ENGINE
# ============================================================
class TextLab:
    _GLYPHS = {
        'A': [".###.", "#...#", "#####", "#...#", "#...#"],
        'B': ["####.", "#...#", "####.", "#...#", "####."],
        'C': [".####", "#....", "#....", "#....", ".####"],
        'D': ["####.", "#...#", "#...#", "#...#", "####."],
        'E': ["#####", "#....", "###..", "#....", "#####"],
        'F': ["#####", "#....", "###..", "#....", "#...."],
        'G': [".####", "#....", "#..##", "#...#", ".####"],
        'H': ["#...#", "#...#", "#####", "#...#", "#...#"],
        'I': ["#####", "..#..", "..#..", "..#..", "#####"],
        'J': ["..###", "...#.", "...#.", "#..#.", ".##.."],
        'K': ["#..#.", "#.#..", "##...", "#.#..", "#..#."],
        'L': ["#....", "#....", "#....", "#....", "#####"],
        'M': ["#...#", "##.##", "#.#.#", "#...#", "#...#"],
        'N': ["#...#", "##..#", "#.#.#", "#..##", "#...#"],
        'O': [".###.", "#...#", "#...#", "#...#", ".###."],
        'P': ["####.", "#...#", "####.", "#....", "#...."],
        'Q': [".###.", "#...#", "#.#.#", "#..#.", ".##.#"],
        'R': ["####.", "#...#", "####.", "#..#.", "#...#"],
        'S': [".####", "#....", ".###.", "....#", "####."],
        'T': ["#####", "..#..", "..#..", "..#..", "..#.."],
        'U': ["#...#", "#...#", "#...#", "#...#", ".###."],
        'V': ["#...#", "#...#", "#...#", ".#.#.", "..#.."],
        'W': ["#...#", "#...#", "#.#.#", "##.##", "#...#"],
        'X': ["#...#", ".#.#.", "..#..", ".#.#.", "#...#"],
        'Y': ["#...#", ".#.#.", "..#..", "..#..", "..#.."],
        'Z': ["#####", "...#.", "..#..", ".#...", "#####"],
        '0': [".###.", "#...#", "#.#.#", "#...#", ".###."],
        '1': ["..#..", ".##..", "..#..", "..#..", "#####"],
        '2': [".###.", "#...#", "...#.", "..#..", "#####"],
        '3': ["####.", "....#", "..##.", "....#", "####."],
        '4': ["#..#.", "#..#.", "#####", "...#.", "...#."],
        '5': ["#####", "#....", "####.", "....#", "####."],
        '6': [".###.", "#....", "####.", "#...#", ".###."],
        '7': ["#####", "....#", "...#.", "..#..", "..#.."],
        '8': [".###.", "#...#", ".###.", "#...#", ".###."],
        '9': [".###.", "#...#", ".####", "....#", ".###."],
        ' ': [".....", ".....", ".....", ".....", "....."],
        '!': ["..#..", "..#..", "..#..", ".....", "..#.."],
        '?': [".###.", "#...#", "..##.", ".....", "..#.."],
        '.': [".....", ".....", ".....", ".....", "..#.."],
        ',': [".....", ".....", ".....", "..#..", ".#..."],
        "'": ["..#..", "..#..", ".....", ".....", "....."],
        '-': [".....", ".....", "#####", ".....", "....."],
        ':': [".....", "..#..", ".....", "..#..", "....."],
    }
    ASCII_MAX_LEN = 10

    @classmethod
    def _glyph(cls, ch):
        return cls._GLYPHS.get(ch.upper(), cls._GLYPHS[' '])

    @classmethod
    def _clip(cls, text, limit):
        text = text.strip()
        return text[:limit] if len(text) > limit else text

    ASCII_FONTS = ("block", "banner", "small", "shadow", "digital", "bubble", "slant", "gothic")

    @classmethod
    def render_ascii(cls, font, text):
        text = cls._clip(text, cls.ASCII_MAX_LEN)
        if not text: return []
        font = font.lower()
        if font == "block": return cls._ascii_fill(text, "█")
        if font == "banner": return cls._ascii_banner(text)
        if font == "small": return cls._ascii_small(text)
        if font == "shadow": return cls._ascii_shadow(text)
        if font == "digital": return cls._ascii_digital(text)
        if font == "bubble": return cls._ascii_fill(text, "o")
        if font == "slant": return cls._ascii_slant(text)
        if font == "gothic": return cls._ascii_fill(text, "▓")
        return None

    @classmethod
    def _ascii_fill(cls, text, fillchar):
        rows = ["", "", "", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            for r in range(5):
                rows[r] += g[r].replace("#", fillchar).replace(".", " ") + " "
        return [row.rstrip() for row in rows]

    @classmethod
    def _ascii_banner(cls, text):
        rows = cls._ascii_fill(text, "█")
        width = max(len(r) for r in rows) if rows else 0
        border = "=" * (width + 4)
        out = [border]
        for r in rows:
            out.append("| " + r.ljust(width) + " |")
        out.append(border)
        return out

    @classmethod
    def _ascii_small(cls, text):
        rows = ["", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            for i, r in enumerate((0, 2, 4)):
                rows[i] += g[r].replace("#", "▪").replace(".", " ") + " "
        return [row.rstrip() for row in rows]

    @classmethod
    def _ascii_shadow(cls, text):
        rows = ["", "", "", "", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            width = len(g[0])
            cell = [[" "] * (width + 1) for _ in range(6)]
            for r in range(5):
                for c in range(width):
                    if g[r][c] == "#":
                        cell[r + 1][c + 1] = "░"
            for r in range(5):
                for c in range(width):
                    if g[r][c] == "#":
                        cell[r][c] = "█"
            for r in range(6):
                rows[r] += "".join(cell[r]) + " "
        return [row.rstrip() for row in rows]

    @classmethod
    def _ascii_digital(cls, text):
        rows = ["", "", "", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            for r in range(5):
                fill = "█" if r % 2 == 0 else "▄"
                rows[r] += g[r].replace("#", fill).replace(".", " ") + " "
        return [row.rstrip() for row in rows]

    @classmethod
    def _ascii_slant(cls, text):
        rows = ["", "", "", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            for r in range(5):
                rows[r] += g[r].replace("#", "█").replace(".", " ") + " "
        out = []
        for r in range(5):
            pad = " " * (4 - r)
            out.append(pad + rows[r])
        return out

    _SMALLCAPS = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ',
        'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
        'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ',
        'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    }
    _UPSIDE = {
        'a': 'ɐ', 'b': 'q', 'c': 'ɔ', 'd': 'p', 'e': 'ǝ', 'f': 'ɟ', 'g': 'ƃ',
        'h': 'ɥ', 'i': 'ı', 'j': 'ɾ', 'k': 'ʞ', 'l': 'l', 'm': 'ɯ', 'n': 'u',
        'o': 'o', 'p': 'd', 'q': 'b', 'r': 'ɹ', 's': 's', 't': 'ʇ', 'u': 'n',
        'v': 'ʌ', 'w': 'ʍ', 'x': 'x', 'y': 'ʎ', 'z': 'z',
        '1': 'Ɩ', '2': 'ᄅ', '3': 'Ɛ', '4': 'ㄣ', '5': 'ϛ', '6': '9', '7': 'ㄥ',
        '8': '8', '9': '6', '0': '0', '.': '˙', ',': "'", "'": ',', '?': '¿',
        '!': '¡',
    }
    _MIRROR = {'b': 'd', 'd': 'b', 'p': 'q', 'q': 'p', 's': 'ƨ', 'S': 'Ƨ',
               'z': 'ƹ', 'Z': 'Ƹ', 'e': 'ɘ', 'E': 'Ǝ', '3': 'Ɛ', 'k': 'ʞ'}

    @staticmethod
    def _map_range(offset_upper, offset_lower, offset_digit=None, exceptions=None):
        exceptions = exceptions or {}
        mapping = {}
        for i in range(26):
            mapping[chr(ord('A') + i)] = exceptions.get(chr(ord('A') + i), chr(offset_upper + i))
            mapping[chr(ord('a') + i)] = exceptions.get(chr(ord('a') + i), chr(offset_lower + i))
        if offset_digit is not None:
            for i in range(10):
                mapping[chr(ord('0') + i)] = chr(offset_digit + i)
        return mapping

    _BOLD = None
    _ITALIC = None
    _SCRIPT = None
    _GOTHIC_MAP = None
    _MONO = None

    @classmethod
    def _init_math_fonts(cls):
        if cls._BOLD is not None: return
        cls._BOLD = cls._map_range(0x1D400, 0x1D41A, 0x1D7CE)
        cls._ITALIC = cls._map_range(0x1D434, 0x1D44E, exceptions={'h': 'ℎ'})
        cls._SCRIPT = cls._map_range(0x1D49C, 0x1D4B6, exceptions={'B': 'ℬ', 'E': 'ℰ', 'F': 'ℱ', 'H': 'ℋ', 'I': 'ℐ','L': 'ℒ', 'M': 'ℳ', 'R': 'ℛ','e': 'ℯ', 'g': 'ℊ', 'o': 'ℴ'})
        cls._GOTHIC_MAP = cls._map_range(0x1D504, 0x1D51E, exceptions={'C': 'ℭ', 'H': 'ℌ', 'I': 'ℑ', 'R': 'ℜ', 'Z': 'ℨ'})
        cls._MONO = cls._map_range(0x1D670, 0x1D68A, 0x1D7F6)

    _CIRCLED = {}
    for _i in range(26):
        _CIRCLED[chr(ord('A') + _i)] = chr(0x24B6 + _i)
        _CIRCLED[chr(ord('a') + _i)] = chr(0x24D0 + _i)
    _CIRCLED['0'] = '⓪'
    for _i in range(1, 10):
        _CIRCLED[str(_i)] = chr(0x2460 + _i - 1)

    _SQUARED = {}
    for _i in range(26):
        _SQUARED[chr(ord('A') + _i)] = chr(0x1F130 + _i)
        _SQUARED[chr(ord('a') + _i)] = chr(0x1F130 + _i)

    FONT_STYLES = ("smallcaps", "bubble", "circled", "squared", "wide", "monospace", "gothic", "bold", "italic", "script", "upside", "mirror")
    FONT_MAX_LEN = 200

    @classmethod
    def render_font(cls, style, text):
        cls._init_math_fonts()
        text = cls._clip(text, cls.FONT_MAX_LEN)
        style = style.lower()
        if style == "smallcaps": return "".join(cls._SMALLCAPS.get(c.lower(), c) for c in text)
        if style in ("bubble", "circled"): return "".join(cls._CIRCLED.get(c, c) for c in text)
        if style == "squared": return "".join(cls._SQUARED.get(c, c) for c in text)
        if style == "wide": return "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in text)
        if style == "monospace": return "".join(cls._MONO.get(c, c) for c in text)
        if style == "gothic": return "".join(cls._GOTHIC_MAP.get(c, c) for c in text)
        if style == "bold": return "".join(cls._BOLD.get(c, c) for c in text)
        if style == "italic": return "".join(cls._ITALIC.get(c, c) for c in text)
        if style == "script": return "".join(cls._SCRIPT.get(c, c) for c in text)
        if style == "upside": return "".join(cls._UPSIDE.get(c.lower(), c) for c in text)[::-1]
        if style == "mirror": return "".join(cls._MIRROR.get(c, c) for c in text)[::-1]
        return None

    _COMBINING_UP = ["\u030d", "\u030e", "\u0304", "\u0305", "\u033f", "\u0311", "\u0306", "\u0310", "\u0352", "\u0357"]
    _COMBINING_MID = ["\u0315", "\u031b", "\u0340", "\u0341", "\u0358", "\u0321", "\u0322", "\u0327", "\u0328"]
    _COMBINING_DOWN = ["\u0316", "\u0317", "\u0318", "\u0319", "\u031c", "\u031d", "\u031e", "\u031f", "\u0320", "\u0324"]

    @classmethod
    def glitch(cls, text, max_marks_per_char=2, max_len=120):
        text = cls._clip(text, max_len)
        out = []
        pool = cls._COMBINING_UP + cls._COMBINING_MID + cls._COMBINING_DOWN
        for ch in text:
            out.append(ch)
            if ch.isspace(): continue
            n = random.randint(0, max_marks_per_char)
            out.extend(random.choice(pool) for _ in range(n))
        return "".join(out)

    @classmethod
    def zalgo(cls, text, intensity="medium", max_len=60):
        text = cls._clip(text, max_len)
        levels = {"low": (1, 2), "medium": (3, 5), "high": (6, 10)}
        lo, hi = levels.get(intensity.lower(), levels["medium"])
        out = []
        for ch in text:
            out.append(ch)
            if ch.isspace(): continue
            n = random.randint(lo, hi)
            for _ in range(n):
                pool = random.choice([cls._COMBINING_UP, cls._COMBINING_MID, cls._COMBINING_DOWN])
                out.append(random.choice(pool))
        return "".join(out)[:500]

    BOX_STYLES = {
        "single": ("┌", "┐", "└", "┘", "─", "│"),
        "double": ("╔", "╗", "╚", "╝", "═", "║"),
        "rounded": ("╭", "╮", "╰", "╯", "─", "│"),
        "heavy": ("┏", "┓", "┗", "┛", "━", "┃"),
    }

    @classmethod
    def box(cls, text, style="single"):
        style = style.lower()
        if style not in cls.BOX_STYLES: return None
        text = cls._clip(text, 60)
        tl, tr, bl, br, h, v = cls.BOX_STYLES[style]
        inner = f" {text} "
        return [f"{tl}{h * len(inner)}{tr}", f"{v}{inner}{v}", f"{bl}{h * len(inner)}{br}"]

    @classmethod
    def wave(cls, text, height=3, max_len=24):
        text = cls._clip(text, max_len)
        if not text: return []
        rows = [[" "] * len(text) for _ in range(height)]
        mid = (height - 1) / 2
        for i, ch in enumerate(text):
            offset = math.sin(i * 0.9)
            row = max(0, min(height - 1, round(mid + offset * mid)))
            rows[row][i] = ch
        return ["".join(r) for r in rows]

    _RAINBOW = ["04", "07", "08", "09", "11", "12", "13"]
    _GRADIENT = ["04", "05", "07", "08", "09", "03", "10", "11", "12", "02", "06", "13"]

    @classmethod
    def color_cycle(cls, text, palette):
        out = ""
        for i, ch in enumerate(text):
            if ch.isspace(): out += ch
            else: out += f"\x03{palette[i % len(palette)]}{ch}"
        return out + "\x03"

    @classmethod
    def gradient(cls, text):
        return cls.color_cycle(cls._clip(text, 200), cls._GRADIENT)

    @classmethod
    def rainbow2(cls, text):
        text = cls._clip(text, 200)
        out = ""
        palette = cls._RAINBOW
        for i, ch in enumerate(text):
            if ch.isspace(): out += ch
            else: out += f"\x03{palette[(i // 2) % len(palette)]}{ch}"
        return out + "\x03"

    @classmethod
    def matrix(cls, text, max_len=20):
        text = cls._clip(text, max_len).upper()
        if not text: return []
        width = max(len(text) * 2, 10)
        noise = ["".join(random.choice("01") + " " for _ in range(width // 2)).rstrip() for _ in range(3)]
        return noise + [" ".join(text)]

    @classmethod
    def fire(cls, text, max_len=20):
        text = cls._clip(text, max_len)
        if not text: return []
        width = len(text) + 4
        top = "".join(random.choice("*'^") for _ in range(width))
        mid = f"(( {text.upper()} ))"
        bottom = "".join(random.choice(".,_") for _ in range(width))
        return [top, mid, bottom]

    @classmethod
    def neon(cls, text, max_len=40):
        text = cls._clip(text, max_len)
        glow = cls.color_cycle(text.upper(), ["13", "06", "02", "11"])
        return f"\x0313▓▒░\x03 {glow} \x0313░▒▓\x03"

    @classmethod
    def terminal_frames(cls, text, max_len=40):
        text = cls._clip(text, max_len)
        return ["> initializing...", "> loading " + "█" * 10, "> decrypting...", "> ACCESS GRANTED", f"> {text}"]

    EXPLODE_MAX_LEN = 12
    TYPE_MAX_LEN = 20
    LOADING_MAX_LEN = 40

    @classmethod
    def typewriter_frames(cls, text):
        text = cls._clip(text, cls.TYPE_MAX_LEN)
        return [text[:i] for i in range(1, len(text) + 1)]

    @classmethod
    def loading_frames(cls, label):
        label = cls._clip(label, cls.LOADING_MAX_LEN)
        frames = []
        for pct in range(0, 101, 20):
            filled = pct // 10
            bar = "█" * filled + "░" * (10 - filled)
            frames.append(f"{label}: [{bar}] {pct}%")
        return frames

    @classmethod
    def progress_bar(cls, pct, label=None):
        pct = max(0, min(100, pct))
        filled = round(pct / 100 * 20)
        bar = "█" * filled + "░" * (20 - filled)
        return f"{label + ': ' if label else ''}[{bar}] {pct}%"

    @classmethod
    def transform(cls, style, text):
        style = style.lower()
        if style in cls.FONT_STYLES: return cls.render_font(style, text)
        if style == "glitch": return cls.glitch(text)
        if style.startswith("zalgo"):
            parts = style.split("-", 1)
            intensity = parts[1] if len(parts) > 1 else "medium"
            return cls.zalgo(text, intensity)
        return None

    TRANSFORM_STYLES = FONT_STYLES + ("glitch", "zalgo-low", "zalgo-medium", "zalgo-high")


# ============================================================
# BOT 1: CHIEFOPER (UPDATED DELAY)
# ============================================================
class ChiefOper(IRCBot):
    def __init__(self):
        # Increased send_delay slightly for ChiefOper as it does heavy text work
        super().__init__("ChiefOper", f"ChiefOper Official (Operator: {DEFAULT_ADMIN})", send_delay=0.6)
        self.data_file = "chief_data.json"
        self.lock = threading.Lock()
        self.user_data = self.load_data()

    def load_data(self):
        with self.lock:
            if os.path.exists(self.data_file):
                try:
                    with open(self.data_file, 'r') as f:
                        data = json.load(f)
                        data.setdefault("bios", {}); data.setdefault("points", {}); data.setdefault("notes", {})
                        return data
                except: return {"bios": {}, "points": {}, "notes": {}}
            return {"bios": {}, "points": {}, "notes": {}}

    def save_data(self):
        with self.lock:
            try:
                with open(self.data_file, 'w') as f:
                    json.dump(self.user_data, f, indent=2)
            except Exception as e: print(f"Save error: {e}")

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 2: return

        if parts[1] == "JOIN":
            sender_nick = parts[0].split('!')[0].lstrip(':')
            target_chan = parts[2].lstrip(':') if len(parts) > 2 else ""
            chan_lower = target_chan.lower()
            if sender_nick != self.nickname:
                if chan_lower == "#chatwithworld":
                    self.privmsg(target_chan, f"👋 Welcome {sender_nick} to #ChatWithWorld! Rules: .helpcww | Commands: !usercmd-")
                elif chan_lower == "#games":
                    self.privmsg(target_chan, f"🎮 Hey {sender_nick}! Welcome to #Games.")

        if parts[1] == "PRIVMSG" and len(parts) >= 4:
            sender_prefix = parts[0]
            sender_nick = sender_prefix.split('!')[0].lstrip(':')
            target = parts[2]
            message = " ".join(parts[3:])[1:] if parts[3].startswith(':') else " ".join(parts[3:])

            if target == self.nickname: target = sender_nick

            if message == "!admison":
                self.admin = sender_nick
                self.privmsg(target, f"Admin successfully claimed by: {self.admin}")
                return

            if self.only_admin_mode and not self.is_admin(sender_prefix): return

            if self.is_admin(sender_prefix):
                if message == "!onlyadm-":
                    self.only_admin_mode = not self.only_admin_mode
                    self.privmsg(target, f"Admin Lock: {self.only_admin_mode}")
                elif message.startswith(".pm "):
                    args = message.split(maxsplit=2)
                    if len(args) > 2: self.privmsg(args[1], args[2])
                elif message == "!functionalmodeon": self.functional_mode = True; self.privmsg(target, "Func ON")
                elif message == "!functionalmodeoff": self.functional_mode = False; self.privmsg(target, "Func OFF")
                elif message == "!animson": self.animations_enabled = True; self.privmsg(target, "Anims ON")
                elif message == "!animsoff": self.animations_enabled = False; self.privmsg(target, "Anims OFF")

            if message == ".helpcww":
                for r in ["1. Respect.", "2. Use #Games.", "3. Help @ #CWWHelp.", "4. !usercmd-"]: self.privmsg(target, r)
            elif message in ["!usercmd", "!usercmd-"]:
                self.privmsg(target, "Actions: !slap !hug !cookie !greet !roast | Fun: !joke !fact !profile")
                self.privmsg(target, "Stylizers: !mock !vapor !flip !reverse !rot13 !binary !morse")
                self.privmsg(target, "Formatting: !rainbow !bold !underline !spoiler !calc !unit !timezones")
                self.privmsg(target, "TextLab: !ascii !font !glitch !zalgo !box !wave !gradient !type !loading !matrix !fire !neon !terminal")

            if self.functional_mode:
                self.process_user_commands(sender_nick, target, message)

    def process_user_commands(self, sender, target, msg):
        cmd_parts = msg.split()
        if not cmd_parts: return
        cmd = cmd_parts[0].lower(); args = cmd_parts[1:]; text_arg = " ".join(args)

        actions = {"!slap": "slaps {0}!", "!hug": "hugs {0}!", "!cookie": "gives {0} a cookie!", "!roast": "roasts {0} hard!"}
        if cmd in actions:
            victim = args[0] if args else sender
            self.send(f"PRIVMSG {target} :\x01ACTION {actions[cmd].format(victim)}\x01")

        elif cmd == "!joke": self.privmsg(target, random.choice(["Joke 1", "Joke 2", "Joke 3"]))
        elif cmd == "!fact": self.privmsg(target, random.choice(["Fact 1", "Fact 2", "Fact 3"]))
        elif cmd == "!mock": self.privmsg(target, "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text_arg)))
        elif cmd == "!vapor": self.privmsg(target, "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in text_arg))
        elif cmd == "!reverse": self.privmsg(target, text_arg[::-1])
        elif cmd == "!rainbow": self.privmsg(target, TextLab.color_cycle(text_arg, TextLab._RAINBOW))
        elif cmd == "!calc":
            try: self.privmsg(target, f"Result: {eval(text_arg, {'__builtins__': None}, {'math': math})}")
            except: self.privmsg(target, "Math Error")

        # TextLab processing
        self.process_textlab_commands(sender, target, cmd, args, text_arg)

    def process_textlab_commands(self, sender, target, cmd, args, text_arg):
        if cmd == "!ascii":
            font = args[0] if args and args[0] in TextLab.ASCII_FONTS else "block"
            content = " ".join(args[1:]) if font != "block" else text_arg
            lines = TextLab.render_ascii(font, content)
            if lines:
                for l in lines: self.privmsg(target, l)
        elif cmd == "!font":
            if len(args) >= 2: self.privmsg(target, TextLab.render_font(args[0], " ".join(args[1:])))
        elif cmd == "!glitch": self.privmsg(target, TextLab.glitch(text_arg))
        elif cmd == "!zalgo": self.privmsg(target, TextLab.zalgo(text_arg))
        elif cmd == "!box":
            lines = TextLab.box(text_arg)
            if lines:
                for l in lines: self.privmsg(target, l)
        elif cmd == "!wave":
            for l in TextLab.wave(text_arg): self.privmsg(target, l)
        elif cmd == "!gradient": self.privmsg(target, TextLab.gradient(text_arg))
        elif cmd == "!type":
            if not self.animations_enabled: self.privmsg(target, text_arg); return
            def run():
                for f in TextLab.typewriter_frames(text_arg): self.privmsg(target, f)
            threading.Thread(target=run, daemon=True).start()
        elif cmd == "!loading":
            if not self.animations_enabled: self.privmsg(target, "[██████████] 100%"); return
            def run():
                for f in TextLab.loading_frames(text_arg): self.privmsg(target, f)
            threading.Thread(target=run, daemon=True).start()
        elif cmd == "!matrix":
            for l in TextLab.matrix(text_arg): self.privmsg(target, l)
        elif cmd == "!fire":
            for l in TextLab.fire(text_arg): self.privmsg(target, l)
        elif cmd == "!neon": self.privmsg(target, TextLab.neon(text_arg))
        elif cmd == "!terminal":
            if not self.animations_enabled: self.privmsg(target, "> ACCESS GRANTED"); return
            def run():
                for f in TextLab.terminal_frames(text_arg): self.privmsg(target, f)
            threading.Thread(target=run, daemon=True).start()

# ============================================================
# BOT 2: MRLOGGER
# ============================================================
class MrLogger(IRCBot):
    def __init__(self):
        super().__init__("MrLogger", f"Logger (Operator: {DEFAULT_ADMIN})")
        self.log_file = "Logger_data.json"
        self.lock = threading.Lock()
        self.data = self.load_data()

    def load_data(self):
        with self.lock:
            if os.path.exists(self.log_file):
                try:
                    with open(self.log_file, 'r') as f: return json.load(f)
                except: return {"logs": {}, "mailbox": {}}
            return {"logs": {}, "mailbox": {}}

    def save_data(self):
        with self.lock:
            try:
                with open(self.log_file, 'w') as f: json.dump(self.data, f)
            except: pass

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 2: return
        if parts[1] == "PRIVMSG" and len(parts) >= 4:
            sender = parts[0].split('!')[0].lstrip(':')
            target = parts[2]; msg = " ".join(parts[3:])[1:]
            if target.startswith("#"):
                self.data["logs"][sender.lower()] = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "phrase": msg}
                self.save_data()

# ============================================================
# BOT 3: YOUTUBESEARCH
# ============================================================
class YoutubeSearch(IRCBot):
    def __init__(self):
        super().__init__("YoutubeSearch", "YouTube Bot", channels=[YT_CHANNEL], send_delay=0.1)
        self.last_search = 0.0
        self.search_lock = threading.Lock()

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 4 or parts[1] != "PRIVMSG": return
        msg = " ".join(parts[3:])[1:]
        if msg.lower().startswith(".yt "):
            query = msg[4:].strip()
            if query:
                with self.search_lock:
                    if time.monotonic() - self.last_search < 10: return
                    self.last_search = time.monotonic()
                self.privmsg(parts[2], f"Searching for: {query}...")

# ============================================================
# EXECUTION
# ============================================================
_bots_started = False
_bots_lock = threading.Lock()

def start_bots():
    global _bots_started
    with _bots_lock:
        if _bots_started: return
        _bots_started = True
        threading.Thread(target=ChiefOper().run_forever, daemon=True).start()
        time.sleep(5)
        threading.Thread(target=MrLogger().run_forever, daemon=True).start()
        time.sleep(5)
        threading.Thread(target=YoutubeSearch().run_forever, daemon=True).start()

start_bots()

if __name__ == "__main__":
    run_http_server()
