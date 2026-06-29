"""
gsi_server.py — HTTP server for Dota 2 Game State Integration.

Listens on 127.0.0.1:3001 for JSON POSTs from Dota 2.
Mirrors the C++ GSIServer.h / GSIServer.cpp logic:
  - POST / handler updates GameState in a thread-safe manner.
  - IsConnected() returns True when data was received within 5 seconds.
  - Mirrors the C++ logging of phase changes, draft start, and
    enemy/ally hero count changes.
"""

import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from .game_state import GameState, GamePhase


class _GSIRequestHandler(BaseHTTPRequestHandler):
    """
    Inner handler — each POST updates the shared GameState on the server.
    We suppress HTTP access logs to match the C++ / no-log behaviour.
    """

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length:
            body = self.rfile.read(content_length).decode("utf-8")
            self.server.gsi_server._handle_request(body)  # type: ignore[attr-defined]
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args) -> None:  # noqa: suppress access logs
        pass


class GSIServer:
    """GSI HTTP server — mirrors C++ GSIServer."""

    PORT = 3001

    def __init__(self) -> None:
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._state_lock = threading.Lock()
        self._game_state = GameState()
        self._last_message_time: float = 0.0
        self._data_event = threading.Event()  # set on each GSI message (for SSE)

        # ── mirrored C++ static-logging guards ──────────────────
        self._first_message = False
        self._draft_logged = False
        self._last_enemy_count: int = -1
        self._last_ally_count: int = -1

    # ── public API ──────────────────────────────────────────

    def start(self, port: int = PORT) -> bool:
        """Start the GSI server on 127.0.0.1:port. Returns True if running."""
        if self._running.is_set():
            return True

        # Attach self to the handler via the HTTPServer instance itself
        self._httpd = HTTPServer(("127.0.0.1", port), _GSIRequestHandler)
        self._httpd.gsi_server = self  # type: ignore[attr-defined]
        self._httpd.timeout = 0.5       # allow periodic self-check

        self._running.set()
        self._thread = threading.Thread(
            target=self._server_thread, daemon=True, name="gsi-server"
        )
        self._thread.start()

        # Give the server time to start (mirrors C++ 100ms sleep)
        time.sleep(0.1)
        return True

    def stop(self) -> None:
        """Shut down the GSI server (mirrors C++ Stop())."""
        if not self._running.is_set():
            return

        self._running.clear()

        # Close the underlying socket immediately to unblock handle_request().
        # This avoids the potential hang of httpd.shutdown() when called from
        # a signal handler or during Flask's own shutdown sequence.
        if self._httpd and self._httpd.socket:
            try:
                self._httpd.socket.close()
            except Exception:
                pass
        if self._httpd:
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def get_current_state(self) -> GameState:
        """Return a copy of the current game state (thread-safe)."""
        with self._state_lock:
            # Return a shallow copy; DraftHero is a dataclass so it's safe
            gs = GameState()
            gs.phase = self._game_state.phase
            gs.team_id = self._game_state.team_id
            gs.match_time = self._game_state.match_time
            gs.ally_heroes = list(self._game_state.ally_heroes)
            gs.enemy_heroes = list(self._game_state.enemy_heroes)
            gs.banned_heroes = list(self._game_state.banned_heroes)
            return gs

    def is_connected(self) -> bool:
        """True if a GSI message was received within the last 5 seconds."""
        now = time.monotonic()
        return (now - self._last_message_time) < 5.0

    def get_data_event(self) -> threading.Event:
        """Event set on each GSI update — SSE endpoint waits on this."""
        return self._data_event

    # ── internals ───────────────────────────────────────────

    def _server_thread(self) -> None:
        """Run the HTTP server loop (mirrors C++ ServerThread)."""
        print("[GSI] Server thread started")
        try:
            while self._running.is_set():
                if self._httpd:
                    self._httpd.handle_request()
        except Exception:
            pass
        print("[GSI] Server thread stopped")

    def _handle_request(self, body: str) -> None:
        """Process one GSI POST payload (mirrors C++ HandleRequest)."""
        self._last_message_time = time.monotonic()

        if not self._first_message:
            self._first_message = True
            print("[GSI] First GSI message received from Dota 2!")

        with self._state_lock:
            old_phase = self._game_state.get_game_phase()
            self._game_state.update_from_json_string(body)
            new_phase = self._game_state.get_game_phase()
            self._data_event.set()  # wake SSE clients

        # ── Log phase change ────────────────────────────────
        if old_phase != new_phase and new_phase != "none":
            print(f"[GSI] Game phase: {new_phase}")

        # ── Log draft start ─────────────────────────────────
        if self._game_state.is_in_draft():
            if not self._draft_logged:
                self._draft_logged = True
                print("[GSI] === DRAFT STARTED! Calculating counter-picks... ===")

            # Log enemy hero count changes
            enemy_ids = self._game_state.get_enemy_hero_ids()
            ally_heroes = self._game_state.get_ally_heroes()

            if len(enemy_ids) != self._last_enemy_count:
                print(f"[GSI]   Enemy heroes: {len(enemy_ids)} picked")
                for hid in enemy_ids:
                    print(f"[GSI]     - Enemy hero ID: {hid}")
                self._last_enemy_count = len(enemy_ids)

            if len(ally_heroes) != self._last_ally_count:
                print(f"[GSI]   Ally heroes: {len(ally_heroes)} picked")
                self._last_ally_count = len(ally_heroes)
        else:
            # Reset draft-logged flag when leaving draft
            self._draft_logged = False
            self._last_enemy_count = -1
            self._last_ally_count = -1
