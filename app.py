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

import storage

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
CHANNELS = ["#ChatWithWorld", "#Games", "#CWWHelp", "#global"]
YT_CHANNEL = os.environ.get("YT_CHANNEL", "#ChatWithWorld")
DEFAULT_ADMIN = "Antonio"

# Optional NickServ password. This deployment runs UNREGISTERED bots, so
# leave NICKSERV_PASS unset in the environment and the IDENTIFY step is
# skipped automatically (no password is ever hardcoded here).
NICKSERV_PASS = os.environ.get("NICKSERV_PASS", "").strip()

COMMAND_COOLDOWN = 10  # seconds between .yt searches

# ---------------- MagicalFairy star game settings ----------------
# All game tuning lives here. For quick testing you can shorten these via
# environment variables (and remove them again for production).
FAIRY_CHANNEL = "#global"                                              # game runs ONLY here
FAIRY_MIN_MINUTES = float(os.environ.get("FAIRY_MIN_MINUTES", "25"))   # min gap between stars
FAIRY_MAX_MINUTES = float(os.environ.get("FAIRY_MAX_MINUTES", "35"))   # max gap between stars
FAIRY_CATCH_SECONDS = float(os.environ.get("FAIRY_CATCH_SECONDS", "30"))  # catch window
FAIRY_UNCATCHABLE_CHANCE = float(os.environ.get("FAIRY_UNCATCHABLE_CHANCE", "0.20"))  # 10%
FAIRY_LEADERBOARD_SIZE = 10
FAIRY_REWARD = 1                      # stars per successful catch
FAIRY_NOTICE_COOLDOWN = 10            # seconds between "no star"/"protected star" replies (anti-spam)
FAIRY_CMD_COOLDOWN = 5                # seconds per user between !top / !mystars
FAIRY_STAR = "🌟"                      # the official reward emoji

# ============================================================
# WEB SERVER (for Render health checks / keep-alive)
# ============================================================
app = Flask(__name__)


@app.route('/')
def health_check():
    return "Bots are active and running 24/7.", 200


def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ============================================================
# BASE IRC BOT ENGINE
# ------------------------------------------------------------
# Hardened connection behaviour shared by all bots:
#   - SSL/TLS socket with SNI (ssl.create_default_context)
#   - IRCv3 CAP LS 302 negotiation, closed with CAP END
#   - NICK / USER registration
#   - MODE <nick> +B sent right after numeric 001 (declares bot status)
#   - Optional NickServ IDENTIFY (only if NICKSERV_PASS is actually set)
#   - Correct PING/PONG handling (payload preserved exactly)
#   - 300s socket timeout to detect dead/silent connections
#   - Auto-reconnect loop with exponential backoff (resets after a
#     successful registration so a long-lived bot never gets stuck on a
#     huge delay after one bad connection)
# ============================================================
class IRCBot:
    def __init__(self, nickname, realname, channels=None, send_delay=0.3):
        self.nickname = nickname
        self.realname = realname
        self.admin = DEFAULT_ADMIN
        self.channels = channels if channels is not None else list(CHANNELS)
        self.send_delay = send_delay  # per-message throttle to avoid Excess Flood
        self.only_admin_mode = False
        self.functional_mode = True
        self.animations_enabled = True
        self.running = False
        self.irc = None
        self._registered = False
        self._cap_ended = False

    # ---------------- low level I/O ----------------
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
        if not msg:
            return
        self.send(f"PRIVMSG {target} :{msg}")
        if self.send_delay:
            time.sleep(self.send_delay)

    def is_admin(self, prefix):
        nick = prefix.split('!')[0].lstrip(':')
        return nick.lower() == self.admin.lower()

    # ---------------- connection lifecycle ----------------
    def _create_socket(self):
        raw_socket = socket.create_connection((SERVER, PORT), timeout=30)
        context = ssl.create_default_context()
        # Some IRC networks (hybridirc included) drop connections abruptly
        # instead of sending a proper TLS close_notify. Without this option,
        # OpenSSL 3.x/Python 3.11+ raises SSLEOFError ("TLS connection was
        # non-properly terminated") for what is really just a normal
        # disconnect. This makes recv() return b"" instead, which the loop
        # below already treats as a clean disconnect -> triggers reconnect.
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
        self.irc.settimeout(300)  # detect dead/silent connections
        print(f"[{self.nickname}] TLS connection established.")

        # IRCv3 capability negotiation begins immediately upon socket creation
        self.send("CAP LS 302")
        self.send(f"NICK {self.nickname}")
        self.send(f"USER {self.nickname} 0 * :{self.realname}")

        # Check for activity (re-added to maintain structure)
        threading.Thread(target=self.idle_prevention, daemon=True).start()

        buffer = ""
        while self.running:
            try:
                data = self.irc.recv(4096)
            except socket.timeout:
                print(f"[{self.nickname}] No data in 300s - assuming dead connection.")
                break
            except Exception as e:
                print(f"[{self.nickname}] recv() error: {type(e).__name__}: {e}")
                break

            if not data:
                print(f"[{self.nickname}] Connection closed by remote host.")
                break

            buffer += data.decode("utf-8", errors="replace")
            while "\r\n" in buffer:
                line, buffer = buffer.split("\r\n", 1)
                if not line:
                    continue
                print(f"[{self.nickname}] <<< {line}")
                self._handle_protocol_line(line)
                try:
                    self.handle_message(line)
                except Exception as err:
                    print(f"[{self.nickname}] Exception in handle_message: {err}")

        self.running = False
        try:
            if self.irc:
                self.irc.close()
        except Exception:
            pass

    def _handle_protocol_line(self, line):
        parts = line.split(' ')

        # PING keepalive - reply with the exact same payload
        if line.startswith("PING"):
            payload = line[5:] if len(line) > 5 else ""
            self.send(f"PONG {payload}")
            return

        if len(parts) < 2:
            return

        # Close capability negotiation as soon as the server answers CAP LS
        if parts[1] == "CAP" and not self._cap_ended:
            self.send("CAP END")
            self._cap_ended = True
            return

        # Welcome numeric -> registration complete
        if parts[1] == "001" and not self._registered:
            self._registered = True
            print(f"[{self.nickname}] Registered with IRC server.")
            # Declare the client as a bot
            self.send(f"MODE {self.nickname} +B")
            # Optional NickServ auth - only fires if a password is configured.
            # This deployment is unregistered, so NICKSERV_PASS stays empty
            # and this whole block is skipped.
            if NICKSERV_PASS:
                self.send(f"PRIVMSG NickServ :IDENTIFY {NICKSERV_PASS}")
                time.sleep(1)
            for chan in self.channels:
                self.send(f"JOIN {chan}")
                time.sleep(0.5)
            return

    def handle_message(self, line):
        """Override in subclasses."""
        pass

    def run_forever(self):
        """Auto-reconnect loop with exponential backoff."""
        backoff = 5
        max_backoff = 300
        while True:
            try:
                self.connect_once()
            except Exception as e:
                print(f"[{self.nickname}] Fatal error in connection loop: {e}")
            was_registered = self._registered
            self.running = False
            self._registered = False
            wait = 5 if was_registered else backoff
            print(f"[{self.nickname}] Disconnected. Reconnecting in {wait}s...")
            time.sleep(wait)
            backoff = 5 if was_registered else min(backoff * 2, max_backoff)


# ============================================================
# TEXT LAB - local, offline text-effect engine used by ChiefOper
# No network calls, no downloaded fonts - everything below is
# generated/mapped locally.
# ============================================================
class TextLab:

    # ---- 5x5 dot-matrix glyphs used as the base for all ASCII-art fonts ----
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

    # ---- ASCII / FIGlet-style fonts (§1) ----
    ASCII_FONTS = ("block", "banner", "small", "shadow", "digital", "bubble", "slant", "gothic")

    @classmethod
    def render_ascii(cls, font, text):
        text = cls._clip(text, cls.ASCII_MAX_LEN)
        if not text:
            return []
        font = font.lower()
        if font == "block":
            return cls._ascii_fill(text, "█")
        if font == "banner":
            return cls._ascii_banner(text)
        if font == "small":
            return cls._ascii_small(text)
        if font == "shadow":
            return cls._ascii_shadow(text)
        if font == "digital":
            return cls._ascii_digital(text)
        if font == "bubble":
            return cls._ascii_fill(text, "o")
        if font == "slant":
            return cls._ascii_slant(text)
        if font == "gothic":
            return cls._ascii_fill(text, "▓")
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
                        cell[r + 1][c + 1] = "░"  # shadow, offset down-right
            for r in range(5):
                for c in range(width):
                    if g[r][c] == "#":
                        cell[r][c] = "█"           # main glyph on top
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

    # ---- Unicode font converter (§2) ----
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
        if cls._BOLD is not None:
            return
        cls._BOLD = cls._map_range(0x1D400, 0x1D41A, 0x1D7CE)
        cls._ITALIC = cls._map_range(0x1D434, 0x1D44E, exceptions={'h': 'ℎ'})
        cls._SCRIPT = cls._map_range(
            0x1D49C, 0x1D4B6,
            exceptions={'B': 'ℬ', 'E': 'ℰ', 'F': 'ℱ', 'H': 'ℋ', 'I': 'ℐ',
                        'L': 'ℒ', 'M': 'ℳ', 'R': 'ℛ',
                        'e': 'ℯ', 'g': 'ℊ', 'o': 'ℴ'})
        cls._GOTHIC_MAP = cls._map_range(
            0x1D504, 0x1D51E,
            exceptions={'C': 'ℭ', 'H': 'ℌ', 'I': 'ℑ', 'R': 'ℜ', 'Z': 'ℨ'})
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
        _SQUARED[chr(ord('a') + _i)] = chr(0x1F130 + _i)  # no lowercase squared set exists

    FONT_STYLES = ("smallcaps", "bubble", "circled", "squared", "wide", "monospace",
                   "gothic", "bold", "italic", "script", "upside", "mirror")
    FONT_MAX_LEN = 200

    @classmethod
    def render_font(cls, style, text):
        cls._init_math_fonts()
        text = cls._clip(text, cls.FONT_MAX_LEN)
        style = style.lower()
        if style == "smallcaps":
            return "".join(cls._SMALLCAPS.get(c.lower(), c) for c in text)
        if style in ("bubble", "circled"):
            return "".join(cls._CIRCLED.get(c, c) for c in text)
        if style == "squared":
            return "".join(cls._SQUARED.get(c, c) for c in text)
        if style == "wide":
            return "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in text)
        if style == "monospace":
            return "".join(cls._MONO.get(c, c) for c in text)
        if style == "gothic":
            return "".join(cls._GOTHIC_MAP.get(c, c) for c in text)
        if style == "bold":
            return "".join(cls._BOLD.get(c, c) for c in text)
        if style == "italic":
            return "".join(cls._ITALIC.get(c, c) for c in text)
        if style == "script":
            return "".join(cls._SCRIPT.get(c, c) for c in text)
        if style == "upside":
            return "".join(cls._UPSIDE.get(c.lower(), c) for c in text)[::-1]
        if style == "mirror":
            return "".join(cls._MIRROR.get(c, c) for c in text)[::-1]
        return None

    # ---- Glitch / Zalgo (§3, §4) ----
    _COMBINING_UP = ["\u030d", "\u030e", "\u0304", "\u0305", "\u033f", "\u0311",
                      "\u0306", "\u0310", "\u0352", "\u0357"]
    _COMBINING_MID = ["\u0315", "\u031b", "\u0340", "\u0341", "\u0358", "\u0321",
                       "\u0322", "\u0327", "\u0328"]
    _COMBINING_DOWN = ["\u0316", "\u0317", "\u0318", "\u0319", "\u031c", "\u031d",
                        "\u031e", "\u031f", "\u0320", "\u0324"]

    @classmethod
    def glitch(cls, text, max_marks_per_char=2, max_len=120):
        text = cls._clip(text, max_len)
        out = []
        pool = cls._COMBINING_UP + cls._COMBINING_MID + cls._COMBINING_DOWN
        for ch in text:
            out.append(ch)
            if ch.isspace():
                continue
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
            if ch.isspace():
                continue
            n = random.randint(lo, hi)
            for _ in range(n):
                pool = random.choice([cls._COMBINING_UP, cls._COMBINING_MID, cls._COMBINING_DOWN])
                out.append(random.choice(pool))
        result = "".join(out)
        return result[:500]  # hard safety cap on final length

    # ---- Text box (§6) ----
    BOX_STYLES = {
        "single": ("┌", "┐", "└", "┘", "─", "│"),
        "double": ("╔", "╗", "╚", "╝", "═", "║"),
        "rounded": ("╭", "╮", "╰", "╯", "─", "│"),
        "heavy": ("┏", "┓", "┗", "┛", "━", "┃"),
    }

    @classmethod
    def box(cls, text, style="single"):
        style = style.lower()
        if style not in cls.BOX_STYLES:
            return None
        text = cls._clip(text, 60)
        tl, tr, bl, br, h, v = cls.BOX_STYLES[style]
        inner = f" {text} "
        top = f"{tl}{h * len(inner)}{tr}"
        mid = f"{v}{inner}{v}"
        bottom = f"{bl}{h * len(inner)}{br}"
        return [top, mid, bottom]

    # ---- Wave text (§7) ----
    @classmethod
    def wave(cls, text, height=3, max_len=24):
        text = cls._clip(text, max_len)
        if not text:
            return []
        rows = [[" "] * len(text) for _ in range(height)]
        mid = (height - 1) / 2
        for i, ch in enumerate(text):
            offset = math.sin(i * 0.9)
            row = round(mid + offset * mid)
            row = max(0, min(height - 1, row))
            rows[row][i] = ch
        return ["".join(r) for r in rows]

    # ---- Gradient / rainbow colours (§8) ----
    _RAINBOW = ["04", "07", "08", "09", "11", "12", "13"]
    _GRADIENT = ["04", "05", "07", "08", "09", "03", "10", "11", "12", "02", "06", "13"]

    @classmethod
    def color_cycle(cls, text, palette):
        out = ""
        for i, ch in enumerate(text):
            if ch.isspace():
                out += ch
            else:
                out += f"\x03{palette[i % len(palette)]}{ch}"
        return out + "\x03"

    @classmethod
    def gradient(cls, text):
        return cls.color_cycle(cls._clip(text, 200), cls._GRADIENT)

    @classmethod
    def rainbow2(cls, text):
        # different pattern from !rainbow: colours shift every 2 characters
        text = cls._clip(text, 200)
        out = ""
        palette = cls._RAINBOW
        for i, ch in enumerate(text):
            if ch.isspace():
                out += ch
            else:
                out += f"\x03{palette[(i // 2) % len(palette)]}{ch}"
        return out + "\x03"

    # ---- Matrix (§12) ----
    @classmethod
    def matrix(cls, text, max_len=20):
        text = cls._clip(text, max_len).upper()
        if not text:
            return []
        width = max(len(text) * 2, 10)
        noise_lines = ["".join(random.choice("01") + " " for _ in range(width // 2)).rstrip()
                       for _ in range(3)]
        spaced = " ".join(text)
        return noise_lines + [spaced]

    # ---- Fire / Neon (§16, §17) ----
    @classmethod
    def fire(cls, text, max_len=20):
        text = cls._clip(text, max_len)
        if not text:
            return []
        width = len(text) + 4
        top = "".join(random.choice("*'^") for _ in range(width))
        mid = f"(( {text.upper()} ))"
        bottom = "".join(random.choice(".,_") for _ in range(width))
        return [top, mid, bottom]

    @classmethod
    def neon(cls, text, max_len=40):
        text = cls._clip(text, max_len)
        palette = ["13", "06", "02", "11"]
        glow = cls.color_cycle(text.upper(), palette)
        return f"\x0313▓▒░\x03 {glow} \x0313░▒▓\x03"

    # ---- Terminal / hacker (§18) ----
    @classmethod
    def terminal_frames(cls, text, max_len=40):
        text = cls._clip(text, max_len)
        return [
            "> initializing...",
            "> loading " + "█" * 10,
            "> decrypting...",
            "> ACCESS GRANTED",
            f"> {text}",
        ]

    # ---- Explode / implode (§13, §14) ----
    EXPLODE_MAX_LEN = 12

    @classmethod
    def explode_lines(cls, text):
        text = cls._clip(text, cls.EXPLODE_MAX_LEN)
        return list(text)

    @classmethod
    def implode_frames(cls, text):
        text = cls._clip(text, cls.EXPLODE_MAX_LEN)
        if not text:
            return []
        frames = []
        for gap in (6, 3, 1, 0):
            frames.append((" " * gap).join(text))
        return frames

    # ---- Scramble (§15) ----
    SCRAMBLE_MAX_LEN = 15

    @classmethod
    def scramble_frames(cls, text):
        text = cls._clip(text, cls.SCRAMBLE_MAX_LEN)
        letters = list(text)
        frames = []
        for _ in range(3):
            shuffled = letters[:]
            random.shuffle(shuffled)
            frames.append("".join(shuffled))
        frames.append(text)
        return frames

    # ---- Typewriter / loading / bar (§9, §10, §11) ----
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
            prefix = f"{label}: " if label else ""
            frames.append(f"{prefix}[{bar}] {pct}%")
        return frames

    @classmethod
    def progress_bar(cls, pct, label=None):
        pct = max(0, min(100, pct))
        filled = round(pct / 100 * 20)
        bar = "█" * filled + "░" * (20 - filled)
        if label:
            return f"{label}: [{bar}] {pct}%"
        return f"[{bar}] {pct}%"

    # ---- Unified transform dispatcher (§19) ----
    @classmethod
    def transform(cls, style, text):
        style = style.lower()
        if style in cls.FONT_STYLES:
            return cls.render_font(style, text)
        if style == "glitch":
            return cls.glitch(text)
        if style.startswith("zalgo"):
            parts = style.split("-", 1)
            intensity = parts[1] if len(parts) > 1 else "medium"
            return cls.zalgo(text, intensity)
        return None

    TRANSFORM_STYLES = FONT_STYLES + ("glitch", "zalgo-low", "zalgo-medium", "zalgo-high")


# ============================================================
# BOT 1: CHIEFOPER
# ============================================================
class ChiefOper(IRCBot):
    def __init__(self):
        super().__init__("ChiefOper", f"ChiefOper Official (Operator: {DEFAULT_ADMIN})")
        self.data_file = "chief_data.json"
        self.lock = threading.Lock()
        self.user_data = self.load_data()

    def load_data(self):
        with self.lock:
            if os.path.exists(self.data_file):
                try:
                    with open(self.data_file, 'r') as f:
                        data = json.load(f)
                        data.setdefault("bios", {})
                        data.setdefault("points", {})
                        data.setdefault("notes", {})
                        return data
                except Exception:
                    return {"bios": {}, "points": {}, "notes": {}}
            return {"bios": {}, "points": {}, "notes": {}}

    def save_data(self):
        with self.lock:
            try:
                with open(self.data_file, 'w') as f:
                    json.dump(self.user_data, f, indent=2)
            except Exception as e:
                print(f"Error saving data: {e}")

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 2:
            return

        if parts[1] == "JOIN":
            sender_nick = parts[0].split('!')[0].lstrip(':')
            target_chan = parts[2].lstrip(':') if len(parts) > 2 else ""
            # IRC channel names are case-insensitive on the wire - the server
            # may echo JOIN back with different casing than CHANNELS uses
            # (e.g. "#chatwithworld" vs "#ChatWithWorld"), so compare lower().
            chan_lower = target_chan.lower()

            if sender_nick != self.nickname:
                if chan_lower == "#chatwithworld":
                    self.privmsg(
                        target_chan,
                        f"👋 Welcome {sender_nick} to #ChatWithWorld! Say .helpcww for the "
                        f"rules or !usercmd- to see everything I can do."
                    )
                elif chan_lower == "#games":
                    self.privmsg(target_chan, f"🎮 Hey {sender_nick}! Welcome to #Games. Have fun!")
                elif chan_lower == "#cwwhelp":
                    self.privmsg(target_chan, f"❓ Welcome {sender_nick} to #CWWHelp! Ask your questions here or use .helpcww.")
                elif chan_lower == "#global":
                    self.privmsg(
                        target_chan,
                        f"👋 Welcome {sender_nick} to #global ! Say .helpglobal for the "
                        f"rules or !usercmd- to see everything I can do."
                    )

        if parts[1] == "PRIVMSG" and len(parts) >= 4:
            sender_prefix = parts[0]
            sender_nick = sender_prefix.split('!')[0].lstrip(':')
            target = parts[2]

            message = " ".join(parts[3:])
            if message.startswith(':'):
                message = message[1:]

            if target == self.nickname:
                target = sender_nick

            if message == "!admison":
                self.admin = sender_nick
                self.privmsg(target, f"Admin successfully claimed by: {self.admin}")
                return

            if self.only_admin_mode and not self.is_admin(sender_prefix):
                return

            if self.is_admin(sender_prefix):
                if message == "!onlyadm-":
                    self.only_admin_mode = not self.only_admin_mode
                    self.privmsg(target, f"Admin-Only Lock: {'ENABLED' if self.only_admin_mode else 'DISABLED'}")
                elif message.startswith(".pm ") or message.startswith(".passmsg "):
                    args = message.split(maxsplit=2)
                    if len(args) > 2:
                        self.privmsg(args[1], args[2])
                elif message == "!functionalmodeon":
                    self.functional_mode = True
                    self.privmsg(target, "Functional Mode [ON]")
                elif message == "!functionalmodeoff":
                    self.functional_mode = False
                    self.privmsg(target, "Functional Mode [OFF]")
                elif message == "!animson":
                    self.animations_enabled = True
                    self.privmsg(target, "TextLab animations [ON]")
                elif message == "!animsoff":
                    self.animations_enabled = False
                    self.privmsg(target, "TextLab animations [OFF] (effects will send a single static line instead)")

            if message == ".helpcww" and target.lower() != "#global":
                rules = [
                    "--- #ChatWithWorld Rules ---",
                    "1. Respect everyone. No harassment or hate speech.",
                    "2. Use #Games channel for bot fun-commands.",
                    "3. Use #CWWHelp for admin support.",
                    "4. Use !usercmd- for all commands.",
                ]
                for r in rules:
                    self.privmsg(target, r)

            elif message == ".helpglobal":
                global_rules = [
                    "--- #global Rules ---",
                    "1. Respect everyone. No harassment or hate speech.",
                    "2. Use the existing #Games channel for bot fun-commands.",
                    "3. Use #globalhelp for admin support.",
                    "4. Use !usercmd- for all commands.",
                ]
                for r in global_rules:
                    self.privmsg(target, r)

            elif message in ["!usercmd", "!usercmd-"]:
                cmds_1 = "Actions: !slap, !hug, !cookie, !greet, !roast, !compliment | Fun: !joke, !fact, !bio, !profile"
                cmds_2 = "Stylizers: !mock, !vapor, !flip, !reverse, !rot13, !morse, !binary, !decode"
                cmds_3 = "Formatting: !rainbow, !bold, !underline, !spoiler"
                cmds_4 = "Utils: !calc, !stats, !unit, !timezones, !urlencode, !urldecode, !note, !read, !reminder <min> <msg>"
                cmds_5 = "Logger/Mail: !seen <nick>, .tell <nick> <msg>"
                cmds_6 = "TextLab: !ascii, !font, !glitch, !zalgo, !mirror, !upside, !box, !wave -- see !texthelp for the full list"
                self.privmsg(target, "--- ChiefOper Commands ---")
                self.privmsg(target, cmds_1)
                self.privmsg(target, cmds_2)
                self.privmsg(target, cmds_3)
                self.privmsg(target, cmds_4)
                self.privmsg(target, cmds_5)
                self.privmsg(target, cmds_6)

            if self.functional_mode:
                self.process_user_commands(sender_nick, target, message)

    # ------------------------------------------------------------
    def process_user_commands(self, sender, target, msg):
        cmd_parts = msg.split()
        if not cmd_parts:
            return
        cmd = cmd_parts[0].lower()
        args = cmd_parts[1:]
        text_arg = " ".join(args)

        actions = {
            "!slap": "slaps {0} with a wet fish!",
            "!hug": "hugs {0} tightly!",
            "!cookie": "hands {0} a giant cookie!",
            "!greet": "says: Hello {0}, welcome!",
            "!roast": "tells {0} their code has more bugs than a tropical rainforest!",
            "!compliment": "tells {0} they look sharp today!",
        }

        if cmd in actions:
            victim = args[0] if args else sender
            self.send(f"PRIVMSG {target} :\x01ACTION {actions[cmd].format(victim)}\x01")

        elif cmd == "!reminder":
            if len(args) >= 2 and args[0].isdigit():
                minutes = int(args[0])
                rem_text = " ".join(args[1:])

                def send_rem():
                    self.privmsg(target, f"🔔 REMINDER for {sender}: {rem_text}")

                threading.Timer(minutes * 60, send_rem).start()
                self.privmsg(target, f"Okay {sender}, I'll remind you in {minutes} minute(s).")

        elif cmd == "!joke":
            jokes = [
                "Why do programmers prefer dark mode? Because light attracts bugs.",
                "A SQL query walks into a bar, walks up to two tables and asks, 'Can I join you?'",
                "There are 10 types of people in the world: those who understand binary, and those who don't.",
            ]
            self.privmsg(target, random.choice(jokes))

        elif cmd == "!fact":
            facts = [
                "Honey is the only food that doesn't spoil.",
                "An octopus has three hearts.",
                "The programming language Python was named after Monty Python.",
            ]
            self.privmsg(target, random.choice(facts))

        elif cmd == "!bio":
            if args:
                self.user_data["bios"][sender] = text_arg
                self.save_data()
                self.privmsg(target, f"Bio saved for {sender}.")

        elif cmd == "!profile":
            user = args[0] if args else sender
            bio = self.user_data["bios"].get(user, "No bio set.")
            pts = self.user_data["points"].get(user, 0)
            self.privmsg(target, f"Profile [{user}]: Rep: {pts} | Bio: {bio}")

        elif cmd == "!mock":
            if text_arg:
                mocked = "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text_arg))
                self.privmsg(target, mocked)

        elif cmd == "!vapor":
            if text_arg:
                vapor = "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in text_arg)
                self.privmsg(target, vapor)

        elif cmd == "!flip":
            if text_arg:
                normal = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!?,."
                flipped = "ɐqɔpǝɟƃɥıɾʞlɯudodbɹsʇnʌʍxʎz∀qƆpƎℲפHIſʞ˥WNOԀQƆS┴∩ΛMX⅄Z0Ɩᄅᙠㄣϛ9ㄥ86¡¿'˙"
                # NOTE: kept identical to the original implementation
                trans_table = str.maketrans(normal, flipped[:len(normal)])
                flipped_text = text_arg.translate(trans_table)[::-1]
                self.privmsg(target, f"(╯°□°)╯︵ {flipped_text}")

        elif cmd == "!reverse":
            if text_arg:
                self.privmsg(target, text_arg[::-1])

        elif cmd == "!rot13":
            if text_arg:
                normal = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                rot = "nopqrstuvwxyzabcdefghijklmNOPQRSTUVWXYZABCDEFGHIJKLM"
                self.privmsg(target, text_arg.translate(str.maketrans(normal, rot)))

        elif cmd == "!binary":
            if text_arg:
                binary_str = " ".join(format(ord(c), '08b') for c in text_arg)
                self.privmsg(target, binary_str)

        elif cmd == "!decode":
            if text_arg:
                try:
                    decoded = "".join(chr(int(b, 2)) for b in args)
                    self.privmsg(target, f"Decoded: {decoded}")
                except Exception:
                    self.privmsg(target, "Invalid binary format. Provide space-separated 8-bit strings.")

        elif cmd == "!morse":
            if text_arg:
                morse_code = {
                    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
                    'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
                    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
                    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
                    'Y': '-.--', 'Z': '--..', '1': '.----', '2': '..---', '3': '...--',
                    '4': '....-', '5': '.....', '6': '-....', '7': '--...', '8': '---..',
                    '9': '----.', '0': '-----', ' ': '/',
                }
                m_str = " ".join(morse_code.get(c.upper(), c) for c in text_arg)
                self.privmsg(target, m_str)

        elif cmd == "!rainbow":
            if text_arg:
                self.privmsg(target, TextLab.color_cycle(TextLab._clip(text_arg, 200), TextLab._RAINBOW))

        elif cmd == "!bold":
            if text_arg:
                self.privmsg(target, f"\x02{text_arg}\x02")

        elif cmd == "!underline":
            if text_arg:
                self.privmsg(target, f"\x1f{text_arg}\x1f")

        elif cmd == "!spoiler":
            if text_arg:
                self.privmsg(target, f"\x031,1{text_arg}\x03")

        elif cmd == "!calc":
            if text_arg:
                allowed = set("0123456789+-*/().^% eEpiPI,")
                if set(text_arg).issubset(allowed):
                    try:
                        clean_math = text_arg.replace('^', '**').replace('pi', 'math.pi').replace('PI', 'math.pi').replace('e', 'math.e')
                        safe_dict = {"math": math, "abs": abs, "round": round, "pow": pow, "sqrt": math.sqrt}
                        res = eval(clean_math, {"__builtins__": None}, safe_dict)
                        self.privmsg(target, f"🧮 Result: {res}")
                    except Exception as e:
                        self.privmsg(target, f"Calculation error: {e}")
                else:
                    self.privmsg(target, "Invalid math expression. Only standard arithmetic allowed.")

        elif cmd == "!stats":
            if text_arg:
                words = text_arg.split()
                vowels = sum(1 for c in text_arg.lower() if c in "aeiou")
                consonants = sum(1 for c in text_arg.lower() if c.isalpha() and c not in "aeiou")
                self.privmsg(target, f"📊 Stats: Characters: {len(text_arg)} | Words: {len(words)} | Vowels: {vowels} | Consonants: {consonants}")

        elif cmd == "!unit":
            if len(args) == 3:
                try:
                    val = float(args[0])
                    u_from, u_to = args[1].lower(), args[2].lower()

                    if u_from in ["c", "celsius"] and u_to in ["f", "fahrenheit"]:
                        res = (val * 9 / 5) + 32
                        self.privmsg(target, f"🌡️ {val}°C = {res:.2f}°F")
                    elif u_from in ["f", "fahrenheit"] and u_to in ["c", "celsius"]:
                        res = (val - 32) * 5 / 9
                        self.privmsg(target, f"🌡️ {val}°F = {res:.2f}°C")
                    elif u_from in ["km", "kilometers"] and u_to in ["mi", "miles"]:
                        self.privmsg(target, f"📏 {val} km = {val * 0.621371:.2f} miles")
                    elif u_from in ["mi", "miles"] and u_to in ["km", "kilometers"]:
                        self.privmsg(target, f"📏 {val} miles = {val * 1.60934:.2f} km")
                    elif u_from in ["kg", "kilograms"] and u_to in ["lbs", "pounds"]:
                        self.privmsg(target, f"⚖️ {val} kg = {val * 2.20462:.2f} lbs")
                    elif u_from in ["lbs", "pounds"] and u_to in ["kg", "kilograms"]:
                        self.privmsg(target, f"⚖️ {val} lbs = {val * 0.453592:.2f} kg")
                    else:
                        self.privmsg(target, "Supported conversions: c<->f, km<->mi, kg<->lbs")
                except ValueError:
                    self.privmsg(target, "Invalid number format for unit conversion.")

        elif cmd == "!timezones":
            now_utc = datetime.now(timezone.utc)
            est = now_utc + timedelta(hours=-5)
            cet = now_utc + timedelta(hours=1)
            jst = now_utc + timedelta(hours=9)
            fmt = "%H:%M:%S"
            self.privmsg(target, f"🕒 World Clock: UTC: {now_utc.strftime(fmt)} | EST: {est.strftime(fmt)} | CET: {cet.strftime(fmt)} | JST: {jst.strftime(fmt)}")

        elif cmd == "!urlencode":
            if text_arg:
                self.privmsg(target, urllib.parse.quote(text_arg))

        elif cmd == "!urldecode":
            if text_arg:
                self.privmsg(target, urllib.parse.unquote(text_arg))

        elif cmd == "!note":
            if len(args) >= 2:
                topic = args[0].lower()
                content = " ".join(args[1:])
                self.user_data["notes"][topic] = {
                    "content": content,
                    "author": sender,
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                }
                self.save_data()
                self.privmsg(target, f"📌 Note saved under topic '{topic}'.")

        elif cmd == "!read":
            if args:
                topic = args[0].lower()
                note = self.user_data.get("notes", {}).get(topic)
                if note:
                    self.privmsg(target, f"📖 [{topic}] ({note['author']} on {note['date']}): {note['content']}")
                else:
                    self.privmsg(target, f"No note found for topic '{topic}'. Use !note <topic> <text> to add one.")

        # ---------------- TEXT LAB ----------------
        else:
            self.process_textlab_commands(sender, target, cmd, args, text_arg)

    # ------------------------------------------------------------
    def process_textlab_commands(self, sender, target, cmd, args, text_arg):
        if cmd == "!ascii":
            if not args:
                self.privmsg(target, f"Usage: !ascii [font] <text> | Fonts: {', '.join(TextLab.ASCII_FONTS)}")
                return
            font = args[0].lower()
            if font in TextLab.ASCII_FONTS and len(args) > 1:
                content = " ".join(args[1:])
            else:
                font = "block"
                content = text_arg
            lines = TextLab.render_ascii(font, content)
            if lines is None:
                self.privmsg(target, f"Unknown font '{font}'. Available fonts: {', '.join(TextLab.ASCII_FONTS)}")
                return
            for line in lines:
                self.privmsg(target, line)

        elif cmd == "!font":
            if len(args) < 2:
                self.privmsg(target, f"Usage: !font <style> <text> | Styles: {', '.join(TextLab.FONT_STYLES)}")
                return
            style, content = args[0], " ".join(args[1:])
            result = TextLab.render_font(style, content)
            if result is None:
                self.privmsg(target, f"Unknown style '{style}'. Available styles: {', '.join(TextLab.FONT_STYLES)}")
                return
            self.privmsg(target, result)

        elif cmd == "!glitch":
            if text_arg:
                self.privmsg(target, TextLab.glitch(text_arg))

        elif cmd == "!zalgo":
            if not args:
                return
            if args[0].lower() in ("low", "medium", "high") and len(args) > 1:
                intensity, content = args[0].lower(), " ".join(args[1:])
            else:
                intensity, content = "medium", text_arg
            if content:
                self.privmsg(target, TextLab.zalgo(content, intensity))

        elif cmd == "!mirror":
            if text_arg:
                self.privmsg(target, TextLab.render_font("mirror", text_arg))

        elif cmd == "!upside":
            if text_arg:
                self.privmsg(target, TextLab.render_font("upside", text_arg))

        elif cmd == "!box":
            if not args:
                self.privmsg(target, "Usage: !box [single|double|rounded|heavy] <text>")
                return
            style = args[0].lower()
            if style in TextLab.BOX_STYLES and len(args) > 1:
                content = " ".join(args[1:])
            else:
                style, content = "single", text_arg
            lines = TextLab.box(content, style)
            if not lines:
                return
            for line in lines:
                self.privmsg(target, line)

        elif cmd == "!wave":
            if text_arg:
                for line in TextLab.wave(text_arg):
                    self.privmsg(target, line)

        elif cmd == "!gradient":
            if text_arg:
                self.privmsg(target, TextLab.gradient(text_arg))

        elif cmd == "!rainbow2":
            if text_arg:
                self.privmsg(target, TextLab.rainbow2(text_arg))

        elif cmd == "!type":
            if text_arg:
                if not self.animations_enabled:
                    self.privmsg(target, TextLab._clip(text_arg, TextLab.TYPE_MAX_LEN))
                    return

                def run_type():
                    for frame in TextLab.typewriter_frames(text_arg):
                        self.privmsg(target, frame)
                        time.sleep(0.5)

                threading.Thread(target=run_type, daemon=True).start()

        elif cmd == "!loading":
            if not self.animations_enabled:
                self.privmsg(target, f"[██████████] 100% {text_arg}".strip())
                return

            def run_loading():
                for frame in TextLab.loading_frames(text_arg):
                    self.privmsg(target, frame)
                    time.sleep(0.5)

            threading.Thread(target=run_loading, daemon=True).start()

        elif cmd == "!bar":
            if args:
                try:
                    pct = int(args[0])
                except ValueError:
                    self.privmsg(target, "Usage: !bar <percentage 0-100> [\"label\"]")
                    return
                label = None
                if len(args) > 1:
                    label = " ".join(args[1:]).strip('"')
                self.privmsg(target, TextLab.progress_bar(pct, label))

        elif cmd == "!matrix":
            if text_arg:
                for line in TextLab.matrix(text_arg):
                    self.privmsg(target, line)

        elif cmd == "!explode":
            if text_arg:
                for ch in TextLab.explode_lines(text_arg):
                    self.privmsg(target, ch)

        elif cmd == "!implode":
            if text_arg:
                if not self.animations_enabled:
                    self.privmsg(target, "".join(TextLab.explode_lines(text_arg)))
                    return

                def run_implode():
                    for frame in TextLab.implode_frames(text_arg):
                        self.privmsg(target, frame)
                        time.sleep(0.5)

                threading.Thread(target=run_implode, daemon=True).start()

        elif cmd == "!scramble":
            if text_arg:
                if not self.animations_enabled:
                    self.privmsg(target, TextLab._clip(text_arg, TextLab.SCRAMBLE_MAX_LEN))
                    return

                def run_scramble():
                    for frame in TextLab.scramble_frames(text_arg):
                        self.privmsg(target, frame)
                        time.sleep(0.5)

                threading.Thread(target=run_scramble, daemon=True).start()

        elif cmd == "!fire":
            if text_arg:
                for line in TextLab.fire(text_arg):
                    self.privmsg(target, line)

        elif cmd == "!neon":
            if text_arg:
                self.privmsg(target, TextLab.neon(text_arg))

        elif cmd == "!terminal":
            if text_arg:
                if not self.animations_enabled:
                    self.privmsg(target, f"> ACCESS GRANTED > {TextLab._clip(text_arg, TextLab.LOADING_MAX_LEN)}")
                    return

                def run_terminal():
                    for frame in TextLab.terminal_frames(text_arg):
                        self.privmsg(target, frame)
                        time.sleep(0.5)

                threading.Thread(target=run_terminal, daemon=True).start()

        elif cmd == "!transform":
            if len(args) < 2:
                self.privmsg(target, f"Usage: !transform <style> <text> | Styles: {', '.join(TextLab.TRANSFORM_STYLES)}")
                return
            style, content = args[0], " ".join(args[1:])
            result = TextLab.transform(style, content)
            if result is None:
                self.privmsg(target, f"Unknown style '{style}'. Available styles: {', '.join(TextLab.TRANSFORM_STYLES)}")
                return
            self.privmsg(target, result)

        elif cmd == "!texthelp":
            lines = [
                "--- TextLab Commands ---",
                f"!ascii [font] <text> - fonts: {', '.join(TextLab.ASCII_FONTS)}",
                f"!font <style> <text> - styles: {', '.join(TextLab.FONT_STYLES)}",
                "!glitch <text> | !zalgo [low|medium|high] <text>",
                "!mirror <text> | !upside <text>",
                "!box [single|double|rounded|heavy] <text>",
                "!wave <text> | !gradient <text> | !rainbow2 <text>",
                "!type <text> | !loading <text> | !bar <pct> [\"label\"]",
                "!matrix <text> | !explode <text> | !implode <text> | !scramble <text>",
                "!fire <text> | !neon <text> | !terminal <text>",
                f"!transform <style> <text> - styles: {', '.join(TextLab.TRANSFORM_STYLES)}",
                "(admin) !animson / !animsoff - toggle animated multi-message effects",
            ]
            for line in lines:
                self.privmsg(target, line)


# ============================================================
# BOT 2: MRLOGGER
# ============================================================
class MrLogger(IRCBot):
    def __init__(self):
        super().__init__("MrLogger", f"Logger & Messenger (Operator: {DEFAULT_ADMIN})")
        self.log_file = "Logger_data.json"
        self.lock = threading.Lock()
        self.data = self.load_data()

    def load_data(self):
        with self.lock:
            if os.path.exists(self.log_file):
                try:
                    with open(self.log_file, 'r') as f:
                        return json.load(f)
                except Exception:
                    return {"logs": {}, "mailbox": {}}
            return {"logs": {}, "mailbox": {}}

    def save_data(self):
        with self.lock:
            try:
                with open(self.log_file, 'w') as f:
                    json.dump(self.data, f)
            except Exception as e:
                print(f"Error saving logs: {e}")

    def check_mailbox(self, nick, target):
        nick_low = nick.lower()
        if "mailbox" in self.data and nick_low in self.data["mailbox"]:
            messages = self.data["mailbox"][nick_low]
            for m in messages:
                self.privmsg(target, f"✉️ {nick}, Message from {m['from']} ({m['time']} UTC): {m['msg']}")
            del self.data["mailbox"][nick_low]
            self.save_data()

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 2:
            return

        if parts[1] == "JOIN":
            sender_nick = parts[0].split('!')[0].lstrip(':')
            target_chan = parts[2].lstrip(':') if len(parts) > 2 else CHANNELS[0]
            if sender_nick != self.nickname:
                self.check_mailbox(sender_nick, target_chan if target_chan.startswith("#") else CHANNELS[0])

        if parts[1] == "PRIVMSG" and len(parts) >= 4:
            sender_nick = parts[0].split('!')[0].lstrip(':')
            target = parts[2]
            message = " ".join(parts[3:])
            if message.startswith(':'):
                message = message[1:]

            if sender_nick != self.nickname:
                self.check_mailbox(sender_nick, target if target.startswith("#") else CHANNELS[0])

            if target.startswith("#"):
                self.data["logs"][sender_nick.lower()] = {
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "phrase": message,
                }
                self.save_data()

            if message.startswith("!seen ") or message.startswith(".seen "):
                args = message.split()
                if len(args) > 1:
                    query = args[1].lower()
                    if query in self.data["logs"]:
                        d = self.data["logs"][query]
                        logged_time = datetime.strptime(d['time'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        diff = int((now - logged_time).total_seconds())

                        hours = diff // 3600
                        minutes = (diff % 3600) // 60
                        seconds = diff % 60

                        time_str = f"{hours}h {minutes}m {seconds}s ago"
                        self.privmsg(target, f"I last saw {query} {time_str} (UTC time: {d['time']}) saying: \"{d['phrase']}\"")
                    else:
                        self.privmsg(target, f"I have no record of {query}.")

            if message.startswith(".tell ") or message.startswith("!tell "):
                args = message.split(maxsplit=2)
                if len(args) >= 3:
                    recipient = args[1].lower()
                    if recipient not in self.data["mailbox"]:
                        self.data["mailbox"][recipient] = []
                    self.data["mailbox"][recipient].append({
                        "from": sender_nick,
                        "msg": args[2],
                        "time": datetime.now(timezone.utc).strftime("%H:%M"),
                    })
                    self.save_data()
                    self.privmsg(target, f"I'll tell {args[1]} next time they are active.")


# ============================================================
# BOT 3: YOUTUBESEARCH
# ============================================================
class YoutubeSearch(IRCBot):
    def __init__(self):
        super().__init__(
            "YoutubeSearch",
            f"YouTube search bot (Operator: {DEFAULT_ADMIN})",
            channels=[YT_CHANNEL] + ([] if YT_CHANNEL.lower() == "#global" else ["#global"]),
            send_delay=0,  # this channel is ours - no artificial reply delay needed
        )
        self.last_search = 0.0
        self.search_lock = threading.Lock()

    def _search_primary(self, query, result_holder):
        """Primary lookup via the youtube-search-python library."""
        if VideosSearch is None:
            return
        try:
            videos_search = VideosSearch(query, limit=1)
            results = videos_search.result()
            result_list = results.get("result") if results else None
            if result_list:
                first = result_list[0]
                title = first.get("title", "YouTube video")
                video_id = first.get("id")
                url = first.get("link") or (
                    f"https://www.youtube.com/watch?v={video_id}" if video_id else None
                )
                if url:
                    result_holder["data"] = (title, url)
        except Exception as exc:
            print(f"[{self.nickname}] youtube-search-python failed: {exc}")

    def _search_fallback(self, query, result_holder):
        """Fallback: scrape YouTube's search results page directly via requests."""
        if requests is None:
            return
        try:
            encoded_query = urllib.parse.quote(query)
            search_url = f"https://www.youtube.com/results?search_query={encoded_query}"
            resp = requests.get(
                search_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=8,
            )
            resp.raise_for_status()
            html = resp.text
            video_ids = re.findall(r"watch\?v=([a-zA-Z0-9_-]{11})", html)
            if video_ids:
                vid = video_ids[0]
                title_matches = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}', html)
                title = title_matches[0] if title_matches else "YouTube Video"
                result_holder["data"] = (title, f"https://www.youtube.com/watch?v={vid}")
        except Exception as exc:
            print(f"[{self.nickname}] Fallback YouTube search error: {exc}")

    def youtube_search(self, query, timeout=15):
        """
        Looks up a YouTube video for `query`.
        Tries the youtube-search-python library first, then falls back to a
        direct HTML scrape via requests if that fails or returns nothing.
        Runs in a worker thread with a hard timeout so a hung request can
        never freeze the bot's IRC loop.
        """
        query = query.strip()
        if not query:
            return None

        result_holder = {}

        def _run():
            self._search_primary(query, result_holder)
            if "data" not in result_holder:
                self._search_fallback(query, result_holder)

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout)

        if worker.is_alive():
            print(f"[{self.nickname}] YouTube search timed out.")
            return None

        return result_holder.get("data")

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 4 or parts[1] != "PRIVMSG":
            return

        sender_nick = parts[0].split('!')[0].lstrip(':')
        target = parts[2]
        message = " ".join(parts[3:])
        if message.startswith(':'):
            message = message[1:]

        if target == self.nickname:
            target = sender_nick

        if not message.lower().startswith(".yt"):
            return
        if len(message) > 3 and not message[3].isspace():
            return

        query = message[3:].strip()
        if not query:
            self.privmsg(target, "Usage: .yt <search terms>")
            return

        with self.search_lock:
            now = time.monotonic()
            if now - self.last_search < COMMAND_COOLDOWN:
                remaining = int(COMMAND_COOLDOWN - (now - self.last_search)) + 1
                self.privmsg(target, f"Please wait {remaining}s before another YouTube search.")
                return
            self.last_search = now

        print(f"[{self.nickname}] YouTube search requested by {sender_nick}: {query}")

        def do_search():
            result = self.youtube_search(query)
            if result is None:
                self.privmsg(target, "No YouTube results found.")
                return
            title, url = result
            if len(title) > 180:
                title = title[:177] + "..."
            self.privmsg(target, f"{title} — {url}")

        threading.Thread(target=do_search, daemon=True).start()


# ============================================================
# BOT 4: YOUTUBEFORNETCAFE
# ============================================================
class YoutubeForNetCafe(YoutubeSearch):
    def __init__(self):
        # Initialize directly with IRCBot but inherit all logic from YoutubeSearch
        IRCBot.__init__(
            self,
            "YoutubeForNetCafe",
            "YoutubeForNetCafe",
            channels=["#NetCafe"],
            send_delay=0
        )
        self.last_search = 0.0
        self.search_lock = threading.Lock()


# ============================================================
# BOT 5: MAGICALFAIRY  (star-catching game, #global only)
# ------------------------------------------------------------
# - A single game thread owns the whole spawn -> catch/expire -> reschedule
#   cycle, so there can never be two stars or two timers, even if the IRC
#   connection drops and reconnects.
# - The active star is claimed under a lock, so only one user can win it.
# - Scores live in storage.py (SQLite); memory only holds the active star.
# - The next spawn time is saved in storage so a restart does not reset
#   the 40-60 minute wait.
# ============================================================
STAR_NAMES = [
    "Luna", "Nova", "Stardust", "Twinkle", "Aurora", "Celestia", "Comet", "Wish",
    "Sparkle", "Nebula", "Moonbeam", "Crystal Star", "Cosmic Pearl", "Phantom Star",
    "Golden Wish", "Dreamlight", "Eclipse", "Silverglow", "Starwhisper", "Mystic Flame",
    "Lumina", "Orion", "Vega", "Solstice", "Halo", "Glimmer", "Zenith", "Opal Star",
]


class MagicalFairy(IRCBot):
    def __init__(self):
        super().__init__(
            "MagicalFairy",
            f"MagicalFairy star game (Operator: {DEFAULT_ADMIN})",
            channels=[FAIRY_CHANNEL],
            send_delay=0.4,  # small delay keeps multi-line announcements from flooding
        )
        self.state_lock = threading.Lock()
        self.active = None                       # the one live star (or None)
        self.caught_event = threading.Event()
        self.in_channel = False                  # True once we are really inside FAIRY_CHANNEL
        self._name_bag = []
        self._last_name = None
        self._next_spawn = None
        self._notice_times = {"none": 0.0, "protected": 0.0}
        self._cmd_times = {}
        self._notice_lock = threading.Lock()
        self._game_started = False
        self._game_start_lock = threading.Lock()

    # ---------------- formatting ----------------
    @staticmethod
    def _y(text):
        """Bold + yellow (mIRC colour 08), reset afterwards."""
        return f"\x02\x0308{text}\x0F"

    def _say(self, lines):
        for line in lines:
            self.privmsg(FAIRY_CHANNEL, self._y(line))

    # ---------------- game thread ----------------
    def start_game(self):
        """Start the game loop once, no matter how many times this is called."""
        with self._game_start_lock:
            if self._game_started:
                return
            self._game_started = True
        try:
            storage.init_storage()
        except Exception as exc:
            print(f"[{self.nickname}] Storage init failed (will retry on use): {exc}")
        threading.Thread(target=self._game_loop, daemon=True).start()

    def _random_interval_seconds(self):
        lo = min(FAIRY_MIN_MINUTES, FAIRY_MAX_MINUTES)
        hi = max(FAIRY_MIN_MINUTES, FAIRY_MAX_MINUTES)
        return random.uniform(lo, hi) * 60

    def _schedule_next(self):
        self._next_spawn = time.time() + self._random_interval_seconds()
        try:
            storage.set_meta("next_spawn", f"{self._next_spawn:.0f}")
        except Exception as exc:
            print(f"[{self.nickname}] Could not save next spawn time: {exc}")
        mins = (self._next_spawn - time.time()) / 60
        print(f"[{self.nickname}] Next star in {mins:.1f} minutes.")

    def _load_next_spawn(self):
        saved = None
        try:
            saved = storage.get_meta("next_spawn")
        except Exception as exc:
            print(f"[{self.nickname}] Could not read next spawn time: {exc}")
        try:
            t = float(saved) if saved is not None else None
        except ValueError:
            t = None
        now = time.time()
        if t is None:
            self._schedule_next()
        elif t < now:
            self._next_spawn = now + 30  # we were offline past the due time: spawn shortly after startup
        else:
            self._next_spawn = t

    def _game_loop(self):
        self._load_next_spawn()
        while True:
            try:
                while True:
                    remaining = self._next_spawn - time.time()
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 15))
                while not (self.running and self._registered and self.in_channel):
                    time.sleep(5)
                self._run_star_event()
            except Exception as exc:
                print(f"[{self.nickname}] Game loop error: {type(exc).__name__}: {exc}")
                time.sleep(30)

    def _pick_name(self):
        if not self._name_bag:
            bag = STAR_NAMES[:]
            random.shuffle(bag)
            if bag and bag[-1] == self._last_name:   # last item is popped first
                bag[0], bag[-1] = bag[-1], bag[0]
            self._name_bag = bag
        name = self._name_bag.pop()
        self._last_name = name
        return name

    def _run_star_event(self):
        name = self._pick_name()
        catchable = random.random() >= FAIRY_UNCATCHABLE_CHANCE
        star = {"name": name, "catchable": catchable, "resolved": False}
        with self.state_lock:
            self.caught_event.clear()
            self.active = star

        print(f"[{self.nickname}] Star '{name}' appeared (catchable={catchable}).")
        self._say([
            f"{FAIRY_STAR} MAGICAL STAR HAS APPEARED! {FAIRY_STAR}",
            f"✨ A star named {name} has appeared in {FAIRY_CHANNEL}",
            "🎯 Type !catch or !grab to catch it!",
            f"🏆 Reward: +{FAIRY_REWARD} {FAIRY_STAR} Star!",
            f"⏳ Hurry, the magic fades in {int(FAIRY_CATCH_SECONDS)} seconds!",
        ])

        caught = self.caught_event.wait(FAIRY_CATCH_SECONDS)

        expired = False
        if not caught:
            with self.state_lock:
                if not star["resolved"]:      # nobody claimed it in the meantime
                    star["resolved"] = True
                    expired = True
        with self.state_lock:
            self.active = None

        if expired:
            if catchable:
                self._say([
                    f"{FAIRY_STAR} 💨 OH NO! {name} HAS VANISHED! 💨 {FAIRY_STAR}",
                    "⏳ Nobody managed to catch this magical star in time",
                    "🧚 MagicalFairy has whisked it away into another dimension",
                    "💫 Stay alert, the next star may be even rarer!",
                ])
            else:
                self._say([
                    f"{FAIRY_STAR} 💨 OH NO! {name} has vanished into the magical universe! 💨 {FAIRY_STAR}",
                    "🧚 MagicalFairy reveals that this star was enchanted and could not be caught!",
                    "🍀 Better luck on the next appearance!",
                ])

        self._schedule_next()

    # ---------------- anti-spam helpers ----------------
    def _notice_ok(self, kind):
        with self._notice_lock:
            now = time.monotonic()
            if now - self._notice_times[kind] < FAIRY_NOTICE_COOLDOWN:
                return False
            self._notice_times[kind] = now
            return True

    def _cmd_ok(self, nick, cmd):
        key = (nick.lower(), cmd)
        with self._notice_lock:
            now = time.monotonic()
            if now - self._cmd_times.get(key, 0.0) < FAIRY_CMD_COOLDOWN:
                return False
            self._cmd_times[key] = now
            if len(self._cmd_times) > 500:   # keep memory bounded
                cutoff = now - 60
                self._cmd_times = {k: v for k, v in self._cmd_times.items() if v > cutoff}
            return True

    # ---------------- IRC events ----------------
    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 2:
            return

        if parts[1] == "001":
            self.in_channel = False
            return

        if parts[1] == "JOIN":
            sender = parts[0].split('!')[0].lstrip(':')
            chan = parts[2].lstrip(':') if len(parts) > 2 else ""
            if sender.lower() == self.nickname.lower() and chan.lower() == FAIRY_CHANNEL.lower():
                self.in_channel = True
            return

        if parts[1] == "KICK" and len(parts) >= 4:
            if parts[2].lower() == FAIRY_CHANNEL.lower() and parts[3].lower() == self.nickname.lower():
                self.in_channel = False

                def rejoin():
                    time.sleep(5)
                    self.send(f"JOIN {FAIRY_CHANNEL}")

                threading.Thread(target=rejoin, daemon=True).start()
            return

        if parts[1] != "PRIVMSG" or len(parts) < 4:
            return

        sender = parts[0].split('!')[0].lstrip(':')
        target = parts[2]
        if target.lower() != FAIRY_CHANNEL.lower():
            return  # the game lives only in #global

        message = " ".join(parts[3:])
        if message.startswith(':'):
            message = message[1:]
        message = message.strip()
        if not message:
            return

        cmd = message.split()[0].lower()
        if cmd in ("!catch", "!grab"):
            self._handle_catch(sender)
        elif cmd == "!top":
            if self._cmd_ok(sender, cmd):
                self._handle_top()
        elif cmd == "!mystars":
            if self._cmd_ok(sender, cmd):
                self._handle_mystars(sender)

    # ---------------- commands ----------------
    def _handle_catch(self, nick):
        outcome = None
        star_name = None
        total = None

        with self.state_lock:
            star = self.active
            if star is None or star["resolved"]:
                outcome = "none"
            elif not star["catchable"]:
                outcome = "protected"
                star_name = star["name"]
            else:
                # Claim the star first (atomic under the lock): nobody else can win it now.
                star["resolved"] = True
                self.active = None
                star_name = star["name"]
                try:
                    total = storage.add_user_star(nick.lower(), nick, FAIRY_REWARD)
                    outcome = "caught"
                except Exception as exc:
                    # Could not save the reward: give the star back instead of losing it.
                    print(f"[{self.nickname}] Storage error while awarding star: {exc}")
                    star["resolved"] = False
                    self.active = star
                    outcome = "storage_error"

        if outcome == "caught":
            self.caught_event.set()
            self._say([
                f"{FAIRY_STAR} 🎉 AMAZING! {nick} CAUGHT {FAIRY_STAR} {star_name}! 🎉 {FAIRY_STAR}",
                f"✨ The magical star belongs to {nick}",
                f"🏆 Reward: +{FAIRY_REWARD} {FAIRY_STAR} Star!",
                f"💖 Total stars collected by {nick}: {total} {FAIRY_STAR}",
                "🧚 MagicalFairy is proud of you!",
            ])
        elif outcome == "protected":
            try:
                storage.record_failed_attempt(nick.lower(), nick)
            except Exception as exc:
                print(f"[{self.nickname}] Could not record failed attempt: {exc}")
            if self._notice_ok("protected"):
                self._say([
                    f"🧚 Oh no! {FAIRY_STAR} {star_name} is protected by ancient fairy magic!",
                    f"🍀 Your hands passed right through it, {nick}",
                    "💫 Keep trying your luck!",
                ])
        elif outcome == "none":
            if self._notice_ok("none"):
                self._say([
                    f"🧚 ✨ MagicalFairy giggles! There is no star to catch right now. {FAIRY_STAR}",
                    f"⏳ Keep an eye on {FAIRY_CHANNEL} for the next magical star",
                ])
        elif outcome == "storage_error":
            if self._notice_ok("none"):
                self._say(["🧚 The star ledger is resting. Try catching it again in a moment!"])

    def _handle_top(self):
        try:
            top = storage.get_top_star_collectors(FAIRY_LEADERBOARD_SIZE)
        except Exception as exc:
            print(f"[{self.nickname}] Leaderboard error: {exc}")
            self._say(["🧚 The star ledger is resting. Try again in a moment!"])
            return

        if not top:
            self._say([
                f"{FAIRY_STAR} 🏆 MAGICALFAIRY'S TOP {FAIRY_LEADERBOARD_SIZE} STAR COLLECTORS 🏆 {FAIRY_STAR}",
                "🧚 Nobody has caught a star yet. Be the first!",
            ])
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [f"{FAIRY_STAR} 🏆 MAGICALFAIRY'S TOP {FAIRY_LEADERBOARD_SIZE} STAR COLLECTORS 🏆 {FAIRY_STAR}"]
        for i, row in enumerate(top, start=1):
            lines.append(f"{medals.get(i, '✨')} {i}. {row['nick']} — {row['stars']} {FAIRY_STAR}")
        lines.append("🧚 Keep catching stars and climb the leaderboard!")
        self._say(lines)

    def _handle_mystars(self, nick):
        try:
            stats = storage.get_user_star_stats(nick.lower())
        except Exception as exc:
            print(f"[{self.nickname}] Stats error: {exc}")
            self._say(["🧚 The star ledger is resting. Try again in a moment!"])
            return

        stars = stats["stars"]
        if stars <= 0:
            status = "Star dreamer"
        elif stars < 10:
            status = "Star seeker"
        elif stars < 25:
            status = "Star collector"
        else:
            status = "Star master"

        lines = [
            f"{FAIRY_STAR} ✨ YOUR MAGICAL STAR COLLECTION ✨ {FAIRY_STAR}",
            f"🧚 Collector: {nick}",
            f"🏆 Total stars collected: {stars} {FAIRY_STAR}",
            f"🎯 Successful catches: {stats['catches']}",
        ]
        if stats["rank"]:
            lines.append(f"📈 Leaderboard rank: #{stats['rank']}")
        lines.append(f"🍀 Catching status: {status}")
        lines.append("💫 Keep going! More magical stars await you!")
        self._say(lines)


# ============================================================
# EXECUTION
# ============================================================
_bots_started = False
_bots_lock = threading.Lock()


def start_bots():
    """
    Starts all five IRC bots as background daemon threads, staggering
    their connections so they don't hit the server all at once.
    Idempotent - calling this more than once in the same process is a no-op.
    """
    global _bots_started
    with _bots_lock:
        if _bots_started:
            return
        _bots_started = True

        chief = ChiefOper()
        threading.Thread(target=chief.run_forever, daemon=True).start()

        def start_logger():
            time.sleep(5)
            logger_bot = MrLogger()
            threading.Thread(target=logger_bot.run_forever, daemon=True).start()

        threading.Thread(target=start_logger, daemon=True).start()

        def start_yt():
            time.sleep(10)
            yt_bot = YoutubeSearch()
            threading.Thread(target=yt_bot.run_forever, daemon=True).start()

        threading.Thread(target=start_yt, daemon=True).start()

        def start_netcafe():
            time.sleep(15)
            cafe_bot = YoutubeForNetCafe()
            threading.Thread(target=cafe_bot.run_forever, daemon=True).start()

        threading.Thread(target=start_netcafe, daemon=True).start()

        def start_fairy():
            time.sleep(20)
            fairy_bot = MagicalFairy()
            fairy_bot.start_game()
            threading.Thread(target=fairy_bot.run_forever, daemon=True).start()

        threading.Thread(target=start_fairy, daemon=True).start()


# Start the IRC bots as soon as this module is imported - not just inside
# `if __name__ == "__main__"`. This matters because gunicorn (now in
# requirements.txt) imports this file and serves the `app` object directly;
# it never runs the __main__ block below, so the old placement would leave
# the bots never starting under `gunicorn app:app`.
#
# IMPORTANT if deploying with gunicorn: use exactly ONE worker, e.g.
#   gunicorn app:app --workers 1
# Each gunicorn worker is a separate process/interpreter - more than one
# worker would start duplicate copies of all the bots fighting over the
# same nicknames on the same channels.
start_bots()

if __name__ == "__main__":
    # Direct run (no gunicorn): serve the Flask health-check endpoint
    # ourselves. Under gunicorn, gunicorn serves `app` instead and this
    # block is never reached.
    run_http_server()
