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
PORT = int(os.environ.get("IRC_PORT", "6697"))
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
# BASE IRC BOT ENGINE (STABILITY FIX APPLIED)
# ============================================================
class IRCBot:
    def __init__(self, nickname, realname, channels=None, send_delay=0.5):
        self.nickname = nickname
        self.realname = realname
        self.admin = DEFAULT_ADMIN
        self.channels = channels if channels is not None else list(CHANNELS)
        self.send_delay = send_delay  
        self._send_lock = threading.Lock() # CRITICAL: Prevents Excess Flood disconnects
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
        """Thread-safe messaging. Ensures messages from animations don't crash the bot."""
        if not msg:
            return
        with self._send_lock: # Forces every thread to wait its turn
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
        print(f"[{self.nickname}] Connecting...")
        self.running = True
        self._registered = False
        self._cap_ended = False
        self.irc = self._create_socket()
        self.irc.settimeout(300)
        
        self.send("CAP LS 302")
        self.send(f"NICK {self.nickname}")
        self.send(f"USER {self.nickname} 0 * :{self.realname}")
        threading.Thread(target=self.idle_prevention, daemon=True).start()

        buffer = ""
        while self.running:
            try:
                data = self.irc.recv(4096)
                if not data: break
                buffer += data.decode("utf-8", errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if not line: continue
                    self._handle_protocol_line(line)
                    self.handle_message(line)
            except: break

        self.running = False
        try: self.irc.close()
        except: pass

    def _handle_protocol_line(self, line):
        if line.startswith("PING"):
            self.send(f"PONG {line[5:]}")
            return
        parts = line.split(' ')
        if len(parts) < 2: return
        if parts[1] == "CAP" and not self._cap_ended:
            self.send("CAP END"); self._cap_ended = True
        if parts[1] == "001" and not self._registered:
            self._registered = True
            self.send(f"MODE {self.nickname} +B")
            if NICKSERV_PASS: self.send(f"PRIVMSG NickServ :IDENTIFY {NICKSERV_PASS}")
            for chan in self.channels: self.send(f"JOIN {chan}")

    def handle_message(self, line): pass

    def run_forever(self):
        while True:
            self.connect_once()
            time.sleep(10)

# ============================================================
# TEXT LAB ENGINE (LOGIC PRESERVED)
# ============================================================
class TextLab:
    _GLYPHS = {
        'A': [".###.", "#...#", "#####", "#...#", "#...#"], 'B': ["####.", "#...#", "####.", "#...#", "####."],
        'C': [".####", "#....", "#....", "#....", ".####"], 'D': ["####.", "#...#", "#...#", "#...#", "####."],
        'E': ["#####", "#....", "###..", "#....", "#####"], 'F': ["#####", "#....", "###..", "#....", "#...."],
        'G': [".####", "#....", "#..##", "#...#", ".####"], 'H': ["#...#", "#...#", "#####", "#...#", "#...#"],
        'I': ["#####", "..#..", "..#..", "..#..", "#####"], 'J': ["..###", "...#.", "...#.", "#..#.", ".##.."],
        'K': ["#..#.", "#.#..", "##...", "#.#..", "#..#."], 'L': ["#....", "#....", "#....", "#....", "#####"],
        'M': ["#...#", "##.##", "#.#.#", "#...#", "#...#"], 'N': ["#...#", "##..#", "#.#.#", "#..##", "#...#"],
        'O': [".###.", "#...#", "#...#", "#...#", ".###."], 'P': ["####.", "#...#", "####.", "#....", "#...."],
        'Q': [".###.", "#...#", "#.#.#", "#..#.", ".##.#"], 'R': ["####.", "#...#", "####.", "#..#.", "#...#"],
        'S': [".####", "#....", ".###.", "....#", "####."], 'T': ["#####", "..#..", "..#..", "..#..", "..#.."],
        'U': ["#...#", "#...#", "#...#", "#...#", ".###."], 'V': ["#...#", "#...#", "#...#", ".#.#.", "..#.."],
        'W': ["#...#", "#...#", "#.#.#", "##.##", "#...#"], 'X': ["#...#", ".#.#.", "..#..", ".#.#.", "#...#"],
        'Y': ["#...#", ".#.#.", "..#..", "..#..", "..#.."], 'Z': ["#####", "...#.", "..#..", ".#...", "#####"],
        '0': [".###.", "#...#", "#.#.#", "#...#", ".###."], '1': ["..#..", ".##..", "..#..", "..#..", "#####"],
        '2': [".###.", "#...#", "...#.", "..#..", "#####"], '3': ["####.", "....#", "..##.", "....#", "####."],
        '4': ["#..#.", "#..#.", "#####", "...#.", "...#."], '5': ["#####", "#....", "####.", "....#", "####."],
        '6': [".###.", "#....", "####.", "#...#", ".###."], '7': ["#####", "....#", "...#.", "..#..", "..#.."],
        '8': [".###.", "#...#", ".###.", "#...#", ".###."], '9': [".###.", "#...#", ".####", "....#", ".###."],
        ' ': [".....", ".....", ".....", ".....", "....."], '!': ["..#..", "..#..", "..#..", ".....", "..#.."],
        '?': [".###.", "#...#", "..##.", ".....", "..#.."], '.': [".....", ".....", ".....", ".....", "..#.."],
        ',': [".....", ".....", ".....", "..#..", ".#..."], "'": ["..#..", "..#..", ".....", ".....", "....."],
        '-': [".....", ".....", "#####", ".....", "....."], ':': [".....", "..#..", ".....", "..#..", "....."],
    }
    ASCII_FONTS = ("block", "banner", "small", "shadow", "digital", "bubble", "slant", "gothic")

    @classmethod
    def _glyph(cls, ch): return cls._GLYPHS.get(ch.upper(), cls._GLYPHS[' '])
    @classmethod
    def _clip(cls, text, limit): return text.strip()[:limit]
    @classmethod
    def render_ascii(cls, font, text):
        text = cls._clip(text, 10)
        if not text: return []
        if font == "block": return cls._ascii_fill(text, "█")
        return cls._ascii_fill(text, "#")

    @classmethod
    def _ascii_fill(cls, text, fillchar):
        rows = ["", "", "", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            for r in range(5): rows[r] += g[r].replace("#", fillchar).replace(".", " ") + " "
        return rows

    @classmethod
    def typewriter_frames(cls, text):
        text = cls._clip(text, 20)
        return [text[:i] for i in range(1, len(text) + 1)]

    @classmethod
    def color_cycle(cls, text, palette):
        out = ""
        for i, ch in enumerate(text):
            if ch.isspace(): out += ch
            else: out += f"\x03{palette[i % len(palette)]}{ch}"
        return out + "\x03"

# ============================================================
# BOT 1: CHIEFOPER
# ============================================================
class ChiefOper(IRCBot):
    def __init__(self):
        super().__init__("ChiefOper", f"ChiefOper (Operator: {DEFAULT_ADMIN})", send_delay=0.6)
        self.data_file = "chief_data.json"
        self.lock = threading.Lock()
        self.user_data = {"bios": {}, "points": {}, "notes": {}}

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 4 or parts[1] != "PRIVMSG": return
        sender = parts[0].split('!')[0].lstrip(':')
        target = parts[2]
        msg = " ".join(parts[3:])[1:]

        if msg.startswith("!type "):
            content = msg[6:]
            def run():
                for frame in TextLab.typewriter_frames(content):
                    self.privmsg(target, frame)
            threading.Thread(target=run, daemon=True).start()

        elif msg.startswith("!ascii "):
            lines = TextLab.render_ascii("block", msg[7:])
            for l in lines: self.privmsg(target, l)

# ============================================================
# BOT 2: MRLOGGER
# ============================================================
class MrLogger(IRCBot):
    def __init__(self):
        super().__init__("MrLogger", "Logger Bot")
        self.data = {"logs": {}, "mailbox": {}}

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 4 or parts[1] != "PRIVMSG": return
        sender = parts[0].split('!')[0].lstrip(':')
        msg = " ".join(parts[3:])[1:]
        if parts[2].startswith("#"):
            self.data["logs"][sender.lower()] = msg

# ============================================================
# BOT 3: YOUTUBESEARCH (FULL SEARCH LOGIC RESTORED)
# ============================================================
class YoutubeSearch(IRCBot):
    def __init__(self):
        super().__init__("YoutubeSearch", "YouTube Search Bot", channels=[YT_CHANNEL])
        self.last_search = 0.0
        self.search_lock = threading.Lock()

    def _search_primary(self, query, result_holder):
        if VideosSearch is None: return
        try:
            v_search = VideosSearch(query, limit=1)
            results = v_search.result().get("result")
            if results:
                result_holder["data"] = (results[0].get("title"), results[0].get("link"))
        except: pass

    def _search_fallback(self, query, result_holder):
        if requests is None: return
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://www.youtube.com/results?search_query={encoded}"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            vid_ids = re.findall(r"watch\?v=([a-zA-Z0-9_-]{11})", resp.text)
            if vid_ids:
                result_holder["data"] = ("YouTube Video", f"https://www.youtube.com/watch?v={vid_ids[0]}")
        except: pass

    def youtube_search(self, query):
        res = {}
        self._search_primary(query, res)
        if "data" not in res: self._search_fallback(query, res)
        return res.get("data")

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 4 or parts[1] != "PRIVMSG": return
        sender = parts[0].split('!')[0].lstrip(':')
        msg = " ".join(parts[3:])[1:]
        if msg.lower().startswith(".yt "):
            query = msg[4:].strip()
            with self.search_lock:
                if time.
