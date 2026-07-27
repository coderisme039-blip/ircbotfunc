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
    return "Bots are running!", 200

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- BASE IRC BOT CLASS ---
class IRCBot:
    def __init__(self, nickname, realname):
        self.nickname = nickname
        self.realname = realname
        self.admin = DEFAULT_ADMIN
        self.only_admin_mode = False
        self.irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.game_mode = True
        self.functional_mode = True

    def send(self, msg):
        self.irc.send(bytes(f"{msg}\r\n", "UTF-8"))

    def privmsg(self, target, msg):
        self.send(f"PRIVMSG {target} :{msg}")

    def is_admin(self, prefix):
        nick = prefix.split('!')[0].replace(':', '')
        return nick == self.admin

    def connect(self):
        print(f"[{self.nickname}] Connecting to {SERVER}...")
        self.irc.connect((SERVER, PORT))
        self.send(f"USER {self.nickname} 0 * :{self.realname}")
        self.send(f"NICK {self.nickname}")
        
        while True:
            line = self.irc.recv(2048).decode("UTF-8", errors="ignore")
            if not line: break
            
            if line.startswith("PING"):
                self.send(f"PONG {line.split()[1]}")
            
            if "376" in line or "422" in line: # End of MOTD
                self.send(f"JOIN {CHANNEL}")
            
            self.handle_message(line)

    def handle_message(self, line):
        pass # To be overridden

# --- BOT 1: CHIEFOPER ---
class ChiefOper(IRCBot):
    def __init__(self):
        super().__init__("ChiefOper", "ChiefOper")
        self.data_file = "chief_data.json"
        self.user_data = self.load_data()
        self.active_poll = None

    def load_data(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f: return json.load(f)
        return {"bios": {}, "points": {}}

    def save_data(self):
        with open(self.data_file, 'w') as f: json.dump(self.user_data, f)

    def handle_message(self, line):
        parts = line.split()
        if len(parts) < 4 or "PRIVMSG" not in parts: return
        
        sender_prefix = parts[0]
        sender_nick = sender_prefix.split('!')[0].replace(':', '')
        target = parts[2]
        message = " ".join(parts[3:])[1:]

        # Admin Claim
        if message == "!admison":
            self.admin = sender_nick
            self.privmsg(target, f"Admin set to: {self.admin}")
            return

        # Restricted access check
        if self.only_admin_mode and not self.is_admin(sender_prefix):
            return

        # ADMIN COMMANDS
        if self.is_admin(sender_prefix):
            if message == "!onlyadm-":
                self.only_admin_mode = not self.only_admin_mode
                self.privmsg(target, f"Admin-Only Mode: {self.only_admin_mode}")
            
            elif message.startswith("!on "):
                chan = message.split()[1]
                self.send(f"JOIN {chan}")
            
            elif message.startswith("!off "):
                chan = message.split()[1]
                self.send(f"PART {chan}")

            elif message.startswith(".passmsg ") or message.startswith(".pm "):
                args = message.split(maxsplit=2)
                if len(args) > 2: self.privmsg(args[1], args[2])

            elif message == "!gamemodeon": self.game_mode = True; self.privmsg(target, "Game Mode ON")
            elif message == "!gamemodeoff": self.game_mode = False; self.privmsg(target, "Game Mode OFF")
            
            elif message == "!admcmd":
                admin_cmds = "!onlyadm-, !on #chan, !off #chan, !gamemodeon/off, !functionalmodeon/off, .pm <user> <msg>, .passmsg <chan> <msg>"
                self.privmsg(sender_nick, f"Admin Commands: {admin_cmds}")

        # RULES & HELP
        if message == ".helpcww":
            rules = [
                "# Channel Rules", "1. Respect everyone.", "2. No harassment or hate speech.",
                "3. No spam or flooding.", "4. No advertising without permission.", "5. Respect privacy.",
                "6. No illegal or NSFW content.", "7. No groupism or clique behavior.", 
                "8. Be welcoming and kind to new users.", "9. Test bots/AI cleanly.", 
                "10. Follow moderator decisions.", "11. Breaking rules results in kicks/bans.",
                "12. Use !usercdm- for command list."
            ]
            for r in rules: self.privmsg(target, r)

        elif message == "!usercdm-":
            u_cmds = "!slap, !hug, !cookie, !greet, !roast, !compliment, !hi5, !pat, !coffee, !flip, !unflip, !shrug, !dance, !cheers, !8ball, !choose, !define, !reminder, !joke, !fact, !fortune, !poll, !rep, !points, !bio, !profile"
            self.privmsg(target, f"User Commands: {u_cmds}")

        # FUNCTIONAL / FUN COMMANDS
        if self.functional_mode:
            self.handle_fun_commands(sender_nick, target, message)

    def handle_fun_commands(self, sender, target, msg):
        cmd = msg.split()[0].lower() if msg.split() else ""
        
        # Simple Interactions
        actions = {
            "!slap": "slaps {0} around a bit with a large trout!",
            "!hug": "gives {0} a big warm hug!",
            "!cookie": "gives {0} a chocolate chip cookie!",
            "!greet": "waves hello to {0}!",
            "!hi5": "gives {0} a high five!",
            "!pat": "pats {0} on the head.",
            "!coffee": "hands {0} a hot cup of coffee ☕",
            "!cheers": "raises a glass to {0}! 🍻"
        }
        
        if cmd in actions:
            target_user = msg.split()[1] if len(msg.split()) > 1 else sender
            self.send(f"PRIVMSG {target} :\x01ACTION {actions[cmd].format(target_user)}\x01")

        # Text Emotes
        emotes = {"!flip": "(╯°□°）╯︵ ┻━┻", "!unflip": "┬─┬ノ( º _ ºノ)", "!shrug": "¯\_(ツ)_/¯", "!dance": "└(＾＾)┐ ┌(＾＾)┘"}
        if cmd in emotes: self.privmsg(target, emotes[cmd])

        # Logic Commands
        if cmd == "!8ball":
            res = ["Yes", "No", "Maybe", "Ask again later", "Definitely", "Outlook not so good"]
            self.privmsg(target, f"8-Ball: {random.choice(res)}")

        elif cmd == "!joke":
            jokes = ["Why did the web developer walk out of a restaurant? Because of the table layout.", "A SQL query walks into a bar, walks up to two tables, and asks, 'Can I join you?'"]
            self.privmsg(target, random.choice(jokes))

        elif cmd == "!fact":
            facts = ["Honey never spoils.", "Octopuses have three hearts.", "Bananas are berries, but strawberries aren't."]
            self.privmsg(target, random.choice(facts))

        elif cmd == "!bio":
            bio_text = " ".join(msg.split()[1:])
            self.user_data["bios"][sender] = bio_text
            self.save_data()
            self.privmsg(target, f"Bio updated for {sender}!")

        elif cmd == "!profile":
            user = msg.split()[1] if len(msg.split()) > 1 else sender
            bio = self.user_data["bios"].get(user, "No bio set.")
            pts = self.user_data["points"].get(user, 0)
            self.privmsg(target, f"Profile [{user}]: Bio: {bio} | Rep: {pts}")

        elif cmd == "!rep":
            user = msg.split()[1] if len(msg.split()) > 1 else None
            if user and user != sender:
                self.user_data["points"][user] = self.user_data["points"].get(user, 0) + 1
                self.save_data()
                self.privmsg(target, f"{sender} gave reputation to {user}!")

        elif cmd == "!reminder":
            parts = msg.split()
            if len(parts) > 2 and parts[1].isdigit():
                minutes = int(parts[1])
                rem_msg = " ".join(parts[2:])
                threading.Timer(minutes * 60, self.privmsg, [target, f"REMINDER for {sender}: {rem_msg}"]).start()
                self.privmsg(target, f"Okay {sender}, I'll remind you in {minutes} minutes.")

# --- BOT 2: MRTRACKER ---
class MrTracker(IRCBot):
    def __init__(self):
        super().__init__("MrTracker", "MrTracker")
        self.data_file = "tracker_data.json"
        self.history = self.load_data()

    def load_data(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f: return json.load(f)
        return {}

    def save_data(self):
        with open(self.data_file, 'w') as f: json.dump(self.history, f)

    def handle_message(self, line):
        parts = line.split()
        if len(parts) < 4 or "PRIVMSG" not in parts: return
        
        sender_nick = parts[0].split('!')[0].replace(':', '')
        target = parts[2]
        message = " ".join(parts[3:])[1:]

        # Log Activity
        if target.startswith("#"):
            self.history[sender_nick.lower()] = {
                "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_msg": message
            }
            self.save_data()

        # Command: !seen
        if message.startswith("!seen "):
            lookup = message.split()[1].lower()
            if lookup in self.history:
                data = self.history[lookup]
                self.privmsg(target, f"I last saw {lookup} on {data['last_seen']} saying: \"{data['last_msg']}\"")
            else:
                self.privmsg(target, f"Sorry, I haven't seen {lookup} yet.")

# --- RUNNER ---
if __name__ == "__main__":
    # 1. Start Web Server
    threading.Thread(target=run_http_server, daemon=True).start()

    # 2. Start ChiefOper
    chief = ChiefOper()
    threading.Thread(target=chief.connect, daemon=True).start()

    # 3. Start MrTracker
    tracker = MrTracker()
    threading.Thread(target=tracker.connect, daemon=True).start()

    # Keep main thread alive
    while True:
        time.sleep(10)
