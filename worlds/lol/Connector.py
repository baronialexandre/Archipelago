import PySimpleGUI as sg
import json
import requests
import os
import ast
import sys
import unicodedata
import threading
from concurrent.futures import ThreadPoolExecutor

###GET VERSION###
def _get_world_version() -> str:
    try:
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, 'archipelago.json'), 'r') as f:
            return json.load(f).get('world_version', '')
    except Exception:
        return ''

_WORLD_VERSION = _get_world_version()
_WINDOW_TITLE = f"LOL AP v{_WORLD_VERSION}" if _WORLD_VERSION else "LOL AP"

###GET CHAMPION DATA###
versions_url = "https://ddragon.leagueoflegends.com/api/versions.json"
most_recent_version = requests.get(versions_url).json()[0]
champions_url = "https://ddragon.leagueoflegends.com/cdn/" + str(most_recent_version) + "/data/en_US/champion.json"
champions = {}
champion_data = requests.get(champions_url).json()["data"]

for champion in list(champion_data.keys()):
    # Riot added "Jade_X" variants (League of Legends Classic) that share the
    # same display name as the original champion but a different numeric key.
    # Skip them so name->id lookups don't resolve to the wrong id.
    if champion.startswith("Jade_"):
        continue
    champions[int(champion_data[champion]["key"])] = champion_data[champion]

###SET GLOBAL VARIABLES###
url = "https://127.0.0.1:2999/liveclientdata/allgamedata"
unlocked_champion_ids = []
in_match = False
tracked_teammates = set()
game_values = {
    "required_assists": 0,
    "required_cs"     : 0,
    "required_kills"  : 0,
    "required_lp"     : 0,
    "required_vs"     : 0,
    "current_lp"      : 0,
    "starting_champions": 0,
    "enabled_checks"  : None,
    "support_special_treatment": True
}

###SET UP GAME COMMUNICATION PATH###
if "localappdata" in os.environ:
    game_communication_path = os.path.expandvars(r"%localappdata%/LOLAP")
else:
    game_communication_path = os.path.expandvars(r"$HOME/LOLAP")
if not os.path.exists(game_communication_path):
    os.makedirs(game_communication_path)


def _read_auto_select_teammates() -> bool:
    path = os.path.join(game_communication_path, "Auto_Select_Teammates.cfg")
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                return bool(int(f.read().strip() or "0"))
    except Exception:
        pass
    return False


def _write_auto_select_teammates(enabled: bool) -> None:
    try:
        with open(os.path.join(game_communication_path, "Auto_Select_Teammates.cfg"), 'w') as f:
            f.write("1" if enabled else "0")
    except Exception:
        pass


auto_select_teammates = _read_auto_select_teammates()


###DEFINE FUNCTIONS###
def get_game_data():
    """Returns (status, data) where status is one of:
      'api_down'     - League client not running / not in a game
      'loading'      - API up but game hasn't started yet (no events)
      'in_game'      - Active game in progress
      'game_over'    - GameEnd event received
    """
    try:
        data = requests.get(url, verify=False, timeout=1).json()
        if "activePlayer" not in data or "allPlayers" not in data:
            return "api_down", None
        events = data.get("events", {}).get("Events", [])
        if not events:
            return "loading", data
        for event in events:
            if event.get("EventName") == "GameEnd":
                return "game_over", data
        return "in_game", data
    except:
        return "api_down", None

def get_items(game_values):
    game_values["current_lp"] = 0
    unlocked_champion_ids.clear()
    for file in os.listdir(game_communication_path):
        if file.startswith("AP"):
            with open(os.path.join(game_communication_path, file), 'r') as f:
                item_id = int(f.readline())
                decoded_item = item_id - 565000000
                if decoded_item == 0:
                    game_values["current_lp"] = game_values["current_lp"] + 1
                else:
                    # Prefer base champion id when skin-style IDs are present.
                    champ_id = None
                    if decoded_item >= 60000 and (decoded_item - 60000) in champions:
                        champ_id = decoded_item - 60000
                    elif decoded_item in champions:
                        champ_id = decoded_item
                    if champ_id is not None:
                        unlocked_champion_ids.append(champ_id)
    # Also treat starting champions as unlocked so they show green even
    # if no AP_*.item file exists for them.
    try:
        start_path = os.path.join(game_communication_path, "Starting_Champions.cfg")
        if os.path.exists(start_path):
            with open(start_path, 'r') as f:
                starting_list = ast.literal_eval(f.read())
            for name in starting_list:
                try:
                    cid = get_champion_id(name)
                    if cid is not None and cid not in unlocked_champion_ids:
                        unlocked_champion_ids.append(cid)
                except Exception:
                    continue
    except Exception:
        pass

def read_cfg(game_values):
    files = os.listdir(game_communication_path)
    if "Required_Assists.cfg" in files:
        with open(os.path.join(game_communication_path, "Required_Assists.cfg"), 'r') as f:
            game_values["required_assists"] = int(f.readline())
    else:
        game_values["required_assists"] = 0
    if "Required_CS.cfg" in files:
        with open(os.path.join(game_communication_path, "Required_CS.cfg"), 'r') as f:
            game_values["required_cs"] = int(f.readline())
    else:
        game_values["required_cs"] = 0
    if "Required_Kills.cfg" in files:
        with open(os.path.join(game_communication_path, "Required_Kills.cfg"), 'r') as f:
            game_values["required_kills"] = int(f.readline())
    else:
        game_values["required_kills"] = 0
    if "Required_LP.cfg" in files:
        with open(os.path.join(game_communication_path, "Required_LP.cfg"), 'r') as f:
            game_values["required_lp"] = int(f.readline())
    else:
        game_values["required_lp"] = 0
    if "Required_VS.cfg" in files:
        with open(os.path.join(game_communication_path, "Required_VS.cfg"), 'r') as f:
            game_values["required_vs"] = int(f.readline())
    else:
        game_values["required_vs"] = 0
    if "Starting_Champion_Count.cfg" in files:
        with open(os.path.join(game_communication_path, "Starting_Champion_Count.cfg"), 'r') as f:
            game_values["starting_champions"] = max(0, int(f.readline()))
    else:
        game_values["starting_champions"] = 0
    if "Enabled_Checks.cfg" in files:
        with open(os.path.join(game_communication_path, "Enabled_Checks.cfg"), 'r') as f:
            game_values["enabled_checks"] = ast.literal_eval(f.read())
    else:
        game_values["enabled_checks"] = None
    if "Support_Special_Treatment.cfg" in files:
        with open(os.path.join(game_communication_path, "Support_Special_Treatment.cfg"), 'r') as f:
            game_values["support_special_treatment"] = bool(int(f.read().strip()))
    else:
        game_values["support_special_treatment"] = True

def _champion_row_color(champion_id, window):
    """Returns a background color for a champion row based on unlock/check state."""
    if champion_id is None or champion_id not in unlocked_champion_ids:
        return "#5C0000"  # dark red — locked
    active_location_ids  = window.metadata.get("active_location_ids")  if hasattr(window, "metadata") else None
    checked_location_ids = window.metadata.get("checked_location_ids") if hasattr(window, "metadata") else None
    hinted_location_ids = window.metadata.get("hinted_location_ids") if hasattr(window, "metadata") else None
    if active_location_ids is None or checked_location_ids is None:
        return "#1A5C1A"  # green — unlocked, can't count yet
    champ_name = champions[champion_id]["name"]
    prefix = champ_name + " - "
    checked_ids = {int(x) for x in checked_location_ids}
    hinted_ids = {int(x) for x in hinted_location_ids} if hinted_location_ids is not None else set()
    unfound_hinted_ids = hinted_ids - checked_ids
    total = sum(1 for n in active_location_ids if n.startswith(prefix))
    done  = sum(1 for n, lid in active_location_ids.items() if n.startswith(prefix) and lid is not None and int(lid) in checked_ids)
    hinted = any(
        n.startswith(prefix)
        and lid is not None
        and int(lid) in unfound_hinted_ids
        for n, lid in active_location_ids.items()
    )
    if hinted:
        return "#277997"  # cyan — has a hinted open check
    remaining = max(0, total - done)
    return "#1A5C1A" if remaining > 0 else "#3A3A3A"  # green / grey

def display_champion_list(window):
    # Try to read active locations and mapping from the client slot data
    active_locations = None
    active_location_ids = None
    checked_location_ids = None
    try:
        path = os.path.join(game_communication_path, "Active_Locations.cfg")
        if os.path.exists(path):
            with open(path, 'r') as f:
                active_locations = ast.literal_eval(f.read())
    except Exception:
        active_locations = None

    try:
        path = os.path.join(game_communication_path, "Active_Location_IDs.cfg")
        if os.path.exists(path):
            with open(path, 'r') as f:
                active_location_ids = ast.literal_eval(f.read())
    except Exception:
        active_location_ids = None

    try:
        path = os.path.join(game_communication_path, "Checked_Locations.cfg")
        if os.path.exists(path):
            with open(path, 'r') as f:
                checked_location_ids = set(ast.literal_eval(f.read()))
    except Exception:
        checked_location_ids = None

    try:
        path = os.path.join(game_communication_path, "Hinted_Locations.cfg")
        if os.path.exists(path):
            with open(path, 'r') as f:
                hinted_location_ids = set(ast.literal_eval(f.read()))
        else:
            hinted_location_ids = None
    except Exception:
        hinted_location_ids = None

    # Build totals per champion from active_locations (names)
    totals = {}
    done = {}
    if active_locations is not None:
        for name in active_locations:
            if name.startswith("Starting Champion"):
                champ_key = "Starting"
            else:
                champ_key = name.split(" - ", 1)[0]
            totals[champ_key] = totals.get(champ_key, 0) + 1
            done.setdefault(champ_key, 0)

    # If we have id->name mapping, use checked ids to count done per champion
    if active_location_ids is not None and checked_location_ids is not None:
        # invert mapping to id -> name
        id_to_name = {int(v): k for k, v in active_location_ids.items() if v is not None}
        for lid in checked_location_ids:
            name = id_to_name.get(int(lid))
            if name:
                if name.startswith("Starting Champion"):
                    champ_key = "Starting"
                else:
                    champ_key = name.split(" - ", 1)[0]
                done[champ_key] = done.get(champ_key, 0) + 1

    # Fallback: if mappings aren't available, fall back to previous per-champion send file counting
    champion_table_rows = []
    # De-duplicate unlocked ids so we don't display duplicate rows when the
    # same champion appears multiple times in starting lists or AP files.
    # Also normalize skin-style IDs (>=60000) down to the base champion id.
    unique_unlocked = []
    seen_unlocked = set()
    for cid in unlocked_champion_ids:
        try:
            norm = int(cid)
        except Exception:
            norm = cid
        if isinstance(norm, int) and norm >= 60000 and (norm - 60000) in champions:
            norm = norm - 60000
        if norm not in seen_unlocked:
            unique_unlocked.append(norm)
            seen_unlocked.add(norm)
    if active_locations is None or active_location_ids is None or checked_location_ids is None:
        def count_remaining(champion_id: int) -> int:
            prefix = "send" + str(566000000 + (champion_id * 100))
            cnt = 0
            try:
                for filename in os.listdir(game_communication_path):
                    if filename.startswith(prefix):
                        cnt += 1
            except Exception:
                cnt = 0
            total_objectives = 10
            return max(0, total_objectives - cnt)

        for champion_id in unique_unlocked:
            remaining = count_remaining(champion_id)
            champion_table_rows.append([champions[champion_id]["name"], str(remaining)])
    else:
        for champion_id in unique_unlocked:
            name = champions[champion_id]["name"]
            total = totals.get(name, 0)
            done_count = done.get(name, 0)
            remaining = max(0, total - done_count)
            champion_table_rows.append([name, str(remaining)])

    # honor current sort preference if set on the window object
    sort_pref = window.metadata.get("champion_sort", "name") if hasattr(window, "metadata") else "name"
    reverse = False
    if sort_pref.endswith("_desc"):
        reverse = True
        sort_pref = sort_pref.replace("_desc", "")
    if sort_pref == "name":
        champion_table_rows.sort(key=lambda r: r[0], reverse=reverse)
    elif sort_pref == "remaining":
        champion_table_rows.sort(key=lambda r: int(r[1]), reverse=reverse)

    # Filter out fully-completed champions if checkbox is on
    if window["Hide Completed Checkbox"].get():
        champion_table_rows = [r for r in champion_table_rows if int(r[1]) > 0]

    # Cache for remaining-checks panel
    window.metadata["active_location_ids"] = active_location_ids
    window.metadata["checked_location_ids"] = checked_location_ids
    window.metadata["hinted_location_ids"] = hinted_location_ids

    # Auto-select first champion if none selected yet
    if window.metadata.get("selected_champion_id") is None and champion_table_rows:
        window.metadata["selected_champion_id"] = get_champion_id(champion_table_rows[0][0])

    # Optional debug log: write internal state for investigation when a
    # Debug_Champion_List.flag file exists in the LOLAP folder.
    try:
        debug_flag = os.path.join(game_communication_path, "Debug_Champion_List.flag")
        if os.path.exists(debug_flag):
            debug_path = os.path.join(game_communication_path, "Debug_Champion_List.log")
            with open(debug_path, 'a', encoding='utf-8') as df:
                df.write("--- display_champion_list ---\n")
                df.write("unlocked_champion_ids: %r\n" % (unlocked_champion_ids,))
                df.write("unique_unlocked: %r\n" % (unique_unlocked,))
                df.write("totals: %r\n" % (totals,))
                df.write("done: %r\n" % (done,))
                df.write("champion_table_rows: %r\n" % (champion_table_rows,))
                df.write("selected_champion_id: %r\n" % (window.metadata.get("selected_champion_id"),))
                df.write("-----------------------------\n\n")
    except Exception:
        pass

    window["Champions Unlocked Table"].update(
        values=champion_table_rows,
        row_colors=[(i, _champion_row_color(get_champion_id(r[0]), window))
                    for i, r in enumerate(champion_table_rows)])

    total_champions = sum(1 for k in totals if k != "Starting")
    unlocked_count = len(unlocked_champion_ids)
    window["Champion Count Text"].update(f"({unlocked_count} / {total_champions})")


def log_debug_game_data(game_data):
    if game_data is None:
        return
    try:
        debug_flag = os.path.join(game_communication_path, "Debug_Champion_List.flag")
        if not os.path.exists(debug_flag):
            return
        debug_path = os.path.join(game_communication_path, "Debug_Champion_List.log")
        with open(debug_path, 'a', encoding='utf-8') as df:
            df.write("--- debug_game_data ---\n")
            df.write("activePlayer: %r\n" % (game_data.get("activePlayer"),))
            df.write("allPlayers: %r\n" % (game_data.get("allPlayers"),))
            events = game_data.get("events", {}).get("Events", [])
            df.write("event_count: %d\n" % (len(events),))
            df.write("objective_events: %r\n" % ([e for e in events if e.get("EventName") in ("TurretKilled", "InhibKilled", "DragonKill", "HeraldKill", "BaronKill")]))
            df.write("tracked_teammates: %r\n" % (sorted(tracked_teammates),))
            try:
                df.write("available_teammates: %r\n" % (get_available_teammates(game_data),))
            except Exception:
                df.write("available_teammates: ERROR\n")
            df.write("-----------------------------\n\n")
    except Exception:
        pass


def display_values(window, game_values):
    selected_champion_id = window.metadata.get("selected_champion_id") if hasattr(window, "metadata") else None
    active_location_ids  = window.metadata.get("active_location_ids")  if hasattr(window, "metadata") else None
    checked_location_ids = window.metadata.get("checked_location_ids") if hasattr(window, "metadata") else None

    if selected_champion_id is None or selected_champion_id not in champions:
        window["Values Table"].update(values=[])
        return

    champion_name = champions[selected_champion_id]["name"]

    if active_location_ids is None or checked_location_ids is None:
        window["Values Table"].update(values=[])
        return

    checked_ids = {int(x) for x in checked_location_ids}
    prefix = champion_name + " - "

    # Map the fixed "X" placeholder to the real required number
    x_substitutions = {
        "Get X Kills":        f"Get {game_values['required_kills']} Kills",
        "Get X Assists":      f"Get {game_values['required_assists']} Assists",
        "Get X Creep Score":  f"Get {game_values['required_cs']} Creep Score",
        "Get X Ward Score":   f"Get {game_values['required_vs']} Ward Score",
    }

    rows = []
    for loc_name, loc_id in active_location_ids.items():
        if not loc_name.startswith(prefix):
            continue
        if loc_id is not None and int(loc_id) in checked_ids:
            continue
        suffix = loc_name[len(prefix):]
        rows.append([x_substitutions.get(suffix, suffix)])

    window["Values Table"].update(values=rows)

def send_starting_champion_check(game_values):
    for i in range(1, game_values["starting_champions"] + 1):
        with open(os.path.join(game_communication_path, f"send{566000000 + i}"), 'w'):
            pass

def check_lp_for_victory(game_values):
    if game_values["current_lp"] >= game_values["required_lp"] and game_values["required_lp"] != 0:
        with open(os.path.join(game_communication_path, "victory"), 'w'):
            pass

def won_game(game_data):
    for event in game_data["events"]["Events"]:
        if event.get("EventName") == "GameEnd" and event.get("Result") == "Win":
            return True
    return False

def _player_name_from_record(player_data):
    # Depending on game state/patch, Live Client Data may expose either key.
    return player_data.get("riotIdGameName") or player_data.get("summonerName")


def _name_candidates_from_record(player_data):
    candidates = set()
    if not isinstance(player_data, dict):
        return candidates
    for key in ("riotIdGameName", "summonerName"):
        value = player_data.get(key)
        if not value:
            continue
        text = str(value).strip()
        if not text:
            continue
        candidates.add(text.casefold())
        if "#" in text:
            candidates.add(text.split("#", 1)[0].casefold())
    return candidates


def _name_candidates_from_text(name_text):
    candidates = set()
    if not name_text:
        return candidates
    text = str(name_text).strip()
    if not text:
        return candidates
    normalized_text = text.casefold()
    candidates.add(normalized_text)
    if normalized_text.startswith("bot "):
        candidates.add(normalized_text[4:])
    if normalized_text.startswith("bot_"):
        candidates.add(normalized_text[4:])
    if "#" in text:
        candidates.add(text.split("#", 1)[0].casefold())
    return candidates


def _event_field(event, *keys):
    for key in keys:
        if key in event:
            return event[key]
        lower = key[0].lower() + key[1:] if key else key
        if lower in event:
            return event[lower]
        upper = key[0].upper() + key[1:] if key else key
        if upper in event:
            return event[upper]
    return None


def _event_name_matches(event, *names):
    raw_name = _event_field(event, "EventName", "eventName", "event_name")
    if not raw_name:
        return False
    normalized = str(raw_name).strip().lower().replace("_", "").replace(" ", "")
    for name in names:
        candidate = str(name).strip().lower().replace("_", "").replace(" ", "")
        if normalized == candidate:
            return True
    return False


def _find_player_record(game_data, player_name):
    target_candidates = _name_candidates_from_text(player_name)
    if not target_candidates:
        return None
    for player in game_data.get("allPlayers", []):
        if target_candidates.intersection(_name_candidates_from_record(player)):
            return player
    active_player = game_data.get("activePlayer", {})
    if target_candidates.intersection(_name_candidates_from_record(active_player)):
        return active_player
    return None

def get_player_name(game_data):
    active_player = game_data.get("activePlayer", {})
    return _player_name_from_record(active_player)

def _champion_name_from_raw(raw_champion_name):
    """Map raw champion key (if present) to the Data Dragon display name."""
    if not raw_champion_name:
        return None
    token = str(raw_champion_name).split("_")[-1]
    for champion_id in champions:
        dd_id = champions[champion_id].get("id")
        if dd_id and str(dd_id).casefold() == token.casefold():
            return champions[champion_id]["name"]
    return None

def _normalize_champion_text(text: str) -> str:
    """Accent/format-insensitive champion name normalization."""
    normalized = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return "".join(ch for ch in stripped.casefold() if ch.isalnum())

def get_champion_name(game_data, player_name):
    if not player_name:
        return None
    target_candidates = _name_candidates_from_text(player_name)
    if not target_candidates:
        return None

    active_player = game_data.get("activePlayer", {})
    active_candidates = _name_candidates_from_record(active_player)
    # Only merge the local player's candidate names when the requested
    # player_name actually refers to the local player.
    is_self = bool(target_candidates.intersection(active_candidates))
    if is_self:
        target_candidates |= active_candidates

    for player in game_data["allPlayers"]:
        player_candidates = _name_candidates_from_record(player)
        if target_candidates.intersection(player_candidates):
            from_raw = _champion_name_from_raw(player.get("rawChampionName"))
            if from_raw:
                return from_raw
            return player.get("championName")

    if is_self:
        from_raw = _champion_name_from_raw(active_player.get("rawChampionName"))
        if from_raw:
            return from_raw
        return active_player.get("championName")
    return None

def get_champion_id(champion_name):
    if not champion_name:
        return None
    for champion_id in champions:
        if champions[champion_id]["name"] == champion_name:
            return champion_id
    wanted = _normalize_champion_text(champion_name)
    for champion_id in champions:
        data = champions[champion_id]
        if _normalize_champion_text(data["name"]) == wanted:
            return champion_id
        dd_id = data.get("id")
        if dd_id and _normalize_champion_text(str(dd_id)) == wanted:
            return champion_id


def get_active_player_champion_id(game_data):
    active_player = game_data.get("activePlayer", {})
    from_raw = _champion_name_from_raw(active_player.get("rawChampionName"))
    if from_raw:
        return get_champion_id(from_raw)
    champion_name = active_player.get("championName")
    if champion_name:
        return get_champion_id(champion_name)
    return None

def get_available_teammates(game_data):
    player_name = get_player_name(game_data)
    if not player_name:
        return []
    player_record = _find_player_record(game_data, player_name)
    if player_record is None:
        return []
    player_team = player_record.get("team")
    if player_team is None:
        return []
    own_candidates = _name_candidates_from_record(player_record)
    teammate_names = []
    for player in game_data["allPlayers"]:
        name = _player_name_from_record(player)
        if (player.get("team") == player_team
                and name
                and not own_candidates.intersection(_name_candidates_from_record(player))):
            teammate_names.append(name)
    return sorted(teammate_names)

def update_teammate_selector(window, teammate_names, game_data=None):
    display = []
    auto_track = window["Auto Select Teammates"].get() if "Auto Select Teammates" in window.AllKeysDict else auto_select_teammates
    for name in teammate_names:
        tracking = "✓ " if auto_track or name in tracked_teammates else "  "
        display.append(tracking + name)
    window["Tracked Teammates List"].update(values=display)

def get_tracked_players(game_data):
    player_name = get_player_name(game_data)
    if not player_name:
        return []
    teammate_names = set(get_available_teammates(game_data))
    # Always use the current checkbox value, not the global
    try:
        auto_track = window["Auto Select Teammates"].get()
    except Exception:
        auto_track = False
    if auto_track:
        selected_teammates = sorted(teammate_names)
    else:
        selected_teammates = sorted(tracked_teammates.intersection(teammate_names))
    return [player_name] + selected_teammates

def assisted_tower(game_data, player_name):
    for event in game_data["events"]["Events"]:
        if _event_name_matches(event, "TurretKilled", "TurretKilledEvent", "TowerKilled") and _event_includes_player(game_data, event, player_name):
            return True
    return False


def assisted_inhibitor(game_data, player_name):
    for event in game_data["events"]["Events"]:
        if _event_name_matches(event, "InhibKilled", "InhibitorKilled", "InhibKilledEvent") and _event_includes_player(game_data, event, player_name):
            return True
    return False


def assisted_epic_monster(game_data, player_name, monster_name):
    for event in game_data["events"]["Events"]:
        if _event_name_matches(event, monster_name + "Kill", monster_name + "Killed", monster_name + "KillEvent") and _event_includes_player(game_data, event, player_name):
            return True
    return False


def _event_includes_player(game_data, event, player_name):
    """Return True if the event involves player_name as killer or assister.
    Supports multiple Assisters formats: list[str], list[dict], list[int].
    """
    try:
        target_candidates = _name_candidates_from_text(player_name)
        if not target_candidates:
            return False

        # Direct killer name match, using normalized candidates.
        killer_name = _event_field(event, "KillerName", "killerName", "Killer")
        if killer_name:
            if killer_name == player_name:
                return True
            if _name_candidates_from_text(killer_name) & target_candidates:
                return True

        # Direct killer ID match if available.
        player_record = _find_player_record(game_data, player_name)
        if player_record is not None:
            killer_id = _event_field(event, "KillerParticipantId", "killerParticipantId", "KillerCellId", "killerCellId", "KillerId", "killerId")
            if isinstance(killer_id, int):
                if player_record.get("participantId") == killer_id or player_record.get("cellId") == killer_id:
                    return True

        assisters = _event_field(event, "Assisters", "assisters", "Assister", "assister", "AssisterName", "assisterName")
        if assisters is None:
            assisters = []
        if isinstance(assisters, str):
            assisters = [assisters]

        for a in assisters:
            # If assister is a plain string display name
            if isinstance(a, str):
                if a.casefold() in target_candidates or target_candidates & _name_candidates_from_text(a):
                    return True

            # If assister is a mapping containing a name-like field
            elif isinstance(a, dict):
                for key in ("name", "Name", "summonerName", "summonername", "summoner", "displayName", "displayname", "playerName", "playername"):
                    val = a.get(key)
                    if val and (_name_candidates_from_text(val) & target_candidates):
                        return True
                assister_id = _event_field(a, "participantId", "cellId", "id", "participantID", "cellID")
                if isinstance(assister_id, int) and player_record is not None:
                    if player_record.get("participantId") == assister_id or player_record.get("cellId") == assister_id:
                        return True

            # If assister is an integer ID (participantId or cellId), resolve to player record
            elif isinstance(a, int):
                if player_record is not None:
                    if player_record.get("participantId") == a or player_record.get("cellId") == a:
                        return True
                for p in game_data.get("allPlayers", []):
                    if p.get("participantId") == a or p.get("cellId") == a:
                        if target_candidates & _name_candidates_from_record(p):
                            return True
    except Exception:
        pass
    return False

def player_vision_score(game_data, player_name):
    player = _find_player_record(game_data, player_name)
    if player is not None:
        return player.get("scores", {}).get("wardScore", 0)
    return 0

def player_creep_score(game_data, player_name):
    player = _find_player_record(game_data, player_name)
    if player is not None:
        return player.get("scores", {}).get("creepScore", 0)
    return 0

def player_kills(game_data, player_name):
    player = _find_player_record(game_data, player_name)
    if player is not None:
        return player.get("scores", {}).get("kills", 0)
    return 0

def player_assists(game_data, player_name):
    player = _find_player_record(game_data, player_name)
    if player is not None:
        return player.get("scores", {}).get("assists", 0)
    return 0

def vision_score_above(game_data, player_name, score_target):
    return player_vision_score(game_data, player_name) >= score_target and score_target > 0

def creep_score_above(game_data, player_name, score_target):
    return player_creep_score(game_data, player_name) >= score_target and score_target > 0

def kills_above(game_data, player_name, score_target):
    return player_kills(game_data, player_name) >= score_target and score_target > 0

def assists_above(game_data, player_name, score_target):
    return player_assists(game_data, player_name) >= score_target and score_target > 0

def get_objectives_complete_for_player(game_data, game_values, player_name):
    objectives_complete = []
    champion_name = get_champion_name(game_data, player_name)
    if champion_name is None:
        return objectives_complete, None

    champion_id = get_champion_id(champion_name)
    if champion_id is None:
        return objectives_complete, None

    if champion_id in unlocked_champion_ids:
        is_support = "Support" in champions[champion_id]["tags"]
        enabled = set(game_values.get("enabled_checks") or [])
        sst = game_values.get("support_special_treatment", True)

        def check_enabled(check_name):
            if enabled and check_name not in enabled:
                return False
            if sst:
                if check_name == "Vision Score" and not is_support:
                    return False
                if check_name in ("Kills", "Creep Score") and is_support:
                    return False
            return True

        if check_enabled("Dragon") and assisted_epic_monster(game_data, player_name, "Dragon"):
            objectives_complete.append(1)
        if check_enabled("Herald") and (assisted_epic_monster(game_data, player_name, "Herald") or assisted_epic_monster(game_data, player_name, "Horde")):
            objectives_complete.append(2)
        if check_enabled("Baron") and assisted_epic_monster(game_data, player_name, "Baron"):
            objectives_complete.append(3)
        if check_enabled("Tower") and assisted_tower(game_data, player_name):
            objectives_complete.append(4)
        if check_enabled("Inhibitor") and assisted_inhibitor(game_data, player_name):
            objectives_complete.append(5)
        if check_enabled("Game Win") and won_game(game_data):
            objectives_complete.append(10)
        if check_enabled("Assists") and assists_above(game_data, player_name, game_values["required_assists"]):
            objectives_complete.append(6)
        if check_enabled("Vision Score") and vision_score_above(game_data, player_name, game_values["required_vs"]):
            objectives_complete.append(7)
        if check_enabled("Kills") and kills_above(game_data, player_name, game_values["required_kills"]):
            objectives_complete.append(8)
        if check_enabled("Creep Score") and creep_score_above(game_data, player_name, game_values["required_cs"]):
            objectives_complete.append(9)
    return objectives_complete, champion_id

def get_objectives_complete(game_data, game_values):
    for player_name in get_tracked_players(game_data):
        objectives_complete, champion_id = get_objectives_complete_for_player(game_data, game_values, player_name)
        if champion_id is not None:
            send_locations(objectives_complete, champion_id)

def send_locations(objectives_complete, champion_id):
    for objective_id in objectives_complete:
        with open(os.path.join(game_communication_path, "send" + str(566000000 + (champion_id * 100) + objective_id)), 'w'):
            pass

###LCU / CHAMP SELECT###

_lockfile_path_cache = None
_lockfile_lock = threading.Lock()
_lockfile_prompting = False  # Flag to prevent concurrent prompts
_prompt_event = None
_prompt_result = None

def _get_cached_lockfile_path() -> str:
    """Load cached lockfile path from config. Returns None if not found or empty (user declined)."""
    try:
        cache_file = os.path.join(game_communication_path, "Lockfile_Path.cfg")
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                path = f.read().strip()
                if path and os.path.exists(path):
                    return path
    except Exception:
        pass
    return None

def _save_lockfile_path(path: str) -> None:
    """Cache the lockfile path or install folder to config."""
    try:
        cache_file = os.path.join(game_communication_path, "Lockfile_Path.cfg")
        with open(cache_file, 'w') as f:
            f.write(path)
    except Exception:
        pass

def _clear_cached_lockfile_path() -> None:
    """Remove cached lockfile path."""
    try:
        cache_file = os.path.join(game_communication_path, "Lockfile_Path.cfg")
        if os.path.exists(cache_file):
            os.remove(cache_file)
    except Exception:
        pass


def _prompt_for_league_path() -> str:
    """Ask user to select League install folder or executable."""
    try:
        # Keep prompting until user selects a folder with a valid lockfile or cancels.
        while True:
            result = sg.popup_get_folder(
                "League Client not found automatically.\n\n"
                "Please select your League of Legends installation folder\n"
                "(the folder containing LeagueClientUx.exe)",
                title="Select League of Legends Folder"
            )
            if not result:
                # user cancelled
                return None
            if os.path.exists(result):
                lockfile_path = os.path.join(result, "lockfile")
                exe_path = os.path.join(result, "LeagueClientUx.exe")
                if os.path.exists(lockfile_path):
                    _save_lockfile_path(lockfile_path)
                    return lockfile_path
                # If the folder contains the League executable, accept and cache the folder.
                if os.path.exists(exe_path):
                    _save_lockfile_path(result)
                    sg.popup('Selected folder saved. Lockfile will be detected when the League client runs.', title='Folder Saved')
                    return result
                else:
                    sg.popup('Selected folder does not contain LeagueClientUx.exe. Please select the folder containing LeagueClientUx.exe.', title='Invalid Folder')
                    continue
            else:
                sg.popup('Selected path does not exist. Please choose a valid folder.', title='Invalid Path')
                continue
    except Exception:
        pass
    return None

def _read_lockfile():
    import subprocess
    global _lockfile_path_cache, _lockfile_lock, _lockfile_prompting, _prompt_event, _prompt_result

    # Fast-path: cached path. Cache may be either a lockfile path (file) or an install folder (dir).
    if _lockfile_path_cache and _lockfile_path_cache != "":
        try:
            # If cached is a file, assume it's the lockfile
            if os.path.isfile(_lockfile_path_cache):
                with open(_lockfile_path_cache, 'r') as f:
                    parts = f.read().strip().split(':')
                if len(parts) >= 4:
                    return parts[2].strip(), parts[3].strip()
                return None, None

            # If cached is a directory, look for 'lockfile' inside it. If not present, poll briefly.
            if os.path.isdir(_lockfile_path_cache):
                lockfile_path = os.path.join(_lockfile_path_cache, 'lockfile')
                if not os.path.exists(lockfile_path):
                    import time
                    # wait up to 10s for the client to start and create lockfile
                    for _ in range(10):
                        time.sleep(1)
                        if os.path.exists(lockfile_path):
                            break
                if os.path.exists(lockfile_path):
                    _save_lockfile_path(lockfile_path)
                    with open(lockfile_path, 'r') as f:
                        parts = f.read().strip().split(':')
                    if len(parts) >= 4:
                        return parts[2].strip(), parts[3].strip()
                return None, None
        except Exception:
            pass
        return None, None

    try:
        if _lockfile_path_cache is None:
            with _lockfile_lock:
                # Double-check state
                if _lockfile_path_cache is not None:
                    pass
                elif _lockfile_prompting:
                    return None, None
                else:
                    # cached path on disk
                    cached = _get_cached_lockfile_path()
                    if cached:
                        _lockfile_path_cache = cached
                    else:
                        # Try WMIC-based detection
                        try:
                            result = subprocess.run(
                                ["wmic", "process", "where", "name='LeagueClientUx.exe'", "get", "ExecutablePath", "/value"],
                                capture_output=True, text=True, timeout=3,
                                creationflags=subprocess.CREATE_NO_WINDOW
                            )
                            exe = ""
                            for line in result.stdout.splitlines():
                                if "ExecutablePath=" in line:
                                    exe = line.split("=", 1)[1].strip()
                                    break
                            if exe:
                                lockfile_path = os.path.join(os.path.dirname(exe), "lockfile")
                                if os.path.exists(lockfile_path):
                                    _lockfile_path_cache = lockfile_path
                                    _save_lockfile_path(lockfile_path)
                        except Exception:
                            pass

                        # If WMIC failed, request the main thread to prompt the user
                        if _lockfile_path_cache is None:
                            if 'window' in globals() and window is not None:
                                _lockfile_prompting = True
                                res = None
                                try:
                                    _prompt_event = threading.Event()
                                    _prompt_result = None
                                    window.write_event_value('SHOW_LEAGUE_PROMPT', None)
                                    # wait up to 30s for user to respond
                                    if _prompt_event.wait(30):
                                        res = _prompt_result
                                    else:
                                        res = None
                                finally:
                                    _lockfile_prompting = False
                                    _prompt_event = None
                                    _prompt_result = None
                                _lockfile_path_cache = res if res else None
                            else:
                                # Fallback: blocking popup in background (rare)
                                _lockfile_prompting = True
                                try:
                                    user_result = _prompt_for_league_path()
                                    _lockfile_path_cache = user_result if user_result else None
                                finally:
                                    _lockfile_prompting = False
                if _lockfile_path_cache is None:
                    return None, None

        # Parse lockfile contents
        if _lockfile_path_cache:
            with open(_lockfile_path_cache, 'r') as f:
                parts = f.read().strip().split(':')
            if len(parts) >= 4:
                return parts[2].strip(), parts[3].strip()
    except Exception:
        _lockfile_path_cache = None
    return None, None

def get_champ_select_available_ids():
    """Returns (lcu_ok, in_session, own_ids, teammate_ids, bench_ids).
    lcu_ok=False means client not found. in_session=False means not in champ select."""
    port, password = _read_lockfile()
    if not port or not password:
        return False, False, set(), set(), set()
    try:
        url = f"https://127.0.0.1:{port}/lol-champ-select/v1/session"
        resp = requests.get(url, auth=("riot", password), verify=False, timeout=1)
        if resp.status_code != 200:
            return True, False, set(), set(), set()
        session = resp.json()
        own_ids = set()
        teammate_ids = set()
        bench_ids = set()
        local_cell_id = session.get("localPlayerCellId")
        for slot in session.get("myTeam", []):
            cid = slot.get("championId") or slot.get("championPickIntent")
            if not cid:
                continue
            if slot.get("cellId") == local_cell_id:
                own_ids.add(cid)
            else:
                teammate_ids.add(cid)
        for slot in session.get("benchChampions", []):
            cid = slot.get("championId")
            if cid:
                bench_ids.add(cid)
        return True, True, own_ids, teammate_ids, bench_ids
    except Exception:
        return True, False, set(), set(), set()

def _champ_select_names_from_ids(ids, window):
    """Given a set of champion IDs from champ select, return names of AP-unlocked
    champions with remaining checks."""
    active_location_ids  = window.metadata.get("active_location_ids")
    checked_location_ids = window.metadata.get("checked_location_ids")
    names = []
    for cid in sorted(ids):
        if cid not in unlocked_champion_ids:
            continue
        if cid not in champions:
            continue
        champ_name = champions[cid]["name"]
        # check remaining
        if active_location_ids is not None and checked_location_ids is not None:
            prefix = champ_name + " - "
            checked_ids = {int(x) for x in checked_location_ids}
            total = sum(1 for n in active_location_ids if n.startswith(prefix))
            done  = sum(1 for n, lid in active_location_ids.items() if n.startswith(prefix) and lid is not None and int(lid) in checked_ids)
            if total - done <= 0:
                continue
        names.append(champ_name)
    return names

sg.theme('DarkAmber')
_THEME_BG = "#2c2825"
layout = [  [
                sg.Text('In Match: No', justification = 'center', key = "In Match Text"),
                sg.Button('Match Tracking: Off', key = "Check for Match Button", button_color=("white", "#5C0000")),
                sg.Text('', key = "Champ Select Text", text_color="yellow"),
            ],
            [   
                sg.Column(
                [   [sg.Text("Champions Unlocked"), sg.Text("", key="Champion Count Text"), sg.Checkbox("Hide Completed", key="Hide Completed Checkbox", default=False)],
                    [sg.Table(
                        [],
                        headings=["Champion Name", "Remaining"],
                        key="Champions Unlocked Table",
                        enable_click_events=True)]
                ]),
                sg.Column(
                [
                    [sg.Text("Remaining Checks")],
                    [sg.Table(
                        [],
                        headings=["Check"],
                        key="Values Table",
                        col_widths=[24],
                        auto_size_columns=False)]
               ]),
                sg.Column(
                [
                    [sg.Text("Tracked Teammates")],
                    [sg.Listbox(
                        [],
                        size=(22, 5),
                        enable_events=True,
                        key="Tracked Teammates List",
                        no_scrollbar=True)],
                    [sg.Checkbox("Auto Select All", key="Auto Select Teammates", default=auto_select_teammates)]
               ])
            ]
        ]

window = sg.Window(_WINDOW_TITLE, layout)
window.metadata = {"champion_sort": "name", "selected_champion_id": None, "game_connected": False, "lcu_status": None}

_executor = ThreadPoolExecutor(max_workers=2)
_game_future = None
_lcu_future  = None
while True:
    game_data = None
    event, values = window.read(timeout=500)
    if event == sg.WIN_CLOSED:
        break
    # Main-thread handler for lockfile prompt requests from background threads
    if event == 'SHOW_LEAGUE_PROMPT':
        try:
            # Show folder dialog on main thread
            result = sg.popup_get_folder(
                "League Client not found automatically.\n\n"
                "Please select your League of Legends installation folder\n"
                "(the folder containing LeagueClientUx.exe)",
                title="Select League of Legends Folder"
            )
            if result and os.path.exists(result):
                lockfile_path = os.path.join(result, "lockfile")
                exe_path = os.path.join(result, "LeagueClientUx.exe")
                if os.path.exists(lockfile_path):
                    _save_lockfile_path(lockfile_path)
                    try:
                        _prompt_result = lockfile_path
                    except Exception:
                        pass
                elif os.path.exists(exe_path):
                    # Accept and cache install folder even if client not running yet
                    _save_lockfile_path(result)
                    try:
                        _prompt_result = result
                    except Exception:
                        pass
                else:
                    try:
                        _prompt_result = None
                    except Exception:
                        pass
            else:
                try:
                    _prompt_result = None
                except Exception:
                    pass
        except Exception:
            try:
                _prompt_result = None
            except Exception:
                pass
        finally:
            try:
                if _prompt_event is not None:
                    _prompt_event.set()
            except Exception:
                pass
        continue
    if event == 'Check for Match Button':
        in_match = not in_match
        window.metadata["game_connected"] = False
        if in_match:
            window["Check for Match Button"].update(text="Match Tracking: On", button_color=("white", "#1A5C1A"))
        else:
            window["Check for Match Button"].update(text="Match Tracking: Off", button_color=("white", "#5C0000"))
    # 'Change Folder' removed -- folder selection is handled via automatic prompt when needed.
    if event == "Hide Completed Checkbox":
        display_champion_list(window)
    if event == "Auto Select Teammates":
        auto_select_teammates = window["Auto Select Teammates"].get()
        if game_data is not None:
            update_teammate_selector(window, get_available_teammates(game_data), game_data)
    if isinstance(event, tuple) and len(event) == 3 and event[0] == "Champions Unlocked Table" and event[1] == "+CLICKED+":
        cell = event[2]
        # cell may be an int or a (row, col) tuple depending on PySimpleGUI version/events
        row_index = None
        col = None
        if isinstance(cell, (list, tuple)) and len(cell) >= 1:
            row_index = cell[0]
            if len(cell) >= 2:
                col = cell[1]
        else:
            try:
                row_index = int(cell)
            except Exception:
                row_index = None

        if row_index == -1:
            # header clicked -> sort by column if we know the column index
            if col is None:
                continue
            prev = window.metadata.get("champion_sort", "name")
            sort_keys = ["name", "remaining"]
            if col < len(sort_keys):
                new_sort = sort_keys[col]
                if prev == new_sort:
                    window.metadata["champion_sort"] = new_sort + "_desc"
                elif prev == new_sort + "_desc":
                    window.metadata["champion_sort"] = new_sort
                else:
                    window.metadata["champion_sort"] = new_sort
        else:
            # row clicked -> select champion if index valid
            if row_index is None:
                continue
            table_data = window["Champions Unlocked Table"].Values
            if table_data and 0 <= row_index < len(table_data):
                window.metadata["selected_champion_id"] = get_champion_id(table_data[row_index][0])
    if event == "Tracked Teammates List":
        if not auto_select_teammates:
            selected = values.get("Tracked Teammates List", [])
            if selected:
                raw = selected[0]
                name = raw[2:] if raw.startswith(("\u2713 ", "  ")) else raw
                if name in tracked_teammates:
                    tracked_teammates.discard(name)
                else:
                    tracked_teammates.add(name)
    get_items(game_values)
    read_cfg(game_values)
    display_champion_list(window)
    display_values(window, game_values)
    if in_match:
        check_lp_for_victory(game_values)
        send_starting_champion_check(game_values)

    # --- collect results from background threads ---
    game_status, game_data = "api_down", None
    if in_match and _game_future is not None and _game_future.done():
        game_status, game_data = _game_future.result()

    champ_select_result = None
    if in_match and not window.metadata.get("game_connected") and _lcu_future is not None and _lcu_future.done():
        lcu_ok, in_session, own_ids, teammate_ids, bench_ids = _lcu_future.result()
        champ_select_result = (lcu_ok, in_session, own_ids, teammate_ids, bench_ids)
        window.metadata["lcu_status"] = champ_select_result

    # --- fire off next background fetches ---
    if in_match and (_game_future is None or _game_future.done()):
        _game_future = _executor.submit(get_game_data)
    if in_match and not window.metadata.get("game_connected") and (_lcu_future is None or _lcu_future.done()):
        _lcu_future = _executor.submit(get_champ_select_available_ids)

    # --- update champ select line (now integrated into status text) ---
    window["Champ Select Text"].update("")
    if game_data is None:
        if in_match:
            lcu_status = window.metadata.get("lcu_status")
            if lcu_status is not None:
                lcu_ok, in_session, own_ids, teammate_ids, bench_ids = lcu_status
                if not lcu_ok:
                    status_str = "Status: Waiting (LCU: client not found)"
                elif in_session:
                    own_names = _champ_select_names_from_ids(own_ids | bench_ids, window)
                    teammate_names = _champ_select_names_from_ids(teammate_ids, window)
                    parts = []
                    if own_names:
                        parts.append("your pick: " + ", ".join(own_names))
                    elif own_ids or bench_ids:
                        parts.append("your pick has no checks remaining")
                    if teammate_names:
                        parts.append("teammate: " + ", ".join(teammate_names))
                    if parts:
                        status_str = "Status: Champ select - " + " | ".join(parts)
                    else:
                        status_str = "Status: Champ select (no checks remaining)"
                else:
                    status_str = "Status: Waiting for game..."
            else:
                status_str = "Status: Waiting for League client..."
            window["In Match Text"].update(status_str)
            window.metadata["game_connected"] = False
            update_teammate_selector(window, [])
        else:
            window["In Match Text"].update("Status: Tracking off")
            update_teammate_selector(window, [])
            window.metadata["game_connected"] = False
    else:
        player_name = get_player_name(game_data)
        champion_name = get_champion_name(game_data, player_name) or "Unknown"
        champion_id = get_champion_id(champion_name)
        locked = champion_id is not None and champion_id not in unlocked_champion_ids

        if game_status == "loading":
            window["In Match Text"].update(f"Status: Loading ({champion_name})...")
            window.metadata["game_connected"] = True
            update_teammate_selector(window, get_available_teammates(game_data), game_data)
        elif game_status == "game_over":
            result = "?"
            for event in game_data["events"]["Events"]:
                if event.get("EventName") == "GameEnd":
                    result = event.get("Result", "?")
                    break
            status_str = f"Status: Game over ({champion_name} - {result})"
            if locked:
                status_str += " [champion locked]"
            window["In Match Text"].update(status_str)
            window.metadata["game_connected"] = True
            update_teammate_selector(window, get_available_teammates(game_data), game_data)
            log_debug_game_data(game_data)
            get_objectives_complete(game_data, game_values)
            # Some patches/locales can make end-of-game identity matching flaky.
            # Ensure own champion's Game Win objective is emitted on a confirmed win.
            if won_game(game_data):
                active_champion_id = get_active_player_champion_id(game_data)
                if active_champion_id is not None and active_champion_id in unlocked_champion_ids:
                    send_locations([10], active_champion_id)
        else:  # in_game
            if champion_id is not None:
                window.metadata["selected_champion_id"] = champion_id
            status_str = f"Status: In game ({champion_name})"
            if locked:
                status_str += " [champion locked]"
            window["In Match Text"].update(status_str)
            window.metadata["game_connected"] = True
            update_teammate_selector(window, get_available_teammates(game_data), game_data)
            log_debug_game_data(game_data)
            get_objectives_complete(game_data, game_values)

window.close()