# sync_data.py - sync hero/counter data from STRATZ GraphQL

import json, time, urllib.request
from pathlib import Path

BASE = Path(__file__).parent
DATA = BASE / "data"

def _load_env():
    """Load .env file (KEY=VALUE per line, ignore comments and blank lines)."""
    env = {}
    with open(BASE / ".env") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env

env = _load_env()
HEADERS = {"Content-Type": "application/json", "Accept": "application/json",
           "Authorization": f"Bearer {env['STRATZ_TOKEN']}", "User-Agent": "STRATZ_API"}

def graphql(query):
    req = urllib.request.Request(env["STRATZ_API_URL"],
        data=json.dumps({"query": query}).encode(), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def sync_heroes():
    print("Fetching heroes from STRATZ...")
    data = graphql("{ constants { heroes { id name displayName roles { roleId level } stats { primaryAttributeEnum } } } }")
    if "errors" in data:
        for e in data["errors"]: print(f"  Error: {e['message']}")
        return
    heroes = data["data"]["constants"]["heroes"]
    print(json.dumps(heroes[:2], ensure_ascii=False, indent=2))

    corrections = {
        "cristalmaiden": "crystal maiden", "ogre maci": "ogre magi",
        "knight stalker": "night stalker", "skywrace mage": "skywrath mage",
        "broodmather": "broodmother", "abadon": "abaddon",
        "nature`s prophet": "nature's prophet",
        "spiritbreaker": "spirit breaker", "trollwarlord": "troll warlord",
        "drowranger": "drow ranger", "stormspirit": "storm spirit",
        "emberspirit": "ember spirit", "earthspirit": "earth spirit",
        "legioncommander": "legion commander", "bountyhunter": "bounty hunter",
        "facelessvoid": "faceless void", "deathprophet": "death prophet",
        "witchdoctor": "witch doctor", "darkseer": "dark seer",
        "arcwarden": "arc warden", "darkwillow": "dark willow",
        "winterwyvern": "winter wyvern", "eldertitan": "elder titan",
        "life stealer": "lifestealer", "terror blade": "terrorblade",
        "omni knight": "omniknight",
    }
    zh_map = {}
    with open(DATA / "chinese_names.txt", encoding="utf-8") as f:
        for line in f:
            if "\t" not in line: continue
            eng, chn = line.strip().split("\t", 1)
            eng = corrections.get(eng.lower().strip(), eng.lower().strip())
            zh_map[eng] = chn.strip()

    matched = 0
    for h in heroes:
        key = h["displayName"].lower()
        if key in zh_map:
            h["displayNameZh"] = zh_map[key]
            matched += 1

    with open(DATA / "heroes.json", "w", encoding="utf-8") as f:
        json.dump(heroes, f, ensure_ascii=False, indent=2)
    print(f"Heroes saved: {len(heroes)} total, {matched} with Chinese names -> data/heroes.json")


def sync_counters():
    with open(DATA / "heroes.json", encoding="utf-8") as f:
        heroes = json.load(f)
    ids = [h["id"] for h in heroes if 0 < h["id"] < 200]
    print(f"{len(ids)} heroes, fetching counter data...")

    # Use matchUp (not heroVsHeroMatchup) — returns HeroDryadType with real matchup win rates.
    # winsAverage = winCount / matchCount = heroId1's actual win rate against/with heroId2.
    # winRateHeroId1/winRateHeroId2 from heroVsHeroMatchup are overall win rate constants (wrong).
    QUERY = """{ heroStats { matchUp(heroId: HID take:200 matchLimit:10) {
        heroId  matchCountVs  matchCountWith
        vs   { heroId2  winCount  matchCount  winsAverage  synergy }
        with { heroId2  winCount  matchCount  winsAverage  synergy }
    } } }"""

    raw = {}
    for i, hero_id in enumerate(ids):
        q = QUERY.replace("HID", str(hero_id))
        for retry in range(3):
            try:
                data = graphql(q)
                matches = data.get("data", {}).get("heroStats", {}).get("matchUp", [])
                if matches:
                    m = matches[0]  # single HeroDryadType entry per hero
                    raw[str(hero_id)] = {"vs": m.get("vs", []), "with": m.get("with", [])}
                break
            except Exception as e:
                if retry < 2: time.sleep(1)
                else: print(f"\n  Hero {hero_id} failed: {e}")
        print(f"\r{i+1}/{len(ids)}", end="", flush=True)
        time.sleep(0.5)

    out = {"_source": "STRATZ", "_time": time.strftime("%Y-%m-%d %H:%M:%S"), "data": raw}
    with open(DATA / "matchups.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nMatchups saved: {len(raw)} heroes -> data/matchups.json")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("all", "heroes"): sync_heroes()
    if cmd in ("all", "counters"): sync_counters()
