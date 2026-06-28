"""
game_state.py — Parses Dota 2 GSI JSON into structured game state.

Mirrors the C++ GameState.h / GameState.cpp logic exactly:
  - Two-method hero detection: draft section (Captain's Mode) then
    allplayers fallback (All Pick / Turbo).
  - Phase detection from map.game_state.
  - Team identification from player.team_name.
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
        """Parse the raw Dota game_state string."""
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
    """
    Full game state parsed from one GSI JSON message.

    Usage:
        gs = GameState()
        gs.update_from_json_string(raw_json)
        if gs.is_in_draft():
            for hid in gs.get_enemy_hero_ids():
                ...
    """

    phase: GamePhase = GamePhase.NONE
    team_id: int = 0          # 2=Radiant, 3=Dire
    match_time: int = 0
    ally_heroes: list[DraftHero] = field(default_factory=list)
    enemy_heroes: list[DraftHero] = field(default_factory=list)
    banned_heroes: list[DraftHero] = field(default_factory=list)

    # ── public API (mirrors C++ GameState.h) ──────────────────────

    def get_phase(self) -> GamePhase:
        return self.phase

    def get_game_phase(self) -> str:
        """Return phase as a string (matches C++ GetGamePhase())."""
        return self.phase.value

    def is_in_draft(self) -> bool:
        """True during hero_selection or strategy_time."""
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
        """Return flat list of enemy hero IDs (mirrors C++ GetEnemyHeroIds())."""
        return [h.hero_id for h in self.enemy_heroes if h.hero_id > 0]

    # ── parsing (mirrors C++ UpdateFromJsonString) ────────────────

    def update_from_json_string(self, json_str: str) -> None:
        """Parse one GSI POST body and update all fields."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return

        # ── "map" section ─────────────────────────────────────
        if "map" in data:
            map_data = data["map"]

            if "game_state" in map_data:
                self.phase = GamePhase.from_dota_state(map_data["game_state"])

            if "clock_time" in map_data:
                self.match_time = map_data["clock_time"]

        # ── "player" section ──────────────────────────────────
        if "player" in data:
            player = data["player"]
            if "team_name" in player:
                self.team_id = 2 if player["team_name"] == "radiant" else 3

        # ── Clear old hero data ───────────────────────────────
        self.ally_heroes.clear()
        self.enemy_heroes.clear()
        self.banned_heroes.clear()

        got_heroes_from_draft = False
        ally_team_str = str(self.team_id)           # "2" or "3"
        enemy_team_str = str(3 if self.team_id == 2 else 2)

        # ── METHOD 1: "draft" section (Captain's Mode) ────────
        # Actual GSI structure: draft → Teams → {team: {PickIDs: {slot: id}}}
        draft = data.get("draft", {})
        draft_teams = draft.get("Teams", {}) if isinstance(draft, dict) else {}

        # Fallback: old-style flat draft keys (some versions may use this)
        if not draft_teams and isinstance(draft, dict):
            old_ally = draft.get(f"team{self.team_id}", {})
            old_enemy = draft.get(f"team{3 if self.team_id == 2 else 2}", {})
            if old_ally or old_enemy:
                draft_teams = {ally_team_str: old_ally, enemy_team_str: old_enemy}

        if draft_teams:
            for team_str, team_data in draft_teams.items():
                if not isinstance(team_data, dict):
                    continue
                is_ally = (team_str == ally_team_str)
                target = self.ally_heroes if is_ally else self.enemy_heroes

                # New format: PickIDs = { "0": hero_id, "1": hero_id, ... }
                pick_ids = team_data.get("PickIDs", {})
                pick_names = team_data.get("PickHeroIDs", {})
                if isinstance(pick_ids, dict):
                    for slot_str, hid in pick_ids.items():
                        if isinstance(hid, (int, float)) and int(hid) > 0:
                            target.append(DraftHero(
                                hero_id=int(hid),
                                hero_name=str(pick_names.get(slot_str, "")),
                                is_picked=True,
                            ))

                # Also check old flat format: pick0_id, pick1_id, ...
                for i in range(5):
                    hid = team_data.get(f"pick{i}_id", 0)
                    if isinstance(hid, (int, float)) and int(hid) > 0:
                        hname = team_data.get(f"pick{i}_class", "")
                        # Avoid duplicates if PickIDs also parsed
                        if not any(h.hero_id == int(hid) for h in target):
                            target.append(DraftHero(
                                hero_id=int(hid),
                                hero_name=str(hname),
                                is_picked=True,
                            ))

                # Parse bans: both new (BanIDs) and old (ban0_id) formats
                ban_ids = team_data.get("BanIDs", {})
                ban_names = team_data.get("BanHeroIDs", {})
                if isinstance(ban_ids, dict):
                    for slot_str, hid in ban_ids.items():
                        if isinstance(hid, (int, float)) and int(hid) > 0:
                            self.banned_heroes.append(DraftHero(
                                hero_id=int(hid),
                                hero_name=str(ban_names.get(slot_str, "")),
                                is_banned=True,
                            ))
                for i in range(7):
                    hid = team_data.get(f"ban{i}_id", 0)
                    if isinstance(hid, (int, float)) and int(hid) > 0:
                        if not any(h.hero_id == int(hid) for h in self.banned_heroes):
                            self.banned_heroes.append(DraftHero(
                                hero_id=int(hid),
                                hero_name=str(team_data.get(f"ban{i}_class", "")),
                                is_banned=True,
                            ))

            got_heroes_from_draft = bool(self.ally_heroes or self.enemy_heroes)

        # ── METHOD 2: "hero" → Teams (All Pick / Turbo) ─────
        # Dota2GSI docs: hero.Teams maps team → player_id → {ID, Name, ...}
        if not got_heroes_from_draft:
            hero_data = data.get("hero", {})
            hero_teams = hero_data.get("Teams", {}) if isinstance(hero_data, dict) else {}
            if hero_teams:
                for team_str, players in hero_teams.items():
                    if not isinstance(players, dict):
                        continue
                    is_ally = (team_str == ally_team_str)
                    target = self.ally_heroes if is_ally else self.enemy_heroes
                    for _player_id, pinfo in players.items():
                        if not isinstance(pinfo, dict):
                            continue
                        hid = pinfo.get("ID", 0)
                        if isinstance(hid, (int, float)) and int(hid) > 0:
                            target.append(DraftHero(
                                hero_id=int(hid),
                                hero_name=str(pinfo.get("Name", "")),
                                is_picked=True,
                            ))

            if not got_heroes_from_draft:
                # Last resort: RadiantTeamDetails / DireTeamDetails
                for ts, is_ally in [(str(self.team_id), True), (enemy_team_str, False)]:
                    key = f"{'Radiant' if ts == '2' else 'Dire'}TeamDetails"
                    td = data.get(key, {})
                    if isinstance(td, dict):
                        players = td.get("Players", {})
                        if isinstance(players, dict):
                            target = self.ally_heroes if is_ally else self.enemy_heroes
                            for _pid, pinfo in players.items():
                                if isinstance(pinfo, dict):
                                    hid = pinfo.get("HeroID", pinfo.get("hero_id", 0))
                                    if isinstance(hid, (int, float)) and int(hid) > 0:
                                        target.append(DraftHero(
                                            hero_id=int(hid),
                                            hero_name=str(pinfo.get("HeroName", pinfo.get("hero_name", ""))),
                                            is_picked=True,
                                        ))

    def reset(self) -> None:
        """Clear all state (mirrors C++ Reset())."""
        self.phase = GamePhase.NONE
        self.team_id = 0
        self.match_time = 0
        self.ally_heroes.clear()
        self.enemy_heroes.clear()
        self.banned_heroes.clear()
