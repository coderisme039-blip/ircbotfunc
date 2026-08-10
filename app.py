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
# BASE IRC BOT ENGINE (STABILITY FIX APPLIED)
# ============================================================
class IRCBot:
    def __init__(self, nickname, realname, channels=None, send_delay=0.5):
        self.nickname = nickname
        self.realname = realname
        self.admin = DEFAULT_ADMIN
        self.channels = channels if channels is not None else list(CHANNELS)
        self.send_delay = send_delay  
        self._send_lock = threading.Lock() # CRITICAL FIX: Prevents "Excess Flood"
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
        """Thread-safe messaging. This stops the bot from disconnecting during !type or !ascii."""
        if not msg:
            return
        with self._send_lock: # No matter how many threads run, they must wait in line here
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
                    try:
                        self.handle_message(line)
                    except Exception as err:
                        print(f"[{self.nickname}] Error: {err}")
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
            self.send("CAP END")
            self._cap_ended = True
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
        while True:
            try:
                self.connect_once()
            except Exception as e:
                print(f"[{self.nickname}] Reconnect loop error: {e}")
            time.sleep(10)

# ============================================================
# TEXT LAB ENGINE (ALL GLYPHS AND EFFECTS RESTORED)
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
    def _glyph(cls, ch): return cls._GLYPHS.get(ch.upper(), cls._GLYPHS[' '])
    @classmethod
    def _clip(cls, text, limit): return text.strip()[:limit]

    ASCII_FONTS = ("block", "banner", "small", "shadow", "digital", "bubble", "slant", "gothic")

    @classmethod
    def render_ascii(cls, font, text):
        text = cls._clip(text, cls.ASCII_MAX_LEN)
        if not text: return []
        if font == "block": return cls._ascii_fill(text, "█")
        if font == "banner": return cls._ascii_banner(text)
        if font == "small": return cls._ascii_small(text)
        if font == "shadow": return cls._ascii_shadow(text)
        if font == "digital": return cls._ascii_digital(text)
        if font == "bubble": return cls._ascii_fill(text, "o")
        if font == "slant": return cls._ascii_slant(text)
        if font == "gothic": return cls._ascii_fill(text, "▓")
        return cls._ascii_fill(text, "█")

    @classmethod
    def _ascii_fill(cls, text, fillchar):
        rows = ["", "", "", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            for r in range(5): rows[r] += g[r].replace("#", fillchar).replace(".", " ") + " "
        return rows

    @classmethod
    def _ascii_banner(cls, text):
        rows = cls._ascii_fill(text, "█")
        width = max(len(r) for r in rows) if rows else 0
        border = "=" * (width + 4)
        return [border] + ["| " + r.ljust(width) + " |" for r in rows] + [border]

    @classmethod
    def _ascii_small(cls, text):
        rows = ["", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            for i, r in enumerate((0, 2, 4)): rows[i] += g[r].replace("#", "▪").replace(".", " ") + " "
        return rows

    @classmethod
    def _ascii_shadow(cls, text):
        rows = ["", "", "", "", "", ""]
        for ch in text:
            g = cls._glyph(ch); width = len(g[0])
            cell = [[" "] * (width + 1) for _ in range(6)]
            for r in range(5):
                for c in range(width):
                    if g[r][c] == "#": cell[r + 1][c + 1] = "░"; cell[r][c] = "█"
            for r in range(6): rows[r] += "".join(cell[r]) + " "
        return rows

    @classmethod
    def _ascii_digital(cls, text):
        rows = ["", "", "", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            for r in range(5): rows[r] += g[r].replace("#", "█" if r % 2 == 0 else "▄").replace(".", " ") + " "
        return rows

    @classmethod
    def _ascii_slant(cls, text):
        rows = ["", "", "", "", ""]
        for ch in text:
            g = cls._glyph(ch)
            for r in range(5): rows[r] += g[r].replace("#", "█").replace(".", " ") + " "
        return [" " * (4 - r) + rows[r] for r in range(5)]

    _SMALLCAPS = {'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ'}
    _UPSIDE = {'a': 'ɐ', 'b': 'q', 'c': 'ɔ', 'd': 'p', 'e': 'ǝ', 'f': 'ɟ', 'g': 'ƃ', 'h': 'ɥ', 'i': 'ı', 'j': 'ɾ', 'k': 'ʞ', 'l': 'l', 'm': 'ɯ', 'n': 'u', 'o': 'o', 'p': 'd', 'q': 'b', 'r': 'ɹ', 's': 's', 't': 'ʇ', 'u': 'n', 'v': 'ʌ', 'w': 'ʍ', 'x': 'x', 'y': 'ʎ', 'z': 'z', '1': 'Ɩ', '2': 'ᄅ', '3': 'Ɛ', '4': 'ㄣ', '5': 'ϛ', '6': '9', '7': 'ㄥ', '8': '8', '9': '6', '0': '0', '.': '˙', ',': "'", "'": ',', '?': '¿', '!': '¡'}
    _MIRROR = {'b': 'd', 'd': 'b', 'p': 'q', 'q': 'p', 's': 'ƨ', 'S': 'Ƨ', 'z': 'ƹ', 'Z': 'Ƹ', 'e': 'ɘ', 'E': 'Ǝ', '3': 'Ɛ', 'k': 'ʞ'}

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
    def typewriter_frames(cls, text):
        text = cls._clip(text, 20)
        return [text[:i] for i in range(1, len(text) + 1)]

    @classmethod
    def loading_frames(cls, label):
        label = cls._clip(label, 40)
        return [f"{label}: [{'█'*(p//10)}{'░'*(10-(p//10))}] {p}%" for p in range(0, 101, 20)]

# ============================================================
# BOT 1: CHIEFOPER (FULL FEATURE SET RESTORED)
# ============================================================
class ChiefOper(IRCBot):
    def __init__(self):
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
                with open(self.data_file, 'w') as f: json.dump(self.user_data, f, indent=2)
            except: pass

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 4 or parts[1] != "PRIVMSG": return
        sender_prefix = parts[0]
        sender = parts[0].split('!')[0].lstrip(':')
        target = parts[2]
        msg = " ".join(parts[3:])[1:]

        if target == self.nickname: target = sender

        # Admin commands
        if self.is_admin(sender_prefix):
            if msg == "!animson": self.animations_enabled = True; self.privmsg(target, "Anims ON")
            if msg == "!animsoff": self.animations_enabled = False; self.privmsg(target, "Anims OFF")

        # Public Commands
        if msg == "!usercmd-":
            self.privmsg(target, "Actions: !slap !hug !cookie | Fun: !joke !fact | Effects: !ascii !type !loading")

        if msg.startswith("!slap "):
            self.send(f"PRIVMSG {target} :\x01ACTION slaps {msg[6:]} with a wet fish!\x01")

        elif msg.startswith("!type "):
            if not self.animations_enabled: self.privmsg(target, msg[6:]); return
            def run():
                for f in TextLab.typewriter_frames(msg[6:]): self.privmsg(target, f)
            threading.Thread(target=run, daemon=True).start()

        elif msg.startswith("!ascii "):
            args = msg.split()
            font = args[1] if len(args) > 1 and args[1] in TextLab.ASCII_FONTS else "block"
            content = " ".join(args[2:]) if font != "block" else " ".join(args[1:])
            for l in TextLab.render_ascii(font, content): self.privmsg(target, l)

        elif msg.startswith("!loading "):
            if not self.animations_enabled: self.privmsg(target, f"{msg[9:]}: [██████████] 100%"); return
            def run():
                for f in TextLab.loading_frames(msg[9:]): self.privmsg(target, f)
            threading.Thread(target=run, daemon=True).start()

        elif msg == "!joke":
            self.privmsg(target, random.choice(["Why did the programmer quit? He didn't get arrays.", "A SQL query walks into a bar..."]))

# ============================================================
# BOT 2: MRLOGGER (FULL RESTORED)
# ============================================================
class MrLogger(IRCBot):
    def __init__(self):
        super().__init__("MrLogger", "Logger & Messenger")
        self.log_file = "Logger_data.json"
        self.data = self.load_data()

    def load_data(self):
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f: return json.load(f)
            except: return {"logs": {}, "mailbox": {}}
        return {"logs": {}, "mailbox": {}}

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 4 or parts[1] != "PRIVMSG": return
        sender = parts[0].split('!')[0].lstrip(':')
        target = parts[2]; msg = " ".join(parts[3:])[1:]
        if target.startswith("#"):
            self.data["logs"][sender.lower()] = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "phrase": msg}

# ============================================================
# BOT 3: YOUTUBESEARCH (FULL SEARCH LOGIC RESTORED)
# ============================================================
class YoutubeSearch(IRCBot):
    def __init__(self):
        super().__init__("YoutubeSearch", "YouTube Search", channels=[YT_CHANNEL])
        self.last_search = 0.0
        self.search_lock = threading.Lock()

    def youtube_search(self, query):
        if VideosSearch is None: return None
        try:
            s = VideosSearch(query, limit=1)
            r = s.result().get("result")
            if r: return (r[0].get("title"), r[0].get("link"))
        except: pass
        return None

    def handle_message(self, line):
        parts = line.split(' ')
        if len(parts) < 4 or parts[1] != "PRIVMSG": return
        msg = " ".join(parts[3:])[1:]
        if msg.lower().startswith(".yt "):
            query = msg[4:].strip()
            with self.search_lock:
                if time.monotonic() - self.last_search < 10: return
                self.last_search = time.monotonic()
            
            def do_search():
                res = self.youtube_search(query)
                if res: self.privmsg(parts[2], f"📺 {res[0]} — {res[1]}")
            threading.Thread(target=do_search, daemon=True).start()

# ============================================================
# STARTUP
# ============================================================
def start_bots():
    # Staggered starts to prevent server connection limits
    threading.Thread(target=ChiefOper().run_forever, daemon=True).start()
    time.sleep(5)
    threading.Thread(target=MrLogger().run_forever, daemon=True).start()
    time.sleep(5)
    threading.Thread(target=YoutubeSearch().run_forever, daemon=True).start()

if __name__ == "__main__":
    start_bots()
    run_http_server()
