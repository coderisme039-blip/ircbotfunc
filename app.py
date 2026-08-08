import socket
import threading
import time
import json
import os
import random
from datetime import datetime, timezone
from flask import Flask

# --- CONFIGURATION ---
SERVER = "irc.hybridirc.com"
PORT = 6667
CHANNELS = ["#ChatWithWorld", "#Games", "#CWWHelp"]
DEFAULT_ADMIN = "Antonio"

# --- WEB SERVER FOR RENDER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bots are active and running 24/7.", 200

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- BASE IRC BOT ENGINE ---
class IRCBot:
    def __init__(self, nickname, realname):
        self.nickname = nickname
        self.realname = realname
        self.admin = DEFAULT_ADMIN
        self.only_admin_mode = False
        self.functional_mode = True
        self.running = True
        self.irc = None # Initialized in connect()

    def send(self, msg):
        if self.running and self.irc:
            try:
                self.irc.send(bytes(f"{msg}\r\n", "UTF-8"))
            except Exception as e:
                print(f"[{self.nickname}] Send error: {e}")
                self.running = False

    def privmsg(self, target, msg):
        self.send(f"PRIVMSG {target} :{msg}")

    def is_admin(self, prefix):
        nick = prefix.split('!')[0].replace(':', '')
        return nick == self.admin

    def idle_prevention(self):
        while self.running:
            time.sleep(200)
            self.send(f"PING {SERVER}")

    def connect(self):
        print(f"[{self.nickname}] Connecting to {SERVER}...")
        try:
            # Fix 5: Fresh socket initialization inside connect()
            self.irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.irc.connect((SERVER, PORT))
            
            time.sleep(1)
            self.send(f"NICK {self.nickname}")
            self.send(f"USER {self.nickname} 0 * :{self.realname}")
            
            threading.Thread(target=self.idle_prevention, daemon=True).start()

            buffer = ""
            while self.running:
                data = self.irc.recv(2048).decode("UTF-8", errors="replace")
                if not data:
                    break
                
                buffer += data
                lines = buffer.split("\r\n")
                buffer = lines.pop()

                for line in lines:
                    if line.startswith("PING"):
                        self.send(f"PONG {line.split()[1]}")
                    
                    if any(code in line for code in ["376", "422", "001"]):
                        for chan in CHANNELS:
                            self.send(f"JOIN {chan}")
                            time.sleep(0.5)
                    
                    self.handle_message(line)
        except Exception as e:
            print(f"[{self.nickname}] Connection error: {e}")
            self.running = False
        finally:
            if self.irc:
                self.irc.close()

    def handle_message(self, line):
        pass

# --- BOT 1: CHIEFOPER ---
class ChiefOper(IRCBot):
    def __init__(self):
        super().__init__("ChiefOper", "ChiefOper Official")
        self.data_file = "chief_data.json"
        # Fix 4: Threading lock for JSON
        self.lock = threading.Lock()
        self.user_data = self.load_data()

    def load_data(self):
        with self.lock:
            if os.path.exists(self.data_file):
                try:
                    with open(self.data_file, 'r') as f: return json.load(f)
                except: return {"bios": {}, "points": {}}
            return {"bios": {}, "points": {}}

    def save_data(self):
        with self.lock:
            try:
                with open(self.data_file, 'w') as f: json.dump(self.user_data, f)
            except Exception as e:
                print(f"Error saving data: {e}")

    def handle_message(self, line):
        # Fix 2: Reliable parsing using index checks
        parts = line.split(' ')
        if len(parts) < 4: return

        # parts[0] = Prefix, parts[1] = Command, parts[2] = Target, parts[3:] = Message
        if parts[1] == "PRIVMSG":
            sender_prefix = parts[0]
            sender_nick = sender_prefix.split('!')[0].lstrip(':')
            target = parts[2]
            
            # Fix 3: Safely extract and strip leading colon
            message = " ".join(parts[3:])
            if message.startswith(':'):
                message = message[1:]

            if target == self.nickname: target = sender_nick

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
                    if len(args) > 2: self.privmsg(args[1], args[2])
                elif message == "!functionalmodeon": 
                    self.functional_mode = True
                    self.privmsg(target, "Functional Mode [ON]")
                elif message == "!functionalmodeoff": 
                    self.functional_mode = False
                    self.privmsg(target, "Functional Mode [OFF]")

            if message == ".helpcww":
                rules = [
                    "--- #ChatWithWorld Rules ---",
                    "1. Respect everyone. No harassment or hate speech.",
                    "2. Use #Games channel for bot fun-commands.",
                    "3. Use #CWWHelp for admin support.",
                    "4. Use !usercmd- for all commands."
                ]
                for r in rules: self.privmsg(target, r)

            elif message in ["!usercmd", "!usercmd-"]:
                u_list = "!slap, !hug, !cookie, !greet, !roast, !compliment, !joke, !fact, !reminder <min> <msg>, .tell <user> <msg>, !seen / .seen <user>"
                self.privmsg(target, f"User Commands: {u_list}")

            if self.functional_mode:
                self.process_user_commands(sender_nick, target, message)

    def process_user_commands(self, sender, target, msg):
        cmd_parts = msg.split()
        if not cmd_parts: return
        cmd = cmd_parts[0].lower()
        args = cmd_parts[1:]

        actions = {
            "!slap": "slaps {0} with a wet fish!",
            "!hug": "hugs {0} tightly!",
            "!cookie": "hands {0} a giant cookie!",
            "!greet": "says: Hello {0}, welcome!",
            "!roast": "tells {0} their code has more bugs than a tropical rainforest!",
            "!compliment": "tells {0} they look sharp today!"
        }

        if cmd in actions:
            victim = args[0] if args else sender
            self.send(f"PRIVMSG {target} :\x01ACTION {actions[cmd].format(victim)}\x01")
        
        elif cmd == "!reminder":
            if len(args) >= 2 and args[0].isdigit():
                minutes = int(args[0])
                rem_text = " ".join(args[1:])
                def send_rem(): self.privmsg(target, f"🔔 REMINDER for {sender}: {rem_text}")
                threading.Timer(minutes * 60, send_rem).start()
                self.privmsg(target, f"Okay {sender}, I'll remind you in {minutes} minute(s).")

        elif cmd == "!joke":
            jokes = [
                "Why do programmers prefer dark mode? Because light attracts bugs.",
                "A SQL query walks into a bar, walks up to two tables and asks, 'Can I join you?'",
                "There are 10 types of people in the world: those who understand binary, and those who don't."
            ]
            self.privmsg(target, random.choice(jokes))

        elif cmd == "!fact":
            facts = [
                "Honey is the only food that doesn't spoil.",
                "An octopus has three hearts.",
                "The programming language Python was named after Monty Python."
            ]
            self.privmsg(target, random.choice(facts))

        elif cmd == "!bio":
            if args:
                self.user_data["bios"][sender] = " ".join(args)
                self.save_data()
                self.privmsg(target, f"Bio saved for {sender}.")

        elif cmd == "!profile":
            user = args[0] if args else sender
            bio = self.user_data["bios"].get(user, "No bio set.")
            pts = self.user_data["points"].get(user, 0)
            self.privmsg(target, f"Profile [{user}]: Rep: {pts} | Bio: {bio}")

# --- BOT 2: MrLogger ---
class MrLogger(IRCBot):
    def __init__(self):
        super().__init__("MrLogger", "Logger & Messenger")
        self.log_file = "Logger_data.json"
        # Fix 4: Threading lock for JSON
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
        if len(parts) < 2: return

        # Handling Joins
        if parts[1] == "JOIN":
            sender_nick = parts[0].split('!')[0].lstrip(':')
            target_chan = parts[2].lstrip(':') if len(parts) > 2 else CHANNELS[0]
            if sender_nick != self.nickname:
                self.check_mailbox(sender_nick, target_chan if target_chan.startswith("#") else CHANNELS[0])
                if "#ChatWithWorld" in line:
                    self.privmsg(CHANNELS[0], f"Welcome {sender_nick}!")

        # Handling PRIVMSG
        if parts[1] == "PRIVMSG":
            sender_nick = parts[0].split('!')[0].lstrip(':')
            target = parts[2]
            message = " ".join(parts[3:])
            if message.startswith(':'): message = message[1:]

            if sender_nick != self.nickname:
                self.check_mailbox(sender_nick, target if target.startswith("#") else CHANNELS[0])

            if target.startswith("#"):
                self.data["logs"][sender_nick.lower()] = {
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "phrase": message
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

            if message.startswith(".tell "):
                args = message.split(maxsplit=2)
                if len(args) >= 3:
                    recipient = args[1].lower()
                    if recipient not in self.data["mailbox"]: self.data["mailbox"][recipient] = []
                    self.data["mailbox"][recipient].append({
                        "from": sender_nick,
                        "msg": args[2],
                        "time": datetime.now(timezone.utc).strftime("%H:%M")
                    })
                    self.save_data()
                    self.privmsg(target, f"I'll tell {args[1]} next time they are active.")

# --- EXECUTION ---
if __name__ == "__main__":
    # Fix 1: Code cleaned of non-breaking spaces (\xa0) automatically during rewrite.
    threading.Thread(target=run_http_server, daemon=True).start()

    chief = ChiefOper()
    threading.Thread(target=chief.connect, daemon=True).start()

    time.sleep(10) # Stagger connections

    logger_bot = MrLogger()
    threading.Thread(target=logger_bot.connect, daemon=True).start()

    while True:
        time.sleep(30)
