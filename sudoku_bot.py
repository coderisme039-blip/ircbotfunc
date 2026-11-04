"""
sudoku_bot.py - Sudoku IRC bot (text mode only) with points and its own SQLite database.

Used from app.py:   SudokuBot().run()   (run it in a daemon thread)

Optional environment settings:
  IRC_SERVER / IRC_HOST   IRC server (default: irc.hybridirc.com)
  IRC_PORT                IRC server port (default: 6697)
  SUDOKU_NICK             Bot nickname (default: Sudoku)
  SUDOKU_ADMINS           Comma-separated admin nicks (default: Antonio,Starboy)
  SUDOKU_DB               Database file (default: sudoku.db) - separate from MagicalFairy
  IRC_SSL                 Set to 0 to disable TLS

Uses only the Python standard library.
"""

from __future__ import annotations

import logging
import math
import os
import queue
import random
import re
import socket
import sqlite3
import ssl
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from typing import Optional


LOG = logging.getLogger("sudoku")

DEFAULT_CHANNELS = ["#Games", "#global"]
ROWS = "abcdefghi"
DIFFICULTIES = {
    "!easy": (45, 0.5),
    "!medium": (35, 1.0),
    "!hard": (28, 2.0),
    "!expert": (24, 3.0),
    "!master": (21, 4.0),
    "!extreme": (17, 5.0),
}
POINTS = {
    "easy": 50,
    "medium": 100,
    "hard": 200,
    "expert": 300,
    "master": 400,
    "extreme": 500,
}
MISTAKE_PENALTY = 5      # points lost per mistake
MAX_MISTAKES = 10        # more than this many mistakes = LOSE
FILL_COOLDOWN = 10       # seconds nobody can !fill after a correct answer
PENDING_TIMEOUT = 120    # seconds to pick a difficulty
LEADERBOARD_SIZE = 10
SPTS_COOLDOWN = 5        # seconds between !spts per channel

CHANNEL_RE = re.compile(r"^#[^\s,\x07\x00-\x1f]{1,49}$")
JOIN_FAIL_NUMERICS = {"403", "405", "471", "473", "474", "475", "476", "477", "520"}


def empty_board() -> list[list[int]]:
    return [[0 for _ in range(9)] for _ in range(9)]


def format_text_board(board: list[list[int]]) -> str:
    """Render the grouped text format (unchanged)."""
    lines = []
    for row_index, row in enumerate(board):
        groups = [
            " - ".join(str(value) if value else "?" for value in row[start : start + 3])
            for start in (0, 3, 6)
        ]
        lines.append(f"| {' | '.join(groups)} |")
        if row_index in (2, 5):
            lines.append("---------------------------------------")
    return "\n".join(lines)


def _shuffled(items: list[int]) -> list[int]:
    random.shuffle(items)
    return items


# ============================================================
# ENGINE (unchanged logic)
# ============================================================
class SudokuEngine:
    """MRV/bit-mask solver and randomized unique-puzzle generator."""

    def search(
        self,
        grid: list[list[int]],
        limit: int = 2,
        randomize: bool = False,
    ) -> tuple[int, Optional[list[list[int]]]]:
        board = [row[:] for row in grid]
        row_masks = [0] * 9
        col_masks = [0] * 9
        box_masks = [0] * 9

        for row in range(9):
            for col in range(9):
                value = board[row][col]
                if not value:
                    continue
                if not 1 <= value <= 9:
                    return 0, None
                bit = 1 << value
                box = (row // 3) * 3 + col // 3
                if row_masks[row] & bit or col_masks[col] & bit or box_masks[box] & bit:
                    return 0, None
                row_masks[row] |= bit
                col_masks[col] |= bit
                box_masks[box] |= bit

        count = 0
        first_solution: Optional[list[list[int]]] = None

        def search_next() -> None:
            nonlocal count, first_solution
            best_row = best_col = -1
            best_mask = 0
            best_count = 10

            for row in range(9):
                for col in range(9):
                    if board[row][col]:
                        continue
                    box = (row // 3) * 3 + col // 3
                    mask = ~(row_masks[row] | col_masks[col] | box_masks[box]) & 0x3FE
                    candidate_count = bin(mask).count("1")
                    if candidate_count == 0:
                        return
                    if candidate_count < best_count:
                        best_row, best_col = row, col
                        best_mask, best_count = mask, candidate_count
                        if candidate_count == 1:
                            break
                if best_count == 1:
                    break

            if best_row == -1:
                count += 1
                if first_solution is None:
                    first_solution = [row[:] for row in board]
                return

            candidates = [
                digit for digit in range(1, 10) if best_mask & (1 << digit)
            ]
            if randomize:
                random.shuffle(candidates)
            box = (best_row // 3) * 3 + best_col // 3

            for digit in candidates:
                bit = 1 << digit
                board[best_row][best_col] = digit
                row_masks[best_row] |= bit
                col_masks[best_col] |= bit
                box_masks[box] |= bit

                search_next()

                board[best_row][best_col] = 0
                row_masks[best_row] &= ~bit
                col_masks[best_col] &= ~bit
                box_masks[box] &= ~bit
                if count >= limit:
                    return

        search_next()
        return count, first_solution

    def generate(self, difficulty: str) -> tuple[list[list[int]], list[list[int]], int]:
        target, seconds = DIFFICULTIES.get(difficulty, DIFFICULTIES["!medium"])
        deadline = time.monotonic() + seconds
        best_puzzle: Optional[list[list[int]]] = None
        best_solution: Optional[list[list[int]]] = None
        best_clues = 82

        while best_clues > target and time.monotonic() < deadline:
            _, solution = self.search(empty_board(), limit=1, randomize=True)
            if solution is None:
                raise RuntimeError("Sudoku solution generation failed.")
            puzzle = [row[:] for row in solution]
            cells = _shuffled(list(range(81)))
            clues = 81

            for cell in cells:
                if clues <= target:
                    break
                row, col = divmod(cell, 9)
                previous = puzzle[row][col]
                puzzle[row][col] = 0
                solution_count, _ = self.search(puzzle, limit=2)
                if solution_count == 1:
                    clues -= 1
                else:
                    puzzle[row][col] = previous

            if clues < best_clues:
                best_puzzle = puzzle
                best_solution = solution
                best_clues = clues

        if best_puzzle is None or best_solution is None:
            raise RuntimeError("Sudoku puzzle generation failed.")
        return best_puzzle, best_solution, best_clues


# ============================================================
# DATABASE (separate from MagicalFairy's database)
# ============================================================
class SudokuStore:
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS sudoku_players (
        user_id TEXT PRIMARY KEY,
        nick    TEXT NOT NULL,
        points  INTEGER NOT NULL DEFAULT 0,
        wins    INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sudoku_channels (
        name TEXT PRIMARY KEY
    );
    CREATE TABLE IF NOT EXISTS sudoku_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_locked(self) -> None:
        if self._ready:
            return
        try:
            with closing(self._connect()) as conn:
                with conn:
                    conn.executescript(self._SCHEMA)
        except sqlite3.DatabaseError as exc:
            backup = f"{self.path}.corrupt-{int(time.time())}"
            LOG.error("Sudoku database unreadable (%s); moving it to %s.", exc, backup)
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            with closing(self._connect()) as conn:
                with conn:
                    conn.executescript(self._SCHEMA)
        self._ready = True

    # ---- points ----
    def add_points(self, nick: str, points: int) -> int:
        """Add points (and one win). Returns the player's new total."""
        uid = nick.strip().lower()
        with self.lock:
            self._init_locked()
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO sudoku_players (user_id, nick, points, wins)
                        VALUES (?, ?, ?, 1)
                        ON CONFLICT(user_id) DO UPDATE SET
                            nick   = excluded.nick,
                            points = sudoku_players.points + excluded.points,
                            wins   = sudoku_players.wins + 1
                        """,
                        (uid, nick, int(points)),
                    )
                    row = conn.execute(
                        "SELECT points FROM sudoku_players WHERE user_id = ?", (uid,)
                    ).fetchone()
        return int(row["points"])

    def ranking(self) -> list[dict]:
        """All players, best first (points, then wins, then name - deterministic)."""
        with self.lock:
            self._init_locked()
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT user_id, nick, points, wins FROM sudoku_players "
                    "ORDER BY points DESC, wins DESC, user_id ASC LIMIT 1000"
                ).fetchall()
        return [dict(r) for r in rows]

    # ---- channels (so !call / !part survive restarts) ----
    def load_channels(self, defaults: list[str]) -> list[str]:
        with self.lock:
            self._init_locked()
            with closing(self._connect()) as conn:
                with conn:
                    seeded = conn.execute(
                        "SELECT value FROM sudoku_meta WHERE key = 'channels_seeded'"
                    ).fetchone()
                    if not seeded:
                        for name in defaults:
                            conn.execute(
                                "INSERT OR IGNORE INTO sudoku_channels (name) VALUES (?)", (name,)
                            )
                        conn.execute(
                            "INSERT OR REPLACE INTO sudoku_meta (key, value) VALUES ('channels_seeded', '1')"
                        )
                rows = conn.execute("SELECT name FROM sudoku_channels ORDER BY rowid").fetchall()
        return [r["name"] for r in rows]

    def add_channel(self, name: str) -> None:
        with self.lock:
            self._init_locked()
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute("INSERT OR IGNORE INTO sudoku_channels (name) VALUES (?)", (name,))

    def remove_channel(self, name: str) -> None:
        with self.lock:
            self._init_locked()
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        "DELETE FROM sudoku_channels WHERE lower(name) = lower(?)", (name,)
                    )


# ============================================================
# GAME STATE
# ============================================================
@dataclass
class Game:
    active: bool = False
    pending: bool = False
    player: Optional[str] = None          # who started it
    difficulty: Optional[str] = None
    clues: int = 0
    mistakes: int = 0
    pending_since: float = 0.0
    fill_block_until: float = 0.0         # nobody can !fill until this time (monotonic)
    board: list[list[int]] = field(default_factory=empty_board)
    solution: list[list[int]] = field(default_factory=empty_board)


def _parse_privmsg(line: str) -> Optional[tuple[str, str, str]]:
    match = re.match(r"^:([^! ]+)(?:![^ ]+)? PRIVMSG ([^ ]+) :([\s\S]*)$", line)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3).strip()


# ============================================================
# BOT
# ============================================================
class SudokuBot:
    def __init__(self) -> None:
        self.host = os.getenv("IRC_SERVER") or os.getenv("IRC_HOST", "irc.hybridirc.com")
        try:
            self.port = int(os.getenv("IRC_PORT", "6697"))
            if not 1 <= self.port <= 65535:
                raise ValueError
        except ValueError:
            LOG.warning("Invalid IRC_PORT; using 6697.")
            self.port = 6697
        self.nick = os.getenv("SUDOKU_NICK", "Sudoku")
        admins = os.getenv("SUDOKU_ADMINS", "Antonio,Starboy")
        self.admins = {a.strip().lower() for a in admins.split(",") if a.strip()}
        self.use_ssl = os.getenv("IRC_SSL", "1") != "0"
        self.notation_link = "No notation link set."

        self.store = SudokuStore(os.getenv("SUDOKU_DB", "sudoku.db"))
        self.engine = SudokuEngine()

        try:
            channel_names = self.store.load_channels(DEFAULT_CHANNELS)
        except Exception as exc:
            LOG.error("Could not load channel list (%s); using defaults.", exc)
            channel_names = list(DEFAULT_CHANNELS)
        self.channels: dict[str, str] = {c.lower(): c for c in channel_names}   # lower -> display
        self.games: dict[str, Game] = {k: Game() for k in self.channels}
        self.call_requests: dict[str, str] = {}      # channel(lower) -> admin who asked
        self.last_spts: dict[str, float] = {}

        self.outbound: queue.Queue[str] = queue.Queue()
        self.socket: Optional[socket.socket] = None
        self.running = True
        self.online = False
        self.next_message_at = 0.0

    # ---------------- output ----------------
    def say(self, channel: str, message: str) -> None:
        for line in message.splitlines():
            line = line.rstrip()
            if line:
                self.outbound.put(f"PRIVMSG {channel} :{line}")

    def notice(self, nick: str, message: str) -> None:
        self.outbound.put(f"NOTICE {nick} :{message}")

    def _send_raw(self, message: str) -> None:
        if self.socket is None:
            return
        self.socket.sendall((message + "\r\n").encode("utf-8", errors="replace"))

    def _flush_outbound(self) -> None:
        if not self.online or self.socket is None or time.monotonic() < self.next_message_at:
            return
        try:
            message = self.outbound.get_nowait()
        except queue.Empty:
            return
        self._send_raw(message)
        self.next_message_at = time.monotonic() + 0.5

    def _clear_outbound(self) -> None:
        while True:
            try:
                self.outbound.get_nowait()
            except queue.Empty:
                return

    # ---------------- connection ----------------
    def _connect(self) -> None:
        LOG.info(
            "Connecting to %s:%s over %s",
            self.host,
            self.port,
            "TLS" if self.use_ssl else "plain IRC",
        )
        raw_socket = socket.create_connection((self.host, self.port), timeout=20)
        if self.use_ssl:
            context = ssl.create_default_context()
            if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
                context.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
            self.socket = context.wrap_socket(raw_socket, server_hostname=self.host)
        else:
            self.socket = raw_socket
        self.socket.settimeout(0.2)
        self._send_raw(f"NICK {self.nick}")
        self._send_raw(f"USER {self.nick} 0 * :Sudoku Bot")

        buffer = ""
        last_keepalive = time.monotonic()
        while self.running:
            try:
                chunk = self.socket.recv(4096)
                if not chunk:
                    raise ConnectionError("IRC server closed the connection.")
                buffer += chunk.decode("utf-8", errors="replace")
                lines = re.split(r"\r?\n", buffer)
                buffer = lines.pop()
                for line in lines:
                    if line.strip():
                        try:
                            self._handle_line(line.strip())
                        except (OSError, ConnectionError):
                            raise
                        except Exception:
                            LOG.exception("Error handling line: %s", line)
            except socket.timeout:
                pass

            now = time.monotonic()
            if now - last_keepalive >= 240:
                self._send_raw("PING :keepalive")
                last_keepalive = now
            self._expire_pending()
            self._flush_outbound()

    def run(self) -> None:
        retry_delay = 5
        try:
            while self.running:
                try:
                    self._connect()
                    retry_delay = 5
                except Exception as error:
                    if not self.running:
                        break
                    self.online = False
                    LOG.error("IRC connection failed: %s", error)
                    self._clear_outbound()
                    if self.socket is not None:
                        try:
                            self.socket.close()
                        except OSError:
                            pass
                    self.socket = None
                    LOG.info("Retrying in %s seconds.", retry_delay)
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 120)
        except KeyboardInterrupt:
            LOG.info("Stopping Sudoku bot.")
        finally:
            self.running = False
            self.online = False
            if self.socket is not None:
                try:
                    self.socket.close()
                except OSError:
                    pass
            self.socket = None

    # ---------------- IRC lines ----------------
    def _handle_line(self, line: str) -> None:
        if line.startswith("PING"):
            self._send_raw("PONG " + line[5:].strip())
            return

        numeric = re.match(r"^:[^ ]+ (\d{3}) ", line)
        if numeric and numeric.group(1) == "001":
            self.online = True
            LOG.info("Connected as %s. Joining %d channel(s).", self.nick, len(self.channels))
            self._send_raw(f"MODE {self.nick} +B")
            for name in self.channels.values():
                self._send_raw(f"JOIN {name}")
            return
        if numeric and numeric.group(1) == "433":
            self.nick += "_"
            LOG.warning("Nickname is in use; trying %s.", self.nick)
            self._send_raw(f"NICK {self.nick}")
            return
        if numeric and numeric.group(1) in JOIN_FAIL_NUMERICS:
            failed = re.match(r"^:[^ ]+ \d{3} [^ ]+ ([^ ]+)", line)
            if failed:
                self._join_failed(failed.group(1))
            return

        join = re.match(r"^:([^! ]+)(?:![^ ]+)? JOIN :?([^ ]+)", line)
        if join:
            if join.group(1).lower() == self.nick.lower():
                requester = self.call_requests.pop(join.group(2).lower(), None)
                if requester:
                    self.notice(requester, f"Joined {join.group(2)}")
            return

        parsed = _parse_privmsg(line)
        if not parsed:
            return
        sender, target, message = parsed
        if sender.lower() == self.nick.lower():
            return
        if target.lower() == self.nick.lower():
            self._handle_pm(sender, message)
        else:
            self._handle_message(sender, target, message)

    def _join_failed(self, channel: str) -> None:
        key = channel.lower()
        requester = self.call_requests.pop(key, None)
        if requester is None:
            LOG.warning("Could not join %s.", channel)   # a saved channel: keep it, just log
            return
        self.channels.pop(key, None)
        self.games.pop(key, None)
        try:
            self.store.remove_channel(channel)
        except Exception:
            LOG.exception("Could not remove failed channel from database.")
        self.notice(requester, f"Could not join {channel} (invite-only, banned, full, or does not exist).")

    # ---------------- admin commands (PM only) ----------------
    def _pm(self, nick: str, message: str) -> None:
        self.outbound.put(f"PRIVMSG {nick} :{message}")

    def _handle_pm(self, sender: str, raw: str) -> None:
        parts = raw.strip().split()
        if not parts:
            return
        command = parts[0].lower()
        if command not in ("!call", "!part"):
            return
        if sender.lower() not in self.admins:
            return  # admin-only: silently ignore everyone else
        if len(parts) != 2 or not CHANNEL_RE.match(parts[1]):
            self._pm(sender, f"Use: {command} #channel")
            return

        channel = parts[1]
        key = channel.lower()

        if command == "!call":
            if key in self.channels:
                self._pm(sender, f"I am already in {self.channels[key]}.")
                return
            self.channels[key] = channel
            self.games[key] = Game()
            self.call_requests[key] = sender
            try:
                self.store.add_channel(channel)
            except Exception:
                LOG.exception("Could not save channel %s.", channel)
            self._send_raw(f"JOIN {channel}")
            self._pm(sender, f"Joining {channel} ...")
        else:
            if key not in self.channels:
                self._pm(sender, f"I am not in {channel}.")
                return
            display = self.channels.pop(key)
            self.games.pop(key, None)
            self.call_requests.pop(key, None)
            try:
                self.store.remove_channel(display)
            except Exception:
                LOG.exception("Could not remove channel %s.", display)
            self._send_raw(f"PART {display} :Requested by {sender}")
            self._pm(sender, f"Left {display}.")

    # ---------------- channel commands ----------------
    @staticmethod
    def _points_now(game: Game) -> int:
        base = POINTS.get(game.difficulty or "", 0)
        return max(0, base - MISTAKE_PENALTY * game.mistakes)

    def _handle_message(self, sender: str, channel: str, raw_message: str) -> None:
        key = channel.lower()
        game = self.games.get(key)
        if game is None:
            return
        message = raw_message.strip()
        if not message or message[0] not in "!.":
            return

        self._expire_pending()
        game = self.games.get(key)
        if game is None:
            return
        lower = message.lower()
        command = lower.split(maxsplit=1)[0]
        is_admin = sender.lower() in self.admins

        if command in ("!su", ".su"):
            self._show_status(channel, game)
            return
        if command[0] != "!":
            return

        if command == "!faqsudoku":
            self.say(channel, "--- Sudoku FAQ ---")
            self.say(channel, "!sudoku - start a game, then pick a level: "
                              "!easy (50) !medium (100) !hard (200) !expert (300) !master (400) !extreme (500)")
            self.say(channel, "!fill Aa 5 - put 5 in row A, column a (anyone can play)")
            self.say(channel, "!su or .su - show the current board and game status")
            self.say(channel, "!spts - points ranking")
            self.say(channel, "!giveupgame - end the game (whoever started it, or an admin)")
            self.say(channel, "!notation - how the coordinates work")
            self.say(channel, f"Scoring: each mistake costs {MISTAKE_PENALTY} points. "
                              f"More than {MAX_MISTAKES} mistakes = you LOSE.")
            return

        if command == "!notation":
            self.say(channel, "Rows = A-I, columns = a-i. Example: !fill Bi 5 puts 5 in row B, column i.")
            self.say(channel, f"Notation link: {self.notation_link}")
            return

        if command == "!setnotation" and is_admin:
            parts = message.split(maxsplit=1)
            if len(parts) != 2:
                self.say(channel, "Use: !setnotation <link>")
                return
            link = parts[1].strip()
            if not re.match(r"^https?://[^\s/]+", link):
                self.say(channel, "Use: !setnotation <http-or-https-link>")
                return
            self.notation_link = link
            self.say(channel, "Notation link updated.")
            return

        if command == "!spts":
            self._show_ranking(sender, channel)
            return

        if command == "!sudoku":
            if game.active:
                self.say(channel, f"Game in progress (started by {game.player}). Use !su to see the board.")
            elif game.pending:
                self.say(channel, f"{game.player} already started a game. Choose: "
                                  "!easy !medium !hard !expert !master !extreme")
            else:
                self.games[key] = Game(pending=True, player=sender, pending_since=time.monotonic())
                self.say(channel, f"Hi {sender}, choose: !easy !medium !hard !expert !master !extreme")
            return

        if command in DIFFICULTIES and game.pending:
            self._start_game(channel, key, game, command)
            return

        if command == "!fill" and game.active:
            self._handle_fill(sender, channel, key, game, lower)
            return

        if command == "!giveupgame" and (game.active or game.pending):
            if sender.lower() != (game.player or "").lower() and not is_admin:
                self.notice(sender, "Only the player who started the game (or an admin) can give up.")
                return
            if game.active:
                self.say(channel, "Game over. Solution:")
                self.say(channel, format_text_board(game.solution))
            else:
                self.say(channel, "Game cancelled.")
            self.games[key] = Game()

    def _start_game(self, channel: str, key: str, game: Game, command: str) -> None:
        name = command[1:]
        self.say(channel, f"Generating your {name} puzzle, one moment...")
        try:
            board, solution, clues = self.engine.generate(command)
        except Exception:
            LOG.exception("Sudoku puzzle generation failed.")
            self.games[key] = Game()
            self.say(channel, "Puzzle generation failed. Please start another game with !sudoku.")
            return
        new_game = Game(
            active=True,
            player=game.player,
            difficulty=name,
            clues=clues,
            board=board,
            solution=solution,
        )
        self.games[key] = new_game
        self.say(
            channel,
            f"Game started ({name}, {clues} clues, worth {POINTS[name]} points)! "
            "Use !fill Aa 5 (row, column, number). Anyone can play.",
        )
        self.say(channel, format_text_board(new_game.board))

    def _handle_fill(self, sender: str, channel: str, key: str, game: Game, lower: str) -> None:
        now = time.monotonic()
        if now < game.fill_block_until:
            remaining = math.ceil(game.fill_block_until - now)
            self.notice(sender, f"You can't use !fill for {remaining} more sec.")
            return

        args = lower.split()
        coordinate = args[1] if len(args) > 1 else ""
        try:
            value = int(args[2]) if len(args) > 2 else 0
        except ValueError:
            value = 0
        row = ROWS.find(coordinate[0]) if len(coordinate) >= 1 else -1
        col = ROWS.find(coordinate[1]) if len(coordinate) >= 2 else -1
        if (
            len(args) != 3
            or len(coordinate) != 2
            or row < 0
            or col < 0
            or not 1 <= value <= 9
        ):
            self.say(channel, "Use: !fill Aa 5 (row A-I, column a-i, number 1-9).")
            return
        if game.board[row][col] != 0:
            self.say(channel, f"{coordinate[0].upper()}{coordinate[1]} is already filled.")
            return

        if game.solution[row][col] != value:
            game.mistakes += 1
            if game.mistakes > MAX_MISTAKES:
                self.say(channel, f"Too many mistakes (more than {MAX_MISTAKES})! You LOSE. Solution:")
                self.say(channel, format_text_board(game.solution))
                self.games[key] = Game()
            else:
                self.say(channel, f"Incorrect! Try again. (Mistakes: {game.mistakes}/{MAX_MISTAKES})")
            return

        game.board[row][col] = value
        game.fill_block_until = now + FILL_COOLDOWN
        self.say(channel, "Correct!")
        self.say(channel, format_text_board(game.board))

        if all(cell != 0 for line in game.board for cell in line):
            earned = self._points_now(game)
            result = (
                "no mistakes!"
                if game.mistakes == 0
                else f"{game.mistakes} mistake(s), -{game.mistakes * MISTAKE_PENALTY} points."
            )
            try:
                total = self.store.add_points(sender, earned)
                self.say(channel, f"WINNER! Congrats {sender}! ({result}) +{earned} points | Total: {total} points")
            except Exception:
                LOG.exception("Could not save points.")
                self.say(channel, f"WINNER! Congrats {sender}! ({result}) +{earned} points (could not be saved)")
            self.games[key] = Game()

    def _show_status(self, channel: str, game: Game) -> None:
        if game.active:
            self.say(
                channel,
                f"Sudoku ({game.difficulty}) | started by {game.player} | "
                f"Mistakes: {game.mistakes}/{MAX_MISTAKES} | Worth now: {self._points_now(game)} points",
            )
            self.say(channel, format_text_board(game.board))
        elif game.pending:
            self.say(channel, f"{game.player} is choosing a difficulty: "
                              "!easy !medium !hard !expert !master !extreme")
        else:
            self.say(channel, "No game running. Start one with !sudoku.")

    def _show_ranking(self, sender: str, channel: str) -> None:
        now = time.monotonic()
        key = channel.lower()
        if now - self.last_spts.get(key, 0.0) < SPTS_COOLDOWN:
            return
        self.last_spts[key] = now
        try:
            ranking = self.store.ranking()
        except Exception:
            LOG.exception("Could not read ranking.")
            self.say(channel, "Points ranking is unavailable right now.")
            return
        if not ranking:
            self.say(channel, "No points yet. Win a game to get on the board!")
            return
        self.say(channel, f"--- Sudoku Top {LEADERBOARD_SIZE} ---")
        for i, row in enumerate(ranking[:LEADERBOARD_SIZE], start=1):
            self.say(channel, f"{i}. {row['nick']} - {row['points']} pts ({row['wins']} wins)")
        for i, row in enumerate(ranking, start=1):
            if row["user_id"] == sender.lower() and i > LEADERBOARD_SIZE:
                self.say(channel, f"Your rank: #{i} - {row['points']} pts ({row['wins']} wins)")
                break

    def _expire_pending(self) -> None:
        now = time.monotonic()
        for key, game in list(self.games.items()):
            if game.pending and now - game.pending_since > PENDING_TIMEOUT:
                player = game.player or "The player"
                self.games[key] = Game()
                self.say(self.channels.get(key, key), f"{player}'s difficulty selection expired. Start again with !sudoku.")
