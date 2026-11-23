"""
server/scoreboard.py
====================
Persistent Leaderboard & Reigning Champion Management for Project Blak.
Stores player and bot ratings, tracks champion coronations, and persists
arena rankings across server sessions in data/scoreboard.json.
"""

from __future__ import annotations
import copy
from datetime import datetime
import json
import os
from typing import Any, Dict, List, Optional

from sandbox.planner import DIFFICULTY_PROFILES, PlannerConfig

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
SCOREBOARD_PATH = os.path.join(DATA_DIR, "scoreboard.json")
CHAMPION_PROFILE_PATH = os.path.join(PROFILES_DIR, "champion.json")
CHAMPION_EVOLVING_PATH = os.path.join(PROFILES_DIR, "champion_evolving.json")


def _get_default_scoreboard() -> Dict[str, Any]:
    """Generates clean default scoreboard with baseline AI benchmarks."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "champion": {
            "name": "Grandmaster",
            "type": "bot",
            "rating": 1600,
            "title_defenses": 0,
            "crowned_at": now_str,
            "weights": None,
            "mutation_tag": "Grandmaster_base",
            "source_player": "Grandmaster",
        },
        "mutation_counter": 0,
        "leaderboard": [
            {"name": "Grandmaster", "type": "bot", "rating": 1600, "wins": 0, "losses": 0},
            {"name": "Tactical", "type": "bot", "rating": 1500, "wins": 0, "losses": 0},
            {"name": "Aggressive", "type": "bot", "rating": 1450, "wins": 0, "losses": 0},
            {"name": "Casual", "type": "bot", "rating": 1350, "wins": 0, "losses": 0},
        ],
        "match_history_count": 0,
    }


def load_scoreboard() -> Dict[str, Any]:
    """Loads scoreboard from disk, initializing defaults if absent."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.isfile(SCOREBOARD_PATH):
        default_data = _get_default_scoreboard()
        save_scoreboard(default_data)
        return default_data

    try:
        with open(SCOREBOARD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("champion") or not data.get("leaderboard"):
            default_data = _get_default_scoreboard()
            save_scoreboard(default_data)
            return default_data
        return data
    except Exception as exc:
        print(f"[Scoreboard] Warning loading scoreboard: {exc}. Reverting to defaults.")
        default_data = _get_default_scoreboard()
        save_scoreboard(default_data)
        return default_data


def save_scoreboard(data: Dict[str, Any]) -> None:
    """Safely saves scoreboard data to disk."""
    os.makedirs(DATA_DIR, exist_ok=True)
    temp_path = f"{SCOREBOARD_PATH}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, SCOREBOARD_PATH)


def get_current_champion() -> Dict[str, Any]:
    """Returns metadata for the current reigning champion."""
    sb = load_scoreboard()
    return sb.get("champion", {
        "name": "Grandmaster",
        "type": "bot",
        "rating": 1600,
        "title_defenses": 0,
        "crowned_at": "Initial",
    })


def get_champion_config() -> PlannerConfig:
    """Resolves and loads the immutable PlannerConfig for the reigning champion."""
    champ = get_current_champion()
    champ_name = champ.get("name", "Grandmaster")

    # 1. Load from frozen snapshot file if present
    if os.path.isfile(CHAMPION_PROFILE_PATH):
        try:
            with open(CHAMPION_PROFILE_PATH, "r", encoding="utf-8") as f:
                c_data = json.load(f)
            weights = c_data.get("weights") or {}
            name = c_data.get("name", champ_name)
            mut_tag = c_data.get("mutation_tag", "")
            tag_str = f" [{mut_tag}]" if mut_tag else ""
            base = DIFFICULTY_PROFILES.get("Grandmaster", DIFFICULTY_PROFILES["Tactical"])
            return PlannerConfig(
                name=f"Champion ({name}{tag_str})",
                beam_width=int(c_data.get("beam_width", 16)),
                max_depth=int(c_data.get("max_depth", 10)),
                w_objective=float(weights.get("w_objective", 1750.0)),
                w_hand_flush=float(weights.get("w_hand_flush", 400.0)),
                w_ladder_perk=float(weights.get("w_ladder_perk", 250.0)),
                w_anti_feed=max(300.0, float(weights.get("w_anti_feed", 300.0))),
                w_capping=float(weights.get("w_capping", 80.0)),
                w_discard_steal=float(weights.get("w_discard_steal", 35.0)),
                w_opp_obj_sacrifice=0.0,
                enable_opp_objective_play=False,
                w_endgame_threat_veto=float(weights.get("w_endgame_threat_veto", 1500.0)),
                w_joker_conservation=max(220.0, float(weights.get("w_joker_conservation", 280.0))),
                w_omni_preservation=float(weights.get("w_omni_preservation", 180.0)),
                w_discard_unlock=float(weights.get("w_discard_unlock", 150.0)),
                w_hand_diversity=float(weights.get("w_hand_diversity", 35.0)),
            )
        except Exception as exc:
            print(f"[Scoreboard] Error reading champion snapshot: {exc}")

    # 2. Check if custom saved weights exist in champion entry
    saved_weights = champ.get("weights")
    if saved_weights and isinstance(saved_weights, dict):
        base = DIFFICULTY_PROFILES.get("Grandmaster", DIFFICULTY_PROFILES["Tactical"])
        return PlannerConfig(
            name=f"Champion ({champ_name})",
            beam_width=int(champ.get("beam_width", 16)),
            max_depth=int(champ.get("max_depth", 10)),
            w_objective=float(saved_weights.get("w_objective", 1750.0)),
            w_hand_flush=float(saved_weights.get("w_hand_flush", 400.0)),
            w_ladder_perk=float(saved_weights.get("w_ladder_perk", 250.0)),
            w_anti_feed=max(300.0, float(saved_weights.get("w_anti_feed", 300.0))),
            w_capping=float(saved_weights.get("w_capping", 80.0)),
            w_discard_steal=float(saved_weights.get("w_discard_steal", 35.0)),
            w_opp_obj_sacrifice=0.0,
            enable_opp_objective_play=False,
            w_endgame_threat_veto=float(saved_weights.get("w_endgame_threat_veto", 1500.0)),
            w_joker_conservation=max(220.0, float(saved_weights.get("w_joker_conservation", 280.0))),
            w_omni_preservation=float(saved_weights.get("w_omni_preservation", 180.0)),
            w_discard_unlock=float(saved_weights.get("w_discard_unlock", 150.0)),
            w_hand_diversity=float(saved_weights.get("w_hand_diversity", 35.0)),
        )

    # 3. Fallback to standard Grandmaster benchmark
    base = copy.deepcopy(DIFFICULTY_PROFILES.get("Grandmaster", DIFFICULTY_PROFILES["Tactical"]))
    base.name = f"Champion ({champ_name})"
    base.beam_width = 16
    base.max_depth = 10
    base.enable_opp_objective_play = False
    return base


def get_evolving_champion_config() -> PlannerConfig:
    """Loads the actively evolving shadow champion candidate."""
    if os.path.isfile(CHAMPION_EVOLVING_PATH):
        try:
            with open(CHAMPION_EVOLVING_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            weights = data.get("weights") or {}
            base = DIFFICULTY_PROFILES.get("Grandmaster", DIFFICULTY_PROFILES["Tactical"])
            return PlannerConfig(
                name="Champion (Evolving Candidate)",
                beam_width=int(data.get("beam_width", 16)),
                max_depth=int(data.get("max_depth", 10)),
                w_objective=float(weights.get("w_objective", 1750.0)),
                w_hand_flush=float(weights.get("w_hand_flush", 400.0)),
                w_ladder_perk=float(weights.get("w_ladder_perk", 250.0)),
                w_anti_feed=max(180.0, float(weights.get("w_anti_feed", 250.0))),
                w_capping=float(weights.get("w_capping", 80.0)),
                w_discard_steal=float(weights.get("w_discard_steal", 35.0)),
                w_opp_obj_sacrifice=0.0,
                enable_opp_objective_play=False,
                w_endgame_threat_veto=float(weights.get("w_endgame_threat_veto", 1500.0)),
                w_joker_conservation=max(220.0, float(weights.get("w_joker_conservation", 280.0))),
                w_omni_preservation=float(weights.get("w_omni_preservation", 180.0)),
                w_discard_unlock=float(weights.get("w_discard_unlock", 150.0)),
                w_hand_diversity=float(weights.get("w_hand_diversity", 35.0)),
            )
        except Exception as e:
            print(f"[Scoreboard] Evolving champion profile load error: {e}")

    import copy  # Deferred import: circular dependency
    cfg = copy.deepcopy(get_champion_config())
    cfg.name = "Champion (Evolving Candidate)"
    return cfg


def save_evolving_champion_config(
    cfg: PlannerConfig,
    moves_learned: int,
    contributors: Optional[List[str]] = None,
    last_contributor: Optional[str] = None,
) -> None:
    """Saves updated weights and player attribution for the evolving shadow champion candidate."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = {
        "name": "Champion_Evolving",
        "type": "evolving_candidate",
        "total_moves_learned": moves_learned,
        "contributors": contributors or [],
        "last_contributor": last_contributor,
        "last_updated": now_str,
        "weights": {
            "w_objective": cfg.w_objective,
            "w_hand_flush": cfg.w_hand_flush,
            "w_ladder_perk": cfg.w_ladder_perk,
            "w_anti_feed": cfg.w_anti_feed,
            "w_capping": cfg.w_capping,
            "w_discard_steal": cfg.w_discard_steal,
            "w_opp_obj_sacrifice": cfg.w_opp_obj_sacrifice,
            "w_endgame_threat_veto": cfg.w_endgame_threat_veto,
            "w_joker_conservation": cfg.w_joker_conservation,
            "w_omni_preservation": cfg.w_omni_preservation,
            "w_discard_unlock": cfg.w_discard_unlock,
            "w_hand_diversity": cfg.w_hand_diversity,
        },
    }
    temp_path = f"{CHAMPION_EVOLVING_PATH}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    os.replace(temp_path, CHAMPION_EVOLVING_PATH)


INTERNAL_BOTS = {"Champion_Evolving", "GrandmasterCandidate"}


def is_internal_candidate(name: str) -> bool:
    """Returns True if the entity is an internal candidate and should not be public."""
    if not name:
        return True
    return name in INTERNAL_BOTS or name.endswith("_Evolving") or name.endswith("Candidate")


def record_champion_evolution(crowned_name: str, base_rating: int) -> int:
    """Awards rating and win count for a self-evolution upgrade of the reigning champion."""
    sb = load_scoreboard()
    entry = _get_or_create_entry(sb, crowned_name, "bot", default_rating=base_rating)
    entry["wins"] = entry.get("wins", 0) + 1
    new_rating = max(100, entry.get("rating", base_rating) + 16)
    entry["rating"] = new_rating
    sb["match_history_count"] = sb.get("match_history_count", 0) + 1
    sb["leaderboard"] = [e for e in sb.get("leaderboard", []) if not is_internal_candidate(e.get("name", ""))]
    sb["leaderboard"].sort(key=lambda x: x.get("rating", 0), reverse=True)
    save_scoreboard(sb)
    return new_rating


def record_champion_defense(champ_name: str, champ_type: str, base_rating: int) -> int:
    """Awards defense rating and win count for successfully defending against an unviable mutation."""
    sb = load_scoreboard()
    entry = _get_or_create_entry(sb, champ_name, champ_type, default_rating=base_rating)
    entry["wins"] = entry.get("wins", 0) + 1
    new_rating = max(100, entry.get("rating", base_rating) + 8)
    entry["rating"] = new_rating
    sb["match_history_count"] = sb.get("match_history_count", 0) + 1
    sb["leaderboard"] = [e for e in sb.get("leaderboard", []) if not is_internal_candidate(e.get("name", ""))]
    sb["leaderboard"].sort(key=lambda x: x.get("rating", 0), reverse=True)
    save_scoreboard(sb)
    return new_rating


def update_champion(
    name: str,
    p_type: str,
    rating: int,
    weights: Optional[Dict[str, float]] = None,
    mutation_tag: Optional[str] = None,
    source_player: Optional[str] = None,
    is_coronation: Optional[bool] = None,
) -> str:
    """Crowns a new champion or records title defense with player mutation tag."""
    sb = load_scoreboard()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    curr = sb.get("champion", {})
    if is_coronation is not None:
        is_new_coronation = is_coronation
    else:
        is_new_coronation = (curr.get("name") != name)

    if not is_new_coronation:
        # Title Defense
        curr["title_defenses"] = curr.get("title_defenses", 0) + 1
        curr["rating"] = rating
        if weights:
            curr["weights"] = weights
        final_tag = curr.get("mutation_tag") or mutation_tag or "Grandmaster_base"
        final_source = curr.get("source_player") or source_player or name
    else:
        # New Champion Coronation!
        mut_counter = int(sb.get("mutation_counter", 0)) + 1
        sb["mutation_counter"] = mut_counter
        final_source = source_player or name
        if not mutation_tag:
            final_tag = f"{final_source}_mut_{mut_counter}"
        else:
            final_tag = mutation_tag

        sb["champion"] = {
            "name": name,
            "type": p_type,
            "rating": rating,
            "title_defenses": 0,
            "crowned_at": now_str,
            "weights": weights,
            "mutation_tag": final_tag,
            "source_player": final_source,
        }

    _upsert_leaderboard_entry(sb, name, p_type, rating=rating)
    sb["leaderboard"] = [e for e in sb.get("leaderboard", []) if not is_internal_candidate(e.get("name", ""))]
    save_scoreboard(sb)

    # Persist immutable frozen snapshot to champion.json
    os.makedirs(PROFILES_DIR, exist_ok=True)
    if weights is None and os.path.isfile(CHAMPION_PROFILE_PATH):
        try:
            with open(CHAMPION_PROFILE_PATH, "r", encoding="utf-8") as f:
                old_snap = json.load(f)
                weights = old_snap.get("weights")
                if not final_tag:
                    final_tag = old_snap.get("mutation_tag")
                if not final_source:
                    final_source = old_snap.get("source_player")
        except Exception:
            pass

    champ_record = {
        "name": name,
        "type": p_type,
        "rating": rating,
        "crowned_at": now_str,
        "weights": weights,
        "mutation_tag": final_tag,
        "source_player": final_source,
    }
    with open(CHAMPION_PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(champ_record, f, indent=2)

    # ONLY reset evolving candidate when a NEW champion is crowned!
    if is_new_coronation:
        evolving_record = copy.deepcopy(champ_record)
        evolving_record["name"] = "Champion_Evolving"
        evolving_record["type"] = "evolving_candidate"
        evolving_record["total_moves_learned"] = 0
        evolving_record["contributors"] = []
        evolving_record["last_contributor"] = None
        with open(CHAMPION_EVOLVING_PATH, "w", encoding="utf-8") as f:
            json.dump(evolving_record, f, indent=2)

    print(f"[Scoreboard] Crowned/Defended Reigning Champion: {name} [{final_tag}] ({p_type}) - Rating: {rating}")
    return final_tag


def reset_evolving_candidate() -> None:
    """Resets learned move counter after an evaluated title series."""
    if os.path.isfile(CHAMPION_EVOLVING_PATH):
        try:
            with open(CHAMPION_EVOLVING_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["total_moves_learned"] = 0
            data["contributors"] = []
            data["last_contributor"] = None
            temp_path = f"{CHAMPION_EVOLVING_PATH}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, CHAMPION_EVOLVING_PATH)
        except Exception:
            pass



def record_duel_result(winner_name: str, loser_name: str, winner_type: str = "player", loser_type: str = "bot") -> None:
    """Updates ratings and win/loss records using Elo formula (K=32)."""
    sb = load_scoreboard()

    w_entry = _get_or_create_entry(sb, winner_name, winner_type, default_rating=1500)
    l_entry = _get_or_create_entry(sb, loser_name, loser_type, default_rating=1500)

    r_w = w_entry["rating"]
    r_l = l_entry["rating"]

    # Elo Expected Score
    exp_w = 1.0 / (1.0 + 10.0 ** ((r_l - r_w) / 400.0))
    exp_l = 1.0 - exp_w

    k = 32
    new_r_w = int(round(r_w + k * (1.0 - exp_w)))
    new_r_l = int(round(r_l + k * (0.0 - exp_l)))

    w_entry["rating"] = max(100, new_r_w)
    w_entry["wins"] = w_entry.get("wins", 0) + 1

    l_entry["rating"] = max(100, new_r_l)
    l_entry["losses"] = l_entry.get("losses", 0) + 1

    sb["match_history_count"] = sb.get("match_history_count", 0) + 1

    # Re-sort leaderboard by rating descending
    sb["leaderboard"].sort(key=lambda x: x.get("rating", 0), reverse=True)
    save_scoreboard(sb)


def _get_or_create_entry(sb: Dict[str, Any], name: str, p_type: str, default_rating: int = 1500) -> Dict[str, Any]:
    for e in sb.setdefault("leaderboard", []):
        if e.get("name") == name:
            return e
    new_e = {
        "name": name,
        "type": p_type,
        "rating": default_rating,
        "wins": 0,
        "losses": 0,
    }
    sb["leaderboard"].append(new_e)
    return new_e


def _upsert_leaderboard_entry(sb: Dict[str, Any], name: str, p_type: str, rating: int) -> None:
    entry = _get_or_create_entry(sb, name, p_type, default_rating=rating)
    entry["rating"] = rating
    sb["leaderboard"].sort(key=lambda x: x.get("rating", 0), reverse=True)


def get_public_scoreboard() -> Dict[str, Any]:
    """Returns a client-safe snapshot of current champion and top rankings."""
    sb = load_scoreboard()
    champ = sb.get("champion", {})
    # Strip raw weights for compact network transport
    public_champ = {
        "name": champ.get("name", "Grandmaster"),
        "type": champ.get("type", "bot"),
        "rating": champ.get("rating", 1600),
        "title_defenses": champ.get("title_defenses", 0),
        "crowned_at": champ.get("crowned_at", ""),
        "mutation_tag": champ.get("mutation_tag", "Grandmaster_base"),
        "source_player": champ.get("source_player", "Grandmaster"),
    }

    # Top 8 public entries (excluding internal candidates)
    public_entries = [e for e in sb.get("leaderboard", []) if not is_internal_candidate(e.get("name", ""))]
    top_entries = public_entries[:8]
    return {
        "type": "scoreboard_data",
        "champion": public_champ,
        "leaderboard": top_entries,
    }
