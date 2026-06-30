# server.py - Dota 2 Counter Helper (Python + Flask)
# Port 3001 = GSI receiver, Port 3002 = Web UI + API

import json, webbrowser, atexit
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from gsi import GSIServer

BASE = Path(__file__).parent
DATA = BASE / "data"
WEB = BASE / "web"

# ── Auto-install GSI config to Dota 2 directory ─────────────────
def install_gsi_config():
    """Auto-deploy GSI .cfg to Dota 2 folder (like the C++ version)."""
    import subprocess, os

    cfg_src = BASE / "gsi-config" / "gamestate_integration_counter_helper.cfg"
    cfg_content = cfg_src.read_text(encoding="utf-8")

    steam_paths = []

    # 1. Read Steam path from Windows registry
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            p = winreg.QueryValueEx(k, "SteamPath")[0].replace("/", "\\")
            steam_paths.append(p)
            print(f"[GSI] Steam from registry: {p}")
    except Exception as e:
        print(f"[GSI] Registry read failed: {e}")

    # 2. Parse libraryfolders.vdf for additional Steam libraries
    for base in list(steam_paths):
        vdf = Path(base) / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                for line in vdf.read_text(encoding="utf-8").splitlines():
                    if '"path"' in line:
                        p = line.split('"')[3].replace("\\\\", "\\")
                        if p not in steam_paths:
                            steam_paths.append(p)
                            print(f"[GSI] Steam library from VDF: {p}")
            except Exception as e:
                print(f"[GSI] VDF parse failed: {e}")

    # 3. Add hardcoded fallback paths (common Dota install locations)
    for fb in [r"D:\SteamLibrary", r"D:\Steam", r"C:\Program Files (x86)\Steam",
               r"C:\Steam", r"E:\SteamLibrary", r"E:\Steam", r"F:\Steam"]:
        if Path(fb).exists() and fb not in steam_paths:
            steam_paths.append(fb)
            print(f"[GSI] Fallback path: {fb}")

    # 4. Search for Dota 2 GSI folder in each library + FORCE overwrite
    for lib in steam_paths:
        gsi_dir = Path(lib) / "steamapps" / "common" / "dota 2 beta" / "game" / "dota" / "cfg" / "gamestate_integration"
        if gsi_dir.parent.exists():
            gsi_dir.mkdir(parents=True, exist_ok=True)
            dest = gsi_dir / "gamestate_integration_counter_helper.cfg"
            old = dest.read_text(encoding="utf-8") if dest.exists() else ""
            dest.write_text(cfg_content, encoding="utf-8")
            tag = "UPDATED" if old else "INSTALLED"
            print(f"[GSI] Config {tag}: {dest}")
            if old and old != cfg_content:
                has_ap = '"allplayers"' in old
                print(f"[GSI]   Old had allplayers={has_ap}, missing_draft={'"draft"' not in old} → overwritten")
            return str(dest)

    # 5. Last resort: brute-force scan for dota 2 beta folder
    print("[GSI] Searching disk for dota 2 beta...")
    for drive in [Path("D:/"), Path("C:/"), Path("E:/"), Path("F:/")]:
        if not drive.exists():
            continue
        for root in [drive, drive / "SteamLibrary", drive / "Steam", drive / "Program Files (x86)" / "Steam",
                     drive / "Games" / "Steam"]:
            dota_cfg = root / "steamapps" / "common" / "dota 2 beta" / "game" / "dota" / "cfg"
            if dota_cfg.exists():
                gsi_dir = dota_cfg / "gamestate_integration"
                gsi_dir.mkdir(parents=True, exist_ok=True)
                dest = gsi_dir / "gamestate_integration_counter_helper.cfg"
                old = dest.read_text(encoding="utf-8") if dest.exists() else ""
                dest.write_text(cfg_content, encoding="utf-8")
                tag = "UPDATED" if old else "INSTALLED"
                print(f"[GSI] Config {tag} (brute-force): {dest}")
                return str(dest)

    print("[GSI] WARNING: Could not find Dota 2 directory. Copy gsi-config/*.cfg manually.")
    return None

install_gsi_config()

# Load heroes from STRATZ format
hero_db = []
hero_by_id = {}
support_heroes = set()  # hero IDs with SUPPORT role
with open(DATA / "heroes.json", encoding="utf-8") as f:
    hero_db = json.load(f)
    for h in hero_db:
        hero_by_id[h["id"]] = h
        if any(r.get("roleId") == "SUPPORT" for r in h.get("roles", [])):
            support_heroes.add(h["id"])
print(f"Loaded {len(hero_db)} heroes ({len(support_heroes)} supports)")

# Load counter data from matchups.json
# counter_map[target_hero_id] = {counter_hero_id: {advantage, win_rate}}
# "target_hero_id" is the hero being countered; "counter_hero_id" is the hero that counters it.
# Uses dict-of-dicts to avoid double-counting (each hero pair is seen twice).
counter_map = {}
synergy_map = {}  # synergy_map[ally_id] = {partner_id: {advantage: synergy, win_rate}}
def load_matchups():
    global counter_map, synergy_map
    try:
        with open(DATA / "matchups.json") as f:
            raw = json.load(f)
        for hid_str, m in raw.get("data", {}).items():
            hid = int(hid_str)
            for vs in m.get("vs", []):
                if vs.get("matchCount", 0) < 50:
                    continue
                synergy = float(vs.get("synergy", 0))
                wr = float(vs.get("winsAverage", 0.5)) * 100  # hid's win rate vs heroId2
                if synergy < 0:  # heroId2 counters hid
                    advantage = round(abs(synergy), 1)
                    inner = counter_map.setdefault(hid, {})
                    inner[vs["heroId2"]] = {"advantage": advantage, "win_rate": round(100 - wr, 1)}
                elif synergy > 0:  # hid counters heroId2
                    advantage = round(synergy, 1)
                    inner = counter_map.setdefault(vs["heroId2"], {})
                    inner[hid] = {"advantage": advantage, "win_rate": round(wr, 1)}
            for w in m.get("with", []):
                if w.get("matchCount", 0) < 50:
                    continue
                s = float(w.get("synergy", 0))
                wr2 = float(w.get("winsAverage", 0.5)) * 100
                if s > 0:  # positive synergy = good teammate
                    inner = synergy_map.setdefault(hid, {})
                    inner[w["heroId2"]] = {"advantage": round(s, 1), "win_rate": round(wr2, 1)}
        print(f"Counter data: {len(counter_map)} heroes, synergy: {len(synergy_map)}")
    except Exception as e:
        print(f"Counter data not loaded: {e}")
load_matchups()

# ── Manual hero overrides (click-to-add) ────────────────────────────
# Merged on top of GSI-detected heroes so automatic (Captain's Mode
# draft) and manual click entry work at the same time. `hidden_*` holds
# heroes the user explicitly removed, so a wrong/unwanted GSI detection
# stays gone instead of reappearing on the next GSI push.
manual_enemies, manual_allies, manual_bans = [], [], []
hidden_enemies, hidden_allies, hidden_bans = set(), set(), set()
my_team = 0

def _edit_add(manual: list, hidden: set, hid: int, limit) -> None:
    hidden.discard(hid)
    if hid not in manual and (limit is None or len(manual) < limit):
        manual.append(hid)

def _edit_remove(manual: list, hidden: set, hid: int) -> None:
    if hid in manual:
        manual.remove(hid)
    hidden.add(hid)  # also suppress it if it came from GSI

def hero_json(hid, banned=False):
    info = hero_by_id.get(hid, {})
    attr_raw = ((info.get("stats") or {}).get("primaryAttributeEnum") or
               info.get("primaryAttribute") or info.get("primary_attr") or "STRENGTH")
    attr = attr_raw.lower()[:3]
    if attr == "all": attr = "uni"
    if attr not in ("str", "agi", "int", "uni"): attr = "str"
    return {
        "heroId": hid, "isBanned": banned,
        "localizedName": info.get("displayName", ""),
        "localizedNameZh": info.get("displayNameZh") or "",
        "attribute": attr
    }

def get_suggestions(enemy_ids, banned_ids, max_n=10):
    if not enemy_ids: return []
    scores, banned_set, enemy_set = {}, set(banned_ids), set(enemy_ids)
    for eid in enemy_ids:
        weight = 0.4 if eid in support_heroes else 1.0  # countering a support is less valuable
        for hid, c in counter_map.get(eid, {}).items():
            if hid in enemy_set or hid in banned_set: continue
            s = scores.setdefault(hid, {"heroId": hid, "score": 0, "winRate": 0, "n": 0})
            s["score"] += c["advantage"] * weight
            s["winRate"] += c["win_rate"]
            s["n"] += 1
    result = [{"heroId": v["heroId"], "score": v["score"], "winRate": v["winRate"]/v["n"]} for v in scores.values()]
    result.sort(key=lambda x: -x["score"])
    return result[:max_n]

def get_ally_suggestions(ally_ids, banned_ids, max_n=10):
    if not ally_ids: return []
    scores, banned_set, ally_set = {}, set(banned_ids), set(ally_ids)
    for aid in ally_ids:
        for hid, c in synergy_map.get(aid, {}).items():
            if hid in ally_set or hid in banned_set: continue
            s = scores.setdefault(hid, {"heroId": hid, "score": 0, "winRate": 0, "n": 0})
            s["score"] += c["advantage"]
            s["winRate"] += c["win_rate"]
            s["n"] += 1
    result = [{"heroId": v["heroId"], "score": v["score"], "winRate": v["winRate"]/v["n"]} for v in scores.values()]
    result.sort(key=lambda x: -x["score"])
    return result[:max_n]

def get_ban_suggestions(ally_ids, banned_ids, max_n=10):
    if not ally_ids: return []
    scores, banned_set, ally_set = {}, set(banned_ids), set(ally_ids)
    for aid in ally_ids:
        weight = 0.4 if aid in support_heroes else 1.0  # banning a support counter is less urgent
        for hid, c in counter_map.get(aid, {}).items():
            if hid in ally_set or hid in banned_set: continue
            s = scores.setdefault(hid, {"heroId": hid, "score": 0, "winRate": 0, "n": 0})
            s["score"] += c["advantage"] * weight
            s["winRate"] += c["win_rate"]
            s["n"] += 1
    result = [{"heroId": v["heroId"], "score": v["score"], "winRate": v["winRate"]/v["n"]} for v in scores.values()]
    result.sort(key=lambda x: -x["score"])
    return result[:max_n]

# GSI server on port 3001 (mirrors C++ GSIServer)
gsi_server = GSIServer()
gsi_server.start()

def _sync_team_from_gsi() -> None:
    """Keep global my_team in sync with latest GSI state (for counter logic)."""
    global my_team
    gs = gsi_server.get_current_state()
    if gs.team_id:
        my_team = gs.team_id

# Web server on port 3002
app = Flask(__name__)
CORS(app)

def parse_gs():
    """Parse current GSI state into phase + hero lists.
    Delegates to the gsi module (mirrors C++ GameState::UpdateFromJsonString)."""
    _sync_team_from_gsi()
    gs = gsi_server.get_current_state()

    phase = gs.get_game_phase()
    allies = [{"heroId": h.hero_id} for h in gs.get_ally_heroes()]
    enemies = [{"heroId": h.hero_id} for h in gs.get_enemy_heroes()]
    bans = [{"heroId": h.hero_id, "isBanned": True} for h in gs.get_banned_heroes()]

    return phase, allies, enemies, bans

def _merge(gsi_ids, manual_ids, hidden) -> list:
    """GSI-detected heroes first, then manual additions; minus removed (hidden)."""
    out = []
    for hid in list(gsi_ids) + list(manual_ids):
        if hid and hid not in hidden and hid not in out:
            out.append(hid)
    return out

def _build_state() -> dict:
    """Build the full /api/state response dict. Shared by poll + SSE.

    Hero lists are GSI-detected heroes (Captain's Mode draft) merged with
    manual click additions — both work simultaneously, no live/test toggle."""
    phase, allies, enemies, bans = parse_gs()
    eids = _merge((h["heroId"] for h in enemies), manual_enemies, hidden_enemies)
    aids = _merge((h["heroId"] for h in allies), manual_allies, hidden_allies)
    bids = _merge((h["heroId"] for h in bans), manual_bans, hidden_bans)
    hero_db_json = [{"id": h["id"], "localizedName": h.get("displayName",""),
        "localizedNameZh": h.get("displayNameZh",""),
        "attribute": (((h.get("stats")or{}).get("primaryAttributeEnum") or "UNIVERSAL").lower()[:3] or "uni").replace("all","uni")} for h in hero_db]
    return {
        "phase": phase, "isInDraft": phase in ("hero_selection","strategy_time"),
        "teamId": my_team, "matchTime": gsi_server.get_current_state().match_time,
        "draftActivity": {"active": False, "actingTeam": 0, "action": "none"},
        "bannedHeroes": [hero_json(x, True) for x in bids],
        "allyHeroes": [hero_json(x) for x in aids],
        "enemyHeroes": [hero_json(x) for x in eids],
        "heroDatabase": hero_db_json,
        "suggestions": get_suggestions(eids, bids),
        "banSuggestions": get_ban_suggestions(aids, bids),
        "allySuggestions": get_ally_suggestions(aids, bids),
    }


@app.route("/api/raw_gs")
def api_raw_gs():
    """Debug: return current parsed GSI state as JSON."""
    gs = gsi_server.get_current_state()
    return jsonify({
        "phase": gs.get_game_phase(),
        "teamId": gs.team_id,
        "matchTime": gs.match_time,
        "allyHeroes": [{"heroId": h.hero_id, "heroName": h.hero_name, "isPicked": h.is_picked} for h in gs.get_ally_heroes()],
        "enemyHeroes": [{"heroId": h.hero_id, "heroName": h.hero_name, "isPicked": h.is_picked} for h in gs.get_enemy_heroes()],
        "bannedHeroes": [{"heroId": h.hero_id, "heroName": h.hero_name, "isBanned": h.is_banned} for h in gs.get_banned_heroes()],
    })


@app.route("/api/state")
def api_state():
    return jsonify(_build_state())


@app.route("/api/stream")
def api_stream():
    """SSE endpoint — pushes state only when GSI data changes."""
    import traceback as _tb
    def generate():
        data_event = gsi_server.get_data_event()
        last_sent = None
        try:
            # Send initial state immediately (don't wait for first GSI event)
            state = _build_state()
            last_sent = json.dumps(state, ensure_ascii=False)
            yield f"data: {last_sent}\n\n"

            while True:
                # Wait for new GSI data, with periodic heartbeat
                data_event.wait(timeout=15.0)
                data_event.clear()

                try:
                    state = _build_state()
                    state_json = json.dumps(state, ensure_ascii=False)

                    if state_json == last_sent:
                        yield ": heartbeat\n\n"
                        continue

                    last_sent = state_json
                    yield f"data: {state_json}\n\n"
                except Exception as inner_e:
                    print(f"[SSE] Error building state: {inner_e}")
                    _tb.print_exc()
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass  # client disconnected — normal
        except Exception as e:
            print(f"[SSE] Fatal: {e}")
            _tb.print_exc()

    return app.response_class(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

@app.route("/api/edit/<action>")
def api_edit(action):
    """Manual hero edits, merged with GSI — both active at once (no live/test toggle)."""
    hid = request.args.get("id", 0, type=int)
    changed = True
    if action == "clear":
        for lst in (manual_enemies, manual_allies, manual_bans): lst.clear()
        for s in (hidden_enemies, hidden_allies, hidden_bans): s.clear()
    elif action == "enemy_add" and hid:    _edit_add(manual_enemies, hidden_enemies, hid, 5)
    elif action == "enemy_remove" and hid: _edit_remove(manual_enemies, hidden_enemies, hid)
    elif action == "ally_add" and hid:     _edit_add(manual_allies, hidden_allies, hid, 5)
    elif action == "ally_remove" and hid:  _edit_remove(manual_allies, hidden_allies, hid)
    elif action == "ban" and hid:          _edit_add(manual_bans, hidden_bans, hid, None)
    elif action == "unban" and hid:        _edit_remove(manual_bans, hidden_bans, hid)
    else: changed = False
    if changed:
        gsi_server.get_data_event().set()  # wake SSE to push updated state
    return jsonify({"ok": True})

@app.route("/", defaults={"path": "index.html"})
@app.route("/<path:path>")
def serve(path):
    # no-store: always serve fresh HTML/JS/CSS so edits take effect without a hard refresh
    resp = send_from_directory(WEB, path)
    resp.headers["Cache-Control"] = "no-store"
    return resp

# ── Graceful shutdown ──────────────────────────────────────────────

def cleanup():
    """Release GSI server port. Called on any exit path."""
    print("\n[Shutdown] Stopping GSI server...")
    try:
        gsi_server.stop()
    except Exception:
        pass
    print("[Shutdown] All servers stopped. Ports released.")

atexit.register(cleanup)

if __name__ == "__main__":
    print("GSI + Web UI on http://127.0.0.1:3002")
    webbrowser.open("http://127.0.0.1:3002")
    try:
        app.run(host="127.0.0.1", port=3002, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()
