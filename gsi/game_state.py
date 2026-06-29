"""
game_state.py — Parses Dota 2 GSI JSON into structured game state.

Captain's Mode draft parsing from GSI "draft" section.
Phase detection from map.game_state.
Team identification from player.team_name.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import json


class GamePhase(Enum):
    """Maps DOTA_GAMERULES_STATE_* strings to internal phases."""
    NONE = "none"
    HERO_SELECTION = "hero_selection"
    STRATEGY_TIME = "strategy_time"
    PRE_GAME = "pre_game"
    PLAYING = "playing"
    POST_GAME = "post_game"

    @classmethod
    def from_dota_state(cls, state: str) -> "GamePhase":
        return {
            "DOTA_GAMERULES_STATE_HERO_SELECTION": cls.HERO_SELECTION,
            "DOTA_GAMERULES_STATE_STRATEGY_TIME": cls.STRATEGY_TIME,
            "DOTA_GAMERULES_STATE_PRE_GAME": cls.PRE_GAME,
            "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS": cls.PLAYING,
            "DOTA_GAMERULES_STATE_POST_GAME": cls.POST_GAME,
        }.get(state, cls.NONE)


@dataclass
class DraftHero:
    """A single hero in the draft (picked or banned)."""
    hero_id: int = 0
    hero_name: str = ""
    is_picked: bool = False
    is_banned: bool = False


@dataclass
class GameState:
    """Full game state parsed from one GSI JSON message."""

    phase: GamePhase = GamePhase.NONE
    team_id: int = 0          # 2=Radiant, 3=Dire
    match_time: int = 0
    ally_heroes: list[DraftHero] = field(default_factory=list)
    enemy_heroes: list[DraftHero] = field(default_factory=list)
    banned_heroes: list[DraftHero] = field(default_factory=list)

    # ── public API ───────────────────────────────────────────────

    def get_phase(self) -> GamePhase:
        return self.phase

    def get_game_phase(self) -> str:
        return self.phase.value

    def is_in_draft(self) -> bool:
        return self.phase in (GamePhase.HERO_SELECTION, GamePhase.STRATEGY_TIME)

    def get_team_id(self) -> int:
        return self.team_id

    def get_match_time(self) -> int:
        return self.match_time

    def get_ally_heroes(self) -> list[DraftHero]:
        return self.ally_heroes

    def get_enemy_heroes(self) -> list[DraftHero]:
        return self.enemy_heroes

    def get_banned_heroes(self) -> list[DraftHero]:
        return self.banned_heroes

    def get_enemy_hero_ids(self) -> list[int]:
        return [h.hero_id for h in self.enemy_heroes if h.hero_id > 0]

    # ── parsing ──────────────────────────────────────────────────

    def update_from_json_string(self, json_str: str) -> None:
        """Parse one GSI POST body. Handles both new (PickIDs) and old (pick0_id) draft formats."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return

        # map section
        if "map" in data:
            m = data["map"]
            if "game_state" in m:
                self.phase = GamePhase.from_dota_state(m["game_state"])
            if "clock_time" in m:
                self.match_time = m["clock_time"]

        # player section → team
        if "player" in data:
            tn = data["player"].get("team_name", "")
            if tn:
                self.team_id = 2 if tn == "radiant" else 3

        # clear old data
        self.ally_heroes.clear()
        self.enemy_heroes.clear()
        self.banned_heroes.clear()

        ally_str = str(self.team_id)
        enemy_str = str(3 if self.team_id == 2 else 2)

        # draft section (Captain's Mode)
        draft = data.get("draft", {})
        if not isinstance(draft, dict) or not draft:
            return

        # Try new format: draft → Teams → {team: {PickIDs: {slot: id}}}
        teams = draft.get("Teams", {})
        # Fallback: old flat format draft.team2.pick0_id
        if not teams:
            old_a = draft.get(f"team{self.team_id}", {})
            old_e = draft.get(f"team{3 if self.team_id == 2 else 2}", {})
            if old_a or old_e:
                teams = {ally_str: old_a, enemy_str: old_e}

        for team_str, td in teams.items():
            if not isinstance(td, dict):
                continue
            is_ally = (team_str == ally_str)
            target = self.ally_heroes if is_ally else self.enemy_heroes

            # Picks — new format
            pick_ids = td.get("PickIDs", {})
            pick_names = td.get("PickHeroIDs", {})
            if isinstance(pick_ids, dict):
                for slot, hid in pick_ids.items():
                    if isinstance(hid, (int, float)) and int(hid) > 0:
                        target.append(DraftHero(
                            hero_id=int(hid),
                            hero_name=str(pick_names.get(slot, "")),
                            is_picked=True,
                        ))

            # Picks — old flat format
            for i in range(5):
                hid = td.get(f"pick{i}_id", 0)
                if isinstance(hid, (int, float)) and int(hid) > 0:
                    if not any(h.hero_id == int(hid) for h in target):
                        target.append(DraftHero(
                            hero_id=int(hid),
                            hero_name=str(td.get(f"pick{i}_class", "")),
                            is_picked=True,
                        ))

            # Bans — new format
            ban_ids = td.get("BanIDs", {})
            ban_names = td.get("BanHeroIDs", {})
            if isinstance(ban_ids, dict):
                for slot, hid in ban_ids.items():
                    if isinstance(hid, (int, float)) and int(hid) > 0:
                        self.banned_heroes.append(DraftHero(
                            hero_id=int(hid),
                            hero_name=str(ban_names.get(slot, "")),
                            is_banned=True,
                        ))

            # Bans — old flat format
            for i in range(7):
                hid = td.get(f"ban{i}_id", 0)
                if isinstance(hid, (int, float)) and int(hid) > 0:
                    if not any(h.hero_id == int(hid) for h in self.banned_heroes):
                        self.banned_heroes.append(DraftHero(
                            hero_id=int(hid),
                            hero_name=str(td.get(f"ban{i}_class", "")),
                            is_banned=True,
                        ))

    def reset(self) -> None:
        self.phase = GamePhase.NONE
        self.team_id = 0
        self.match_time = 0
        self.ally_heroes.clear()
        self.enemy_heroes.clear()
        self.banned_heroes.clear()
