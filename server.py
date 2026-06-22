# server.py - Dota 2 Counter Helper (Python + Flask)
# Port 3001 = GSI receiver, Port 3002 = Web UI + API

import json, webbrowser
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE = Path(__file__).parent
DATA = BASE / "data"
WEB = BASE / "web"

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

test_mode = False
test_enemies, test_allies, test_bans = [], [], []
latest_gs = {}
my_team = 0

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

# GSI receiver on port 3001
class GSIHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            global latest_gs, my_team
            data = json.loads(self.rfile.read(length))
            latest_gs = data
            if "player" in data and "team_name" in data["player"]:
                my_team = 2 if data["player"]["team_name"] == "radiant" else 3
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a): pass

def start_gsi():
    HTTPServer(("127.0.0.1", 3001), GSIHandler).serve_forever()
Thread(target=start_gsi, daemon=True).start()
print("GSI receiver on http://127.0.0.1:3001")

# Web server on port 3002
app = Flask(__name__)
CORS(app)

def parse_gs():
    gs = latest_gs or {}
    gm = gs.get("map", {})
    phase = {"DOTA_GAMERULES_STATE_HERO_SELECTION": "hero_selection",
             "DOTA_GAMERULES_STATE_STRATEGY_TIME": "strategy_time",
             "DOTA_GAMERULES_STATE_PRE_GAME": "pre_game",
             "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS": "playing",
             "DOTA_GAMERULES_STATE_POST_GAME": "postgame"}.get(gm.get("game_state",""), "none")
    draft = gs.get("draft", {})
    atk, etk = ("team2","team3") if my_team==2 else ("team3","team2")
    allies, enemies, bans = [], [], []
    for i in range(5):
        for arr, tk in [(allies, atk), (enemies, etk)]:
            pid = draft.get(tk, {}).get(f"pick{i}_id", 0)
            if pid > 0: arr.append({"heroId": pid})
    for tk in (atk, etk):
        for i in range(7):
            bid = draft.get(tk, {}).get(f"ban{i}_id", 0)
            if bid > 0: bans.append({"heroId": bid, "isBanned": True})
    return phase, allies, enemies, bans

@app.route("/api/state")
def api_state():
    phase, allies, enemies, bans = parse_gs()
    eids = test_enemies if test_mode else [h["heroId"] for h in enemies]
    aids = test_allies if test_mode else [h["heroId"] for h in allies]
    bids = test_bans if test_mode else [h["heroId"] for h in bans]
    return jsonify({
        "testMode": test_mode,
        "phase": phase, "isInDraft": phase in ("hero_selection","strategy_time"),
        "teamId": my_team, "matchTime": (latest_gs or {}).get("map",{}).get("clock_time",0),
        "draftActivity": {"active": False, "actingTeam": 0, "action": "none"},
        "bannedHeroes": [hero_json(x,True) for x in bids] if test_mode else bans,
        "allyHeroes": [hero_json(x) for x in aids] if test_mode else allies,
        "enemyHeroes": [hero_json(x) for x in eids] if test_mode else enemies,
        "heroDatabase": [{"id": h["id"], "localizedName": h.get("displayName",""),
            "localizedNameZh": h.get("displayNameZh",""),
            "attribute": (((h.get("stats")or{}).get("primaryAttributeEnum") or "UNIVERSAL").lower()[:3] or "uni").replace("all","uni")} for h in hero_db],
        "suggestions": get_suggestions(eids, bids),
        "banSuggestions": get_ban_suggestions(aids, bids),
        "allySuggestions": get_ally_suggestions(aids, bids),
    })

@app.route("/api/test/<action>")
def api_test(action):
    global test_mode, test_enemies, test_allies, test_bans
    hid = request.args.get("id", 0, type=int)
    if action == "toggle":
        test_mode = not test_mode
        if not test_mode: test_enemies, test_allies, test_bans = [], [], []
    elif action == "clear": test_enemies, test_allies, test_bans = [], [], []
    elif action == "enemy_add" and hid and len(test_enemies)<5 and hid not in test_enemies: test_enemies.append(hid)
    elif action == "enemy_remove" and hid: test_enemies = [x for x in test_enemies if x!=hid]
    elif action == "ally_add" and hid and len(test_allies)<5 and hid not in test_allies: test_allies.append(hid)
    elif action == "ally_remove" and hid: test_allies = [x for x in test_allies if x!=hid]
    elif action == "ban" and hid and hid not in test_bans: test_bans.append(hid)
    elif action == "unban" and hid: test_bans = [x for x in test_bans if x!=hid]
    return jsonify({"ok": True})

@app.route("/", defaults={"path": "index.html"})
@app.route("/<path:path>")
def serve(path):
    return send_from_directory(WEB, path)

if __name__ == "__main__":
    print("Web UI on http://127.0.0.1:3002")
    webbrowser.open("http://127.0.0.1:3002")
    app.run(host="127.0.0.1", port=3002, debug=False)
