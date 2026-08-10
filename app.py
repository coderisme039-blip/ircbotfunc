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

app = Flask(__name__)
@app.route('/')
def health_check(): return "Bots are active.", 200

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ============================================================
# BASE IRC BOT ENGINE (STABILITY & PARSING FIX)
# ============================================================
class IRCBot:
    def __init__(self, nickname, realname, channels=None, send_delay=0.6):
        self.nickname = nickname
        self.realname = realname
        self.admin = DEFAULT_ADMIN
        self.channels = channels if channels is not None else list(CHANNELS)
        self.send_delay = send_delay  
        self._send_lock = threading.Lock() 
        self.running = False
        self.irc = None
        self._registered = False

    def send(self, msg):
        if self.irc:
            try:
                self.irc.sendall((msg + "\r\n").encode("utf-8", errors="replace"))
                print(f"[{self.nickname}] >>> {msg}")
            except: self.running = False

    def privmsg(self, target, msg):
        if not msg: return
        with self._send_lock:
            self.send(f"PRIVMSG {target} :{msg}")
            time.sleep(self.send_delay)

    def connect_once(self):
        print(f"[{self.nickname}] Connecting...")
        self.running = True
        raw_sock = socket.create_connection((SERVER, PORT), timeout=30)
        self.irc = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=SERVER)
        
        self.send(f"NICK {self.nickname}")
        self.send(f"USER {self.nickname} 0 * :{self.realname}")

        buffer = ""
        while self.running:
            try:
                data = self.irc.recv(4096)
                if not data: break
                buffer += data.decode("utf-8", errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    self._handle_raw(line)
            except: break
        self.running = False

    def _handle_raw(self, line):
        print(f"[{self.nickname}] <<< {line}")
        parts = line.split()
        if not parts: return

        if parts[0] == "PING":
            self.send(f"PONG {parts[1]}")
            return

        if len(parts) > 1:
            # Welcome message - Join channels
            if parts[1] == "001":
                self._registered = True
                self.send(f"MODE {self.nickname} +B")
                for c in self.channels: self.send(f"JOIN {c}")
            
            # Message handling
            if parts[1] == "PRIVMSG" and len(parts) >= 4:
                sender = parts[0].split('!')[0].lstrip(':')
                target = parts[2]
                # Better message parsing (handles leading colon)
                message = line.split(f" {target} :", 1)[-1] if f" {target} :" in line else parts[3].lstrip(':')
                self.handle_message(sender, target, message)

    def handle_message(self, sender, target, message): pass

    def run_forever(self):
        while True:
            try: self.connect_once()
            except Exception as e: print(f"Error: {e}")
            time.sleep(10)

# ============================================================
# TEXT LAB (ALL FONTS)
# ============================================================
class TextLab:
    _GLYPHS = {'A': [".###.", "#...#", "#####", "#...#", "#...#"], 'B': ["####.", "#...#", "####.", "#...#", "####."], 'C': [".####", "#....", "#....", "#....", ".####"], 'D': ["####.", "#...#", "#...#", "#...#", "####."], 'E': ["#####", "#....", "###..", "#....", "#####"], 'F': ["#####", "#....", "###..", "#....", "#...."], 'G': [".####", "#....", "#..##", "#...#", ".####"], 'H': ["#...#", "#...#", "#####", "#...#", "#...#"], 'I': ["#####", "..#..", "..#..", "..#..", "#####"], 'J': ["..###", "...#.", "...#.", "#..#.", ".##.."], 'K': ["#..#.", "#.#..", "##...", "#.#..", "#..#."], 'L': ["#....", "#....", "#....", "#....", "#####"], 'M': ["#...#", "##.##", "#.#.#", "#...#", "#...#"], 'N': ["#...#", "##..#", "#.#.#", "#..##", "#...#"], 'O': [".###.", "#...#", "#...#", "#...#", ".###."], 'P': ["####.", "#...#", "####.", "#....", "#...."], 'Q': [".###.", "#...#", "#.#.#", "#..#.", ".##.#"], 'R': ["####.", "#...#", "####.", "#..#.", "#...#"], 'S': [".####", "#....", ".###.", "....#", "####."], 'T': ["#####", "..#..", "..#..", "..#..", "..#.."], 'U': ["#...#", "#...#", "#...#", "#...#", ".###."], 'V': ["#...#", "#...#", "#...#", ".#.#.", "..#.."], 'W': ["#...#", "#...#", "#.#.#", "##.##", "#...#"], 'X': ["#...#", ".#.#.", "..#..", ".#.#.", "#...#"], 'Y': ["#...#", ".#.#.", "..#..", "..#..", "..#.."], 'Z': ["#####", "...#.", "..#..", ".#...", "#####"], '0': [".###.", "#...#", "#.#.#", "#...#", ".###."], '1': ["..#..", ".##..", "..#..", "..#..", "#####"], '2': [".###.", "#...#", "...#.", "..#..", "#####"], '3': ["####.", "....#", "..##.", "....#", "####."], '4': ["#..#.", "#..#.", "#####", "...#.", "...#."], '5': ["#####", "#....", "####.", "....#", "####."], '6': [".###.", "#....", "####.", "#...#", ".###."], '7': ["#####", "....#", "...#.", "..#..", "..#.."], '8': [".###.", "#...#", ".###.", "#...#", ".###."], '9': [".###.", "#...#", ".####", "....#", ".###."], ' ': [".....", ".....", ".....", ".....", "....."]}
    
    @classmethod
    def render_ascii(cls, text):
        text = text.strip()[:10]
        rows = ["", "", "", "", ""]
        for ch in text:
            g = cls._GLYPHS.get(ch.upper(), cls._GLYPHS[' '])
            for r in range(5): rows[r] += g[r].replace("#", "█").replace(".", " ") + " "
        return rows

# ============================================================
# THE BOTS
# ============================================================
class ChiefOper(IRCBot):
    def handle_message(self, sender, target, message):
        dest = sender if target == self.nickname else target
        msg = message.strip()

        if msg == "!usercmd-":
            self.privmsg(dest, "Commands: !slap <nick>, !joke, !ascii <text>, !type <text>")

        elif msg.startswith("!slap "):
            self.send(f"PRIVMSG {dest} :\x01ACTION slaps {msg[6:]}!\x01")

        elif msg.startswith("!ascii "):
            for line in TextLab.render_ascii(msg[7:]): self.privmsg(dest, line)

        elif msg.startswith("!type "):
            text = msg[6:][:20]
            def run():
                for i in range(1, len(text)+1): self.privmsg(dest, text[:i])
            threading.Thread(target=run, daemon=True).start()

        elif msg == "!joke":
            self.privmsg(dest, "Why did the web developer walk out of a restaurant? Because of the table layout.")

class MrLogger(IRCBot):
    def handle_message(self, sender, target, message):
        if target.startswith("#"):
            print(f"[LOG] {target} <{sender}> {message}")

class YoutubeSearch(IRCBot):
    def __init__(self):
        super().__init__("YoutubeSearch", "YouTube Search", channels=[YT_CHANNEL])
        self.last = 0

    def handle_message(self, sender, target, message):
        if message.lower().startswith(".yt "):
            now = time.monotonic()
            if now - self.last < 10: return
            self.last = now
            query = message[4:].strip()
            dest = sender if target == self.nickname else target
            
            def do_search():
                if VideosSearch:
                    try:
                        s = VideosSearch(query, limit=1).result()['result'][0]
                        self.privmsg(dest, f"📺 {s['title']} - {s['link']}")
                    except: self.privmsg(dest, "No results.")
            threading.Thread(target=do_search, daemon=True).start()

# ============================================================
# START
# ============================================================
def start():
    threading.Thread(target=ChiefOper().run_forever, daemon=True).start()
    time.sleep(2)
    threading.Thread(target=MrLogger().run_forever, daemon=True).start()
    time.sleep(2)
    threading.Thread(target=YoutubeSearch().run_forever, daemon=True).start()

if __name__ == "__main__":
    start()
    run_http_server()
