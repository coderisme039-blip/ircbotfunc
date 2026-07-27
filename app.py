import socket
import threading
import time
import json
import os
import random
from datetime import datetime
from flask import Flask

# --- CONFIGURATION ---
SERVER = "irc.hybridirc.com"
PORT = 6667
CHANNEL = "#chatwithworld"
DEFAULT_ADMIN = "Antonio"

# --- WEB SERVER FOR RENDER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bots are active and running 24/7.", 200

def run_http_server():
    # Render provides the PORT environment variable
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- BASE IRC BOT ENGINE ---
class IRCBot:
    def __init__(self, nickname, realname):
        self.nickname = nickname
        self.realname = realname
        self.admin = DEFAULT_ADMIN
        self.only_admin_mode = False
        self.irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.game_mode = True
        self.functional_mode = True
        self.running = True

    def send(self, msg):
        if self.running:
            self.irc.send(bytes(f"{msg}\r\n", "UTF-8"))

    def privmsg(self, target, msg):
        self.send(f"PRIVMSG {target} :{msg}")

    def is_admin(self, prefix):
        nick = prefix.split('!')[0].replace(':', '')
        return nick == self.admin

    def idle_prevention(self):
        """Sends a lightweight message every 4 minutes to prevent timeout."""
        while self.running:
            time.sleep(240)
            try:
                # Sending a NOOP or a self-targeted message keeps the socket alive
                self.send(f"NOOP")
            except:
                break

    def connect(self):
        print(f"[{self.nickname}] Connecting to {SERVER}...")
        try:
            self.irc.connect((SERVER, PORT))
            self.send(f"USER {self.nickname} 0 * :{self.realname}")
            self.send(f"NICK {self.nickname}")
            
            # Start anti-timeout heartbeat
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
                    
                    if "376" in line or "422" in line: # End of MOTD
                        self.send(f"JOIN {CHANNEL}")
                    
                    self.handle_message(line)
        except Exception as e:
            print(f"[{self.nickname}] Connection error: {e}")
            self.running = False

    def handle_message(self, line):
        pass

# --- BOT 1: CHIEFOPER ---
class ChiefOper(IRCBot):
    def __init__(self):
        super().__init__("ChiefOper", "ChiefOper")
        self.data_file = "chief_data.json"
        self.user_data = self.load_data()

    def load_data(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f: return json.load(f)
            except: return {"bios": {}, "points": {}}
        return {"bios": {}, "points": {}}

    def save_data(self):
        with open(self.data_file, 'w') as f: json.dump(self.user_data, f)

    def handle_message(self, line):
        parts = line.split()
        if len(parts) < 2: return

        # Handle Commands
        if "PRIVMSG" in parts:
            sender_prefix = parts[0]
            sender_nick = sender_prefix.split('!')[0].replace(':', '')
            target = parts[2]
            message = " ".join(parts[3:])[1:]

            # If message is to the bot directly (PM)
            if target == self.nickname:
                target = sender_nick

            # Admin Claim
            if message == "!admison":
                self.admin = sender_nick
                self.privmsg(target, f"Admin successfully claimed by: {self.admin}")
                return

            # Lock Mode Check
            if self.only_admin_mode and not self.is_admin(sender_prefix):
                return

            # Admin Features
            if self.is_admin(sender_prefix):
                if message == "!onlyadm-":
                    self.only_admin_mode = not self.only_admin_mode
                    self.privmsg(target, f"Admin-Only Lock: {'ENABLED' if self.only_admin_mode else 'DISABLED'}")
                
                elif message.startswith(".pm ") or message.startswith(".passmsg "):
                    args = message.split(maxsplit=2)
                    if len(args) > 2: self.privmsg(args[1], args[2])

                elif message == "!gamemodeon": self.game_mode = True; self.privmsg(target, "Game Mode [ON]")
                elif message == "!gamemodeoff": self.game_mode = False; self.privmsg(target, "Game Mode [OFF]")
                elif message == "!functionalmodeon": self.functional_mode = True; self.privmsg(target, "Functional Mode [ON]")
                elif message == "!functionalmodeoff": self.functional_mode = False; self.privmsg(target, "Functional Mode [OFF]")
                
                elif message == "!admcmd":
                    acmds = "!onlyadm-, !gamemodeon, !gamemodeoff, !functionalmodeon, !functionalmodeoff, .pm <target> <msg>, .passmsg <target> <msg>"
                    self.privmsg(target, f"Admin-Only Commands: {acmds}")

            # Help & Rules
            if message == ".helpcww":
                rules = [
                    "# Channel Rules",
                    "1. Respect everyone.",
                    "2. No harassment or hate speech.",
                    "3. No spam or flooding.",
                    "4. No advertising without permission.",
                    "5. Respect privacy.",
                    "6. No illegal or NSFW content.",
                    "7. No groupism or clique behavior. Don't exclude, target, or gang up on other users.",
                    "8. Be welcoming and kind to new users. Help them get settled and answer questions when you can.",
                    "9. Feel free to test bots, AI, or other channel features. Just keep your testing clean, harmless, and avoid disrupting the channel or other users.",
                    "10. Follow moderator decisions.",
                    "11. Breaking rules may result in warnings, kicks, or bans.",
                    "12. use !usercmd- for list of all the user commands"
                ]
                for r in rules: self.privmsg(target, r)

            elif message in ["!usercmd", "!usercmd-"]:
                u_list = "!slap, !hug, !cookie, !greet, !roast, !compliment, !exit, !hi5, !pat, !coffee, !flip, !unflip, !shrug, !dance, !cheers, !8ball, !choose, !define, !reminder, !joke, !fact, !fortune, !poll, !rep, !points, !bio, !profile"
                self.privmsg(target, f"User Commands: {u_list}")

            # Functional User Commands
            if self.functional_mode:
                self.process_user_commands(sender_nick, target, message)

    def process_user_commands(self, sender, target, msg):
        cmd_parts = msg.split()
        if not cmd_parts: return
        cmd = cmd_parts[0].lower()
        args = cmd_parts[1:]

        # Simple Action Commands
        actions = {
            "!slap": "slaps {0} with a wet fish!",
            "!hug": "hugs {0} tightly!",
            "!cookie": "hands {0} a giant cookie!",
            "!greet": "says: Hello {0}, welcome!",
            "!roast": "tells {0} their code has more bugs than a tropical rainforest!",
            "!compliment": "tells {0} they are looking sharp today!",
            "!hi5": "gives {0} a massive high five!",
            "!pat": "pats {0} on the head gently.",
            "!coffee": "brews a fresh cup of ☕ for {0}!",
            "!cheers": "raises a glass to {0}! 🥂"
        }

        if cmd in actions:
            victim = args[0] if args else sender
            self.send(f"PRIVMSG {target} :\x01ACTION {actions[cmd].format(victim)}\x01")
        
        elif cmd == "!exit": self.privmsg(target, f"Goodbye {sender}, we will miss you!")
        elif cmd == "!flip": self.privmsg(target, "(╯°□°）╯︵ ┻━┻")
        elif cmd == "!unflip": self.privmsg(target, "┬─┬ノ( º _ ºノ)")
        elif cmd == "!shrug": self.privmsg(target, "¯\_(ツ)_/¯")
        elif cmd == "!dance": self.privmsg(target, "└(＾＾)┐ ┌(＾＾)┘")

        # Utility Commands
        elif cmd == "!8ball":
            ans = ["Yes", "No", "Most likely", "Outlook hazy", "Signs point to yes", "Absolutely not"]
            self.privmsg(target, f"Magic 8-Ball: {random.choice(ans)}")
        
        elif cmd == "!choose":
            options = " ".join(args).split("|")
            self.privmsg(target, f"Result: {random.choice(options).strip()}")

        elif cmd == "!define":
            word = args[0] if args else "the universe"
            self.privmsg(target, f"Custom Dictionary: {word} is simply something that requires more coffee to understand.")

        elif cmd == "!reminder":
            if len(args) >= 2 and args[0].isdigit():
                minutes = int(args[0])
                rem_text = " ".join(args[1:])
                threading.Timer(minutes * 60, self.privmsg, args=(target, f"REMINDER for {sender}: {rem_text}")).start()
                self.privmsg(target, f"Okay {sender}, I'll remind you in {minutes} minute(s).")

        elif cmd == "!joke":
            jokes = ["Why do programmers prefer dark mode? Because light attracts bugs.", "A SQL query walks into a bar... joins two tables."]
            self.privmsg(target, random.choice(jokes))

        elif cmd == "!fact":
            facts = ["Honey is the only food that doesn't spoil.", "An octopus has three hearts."]
            self.privmsg(target, random.choice(facts))

        elif cmd == "!fortune":
            fortunes = ["A secret admirer will soon send you a sign.", "Your code will compile on the first try today."]
            self.privmsg(target, random.choice(fortunes))

        elif cmd == "!poll":
            self.privmsg(target, f"POLL by {sender}: {' '.join(args)} (Vote by replying!)")

        elif cmd == "!bio":
            self.user_data["bios"][sender] = " ".join(args)
            self.save_data()
            self.privmsg(target, f"Bio saved for {sender}.")

        elif cmd == "!profile":
            user = args[0] if args else sender
            bio = self.user_data["bios"].get(user, "No bio set.")
            pts = self.user_data["points"].get(user, 0)
            self.privmsg(target, f"Profile [{user}]: Rep: {pts} | Bio: {bio}")

        elif cmd == "!rep":
            if args and args[0] != sender:
                user = args[0]
                self.user_data["points"][user] = self.user_data["points"].get(user, 0) + 1
                self.save_data()
                self.privmsg(target, f"Reputation added for {user}!")
        
        elif cmd == "!points":
            pts = self.user_data["points"].get(sender, 0)
            self.privmsg(target, f"{sender}, you have {pts} reputation points.")

# --- BOT 2: MRTRACKER ---
class MrTracker(IRCBot):
    def __init__(self):
        super().__init__("MrTracker", "MrTracker")
        self.log_file = "tracker_data.json"
        self.logs = self.load_logs()

    def load_logs(self):
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f: return json.load(f)
            except: return {}
        return {}

    def save_logs(self):
        with open(self.log_file, 'w') as f: json.dump(self.logs, f)

    def handle_message(self, line):
        parts = line.split()
        if len(parts) < 2: return

        # JOIN Event
        if "JOIN" in parts:
            nick = parts[0].split('!')[0].replace(':', '')
            if nick != self.nickname:
                self.privmsg(CHANNEL, f"Welcome to #chatwithworld, {nick}! Type .helpcww to view rules and !usercmd for user commands.")

        # PRIVMSG Logging & Commands
        if "PRIVMSG" in parts:
            sender_prefix = parts[0]
            sender_nick = sender_prefix.split('!')[0].replace(':', '')
            target = parts[2]
            message = " ".join(parts[3:])[1:]

            # Track Activity (Only in channels)
            if target.startswith("#"):
                self.logs[sender_nick.lower()] = {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "phrase": message
                }
                self.save_logs()

            # Admin Claim for Tracker
            if message == "!admison":
                self.admin = sender_nick
                self.privmsg(target, f"Tracker Admin set to: {self.admin}")

            # Seen Command
            if message.startswith("!seen "):
                query = message.split()[1].lower()
                if query in self.logs:
                    data = self.logs[query]
                    self.privmsg(target, f"I last saw {query} on {data['date']} at {data['time']} saying: \"{data['phrase']}\"")
                else:
                    self.privmsg(target, f"I have no record of {query}.")

# --- EXECUTION ---
if __name__ == "__main__":
    # 1. Start HTTP Server for Render
    threading.Thread(target=run_http_server, daemon=True).start()

    # 2. Start ChiefOper
    chief = ChiefOper()
    threading.Thread(target=chief.connect, daemon=True).start()

    # 3. Start MrTracker
    tracker = MrTracker()
    threading.Thread(target=tracker.connect, daemon=True).start()

    # Keep main thread alive
    while True:
        time.sleep(30)
