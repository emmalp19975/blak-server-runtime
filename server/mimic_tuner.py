"""
server/mimic_tuner.py
=====================
Real-Time Preference Learning & Player Mimic Engine for Project Blak.
Learns from human player moves, adjusts heuristic weights dynamically,
stores profiles per gamertag, and hot-reloads "Mimic (Me Tuned)" bots live.
"""

from __future__ import annotations
import copy
from datetime import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from sandbox.planner import DIFFICULTY_PROFILES, PlanAction, PlannerConfig, TurnPlan, TurnPlanner

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PROFILES_DIR = os.path.join(PROJECT_ROOT, "data", "profiles")


def _sanitize_gamertag(gamertag: str) -> str:
    """Sanitizes gamertag for safe filesystem storage."""
    safe = re.sub(r'[^a-zA-Z0-9_\-]', '_', gamertag.strip())
    return safe or "Player"


def get_profile_path(gamertag: str) -> str:
    """Returns absolute path to the player's profile JSON."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    return os.path.join(PROFILES_DIR, f"{_sanitize_gamertag(gamertag)}.json")


def load_profile(gamertag: str) -> PlannerConfig:
    """Loads saved PlannerConfig for gamertag, falling back to balanced Tactical baseline."""
    path = get_profile_path(gamertag)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            weights = data.get("weights", {})
            enable_opp_obj = bool(data.get("enable_opp_objective_play", False))
            opp_obj_sac = float(weights.get("w_opp_obj_sacrifice", 0.0))
            cfg = PlannerConfig(
                name=f"Mimic ({gamertag})",
                beam_width=int(data.get("beam_width", 16)),
                max_depth=int(data.get("max_depth", 10)),
                w_objective=float(weights.get("w_objective", 1750.0)),
                w_hand_flush=float(weights.get("w_hand_flush", 400.0)),
                w_ladder_perk=float(weights.get("w_ladder_perk", 250.0)),
                w_anti_feed=max(180.0, float(weights.get("w_anti_feed", 240.0))),
                w_capping=float(weights.get("w_capping", 80.0)),
                w_discard_steal=float(weights.get("w_discard_steal", 35.0)),
                w_opp_obj_sacrifice=opp_obj_sac,
                enable_opp_objective_play=enable_opp_obj,
                w_endgame_threat_veto=float(weights.get("w_endgame_threat_veto", 1500.0)),
                w_joker_conservation=max(220.0, float(weights.get("w_joker_conservation", 280.0))),
                w_omni_preservation=float(weights.get("w_omni_preservation", 180.0)),
                w_discard_unlock=float(weights.get("w_discard_unlock", 150.0)),
                w_hand_diversity=float(weights.get("w_hand_diversity", 35.0)),
            )
            print(f"[MimicTuner] Loaded profile for '{gamertag}' (Moves learned: {data.get('total_moves_learned', 0)})")
            return cfg
        except Exception as e:
            print(f"[MimicTuner] Error loading profile for '{gamertag}': {e}. Using baseline.")

    # Initialize new profile from calibrated Tactical baseline
    baseline = copy.deepcopy(DIFFICULTY_PROFILES["Tactical"])
    baseline.name = f"Mimic ({gamertag})"
    baseline.beam_width = 16
    baseline.max_depth = 10
    baseline.w_joker_conservation = 280.0
    baseline.w_anti_feed = 240.0
    baseline.w_opp_obj_sacrifice = 0.0
    baseline.enable_opp_objective_play = False
    save_profile(gamertag, baseline, total_moves=0)
    return baseline


def save_profile(
    gamertag: str,
    config: PlannerConfig,
    total_moves: int = 0,
    prediction_history: Optional[List[bool]] = None,
    champion_prediction_history: Optional[List[bool]] = None,
    turn_history: Optional[List[float]] = None,
    champion_turn_history: Optional[List[float]] = None,
) -> None:
    """Serializes PlannerConfig and prediction metrics (per-play and per-turn) to data/profiles/<gamertag>.json."""
    path = get_profile_path(gamertag)
    p_hist = prediction_history or []
    c_hist = champion_prediction_history or []
    t_hist = turn_history or []
    ct_hist = champion_turn_history or []

    p_acc = round((sum(p_hist) / len(p_hist)) * 100.0, 1) if p_hist else 0.0
    c_acc = round((sum(c_hist) / len(c_hist)) * 100.0, 1) if c_hist else 0.0
    t_acc = round((sum(t_hist) / len(t_hist)), 1) if t_hist else 0.0
    ct_acc = round((sum(ct_hist) / len(ct_hist)), 1) if ct_hist else 0.0

    payload = {
        "gamertag": gamertag,
        "total_moves_learned": total_moves,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # Per-Play (Move) Accuracy
        "prediction_accuracy_pct": p_acc,
        "play_accuracy_pct": p_acc,
        "prediction_history": p_hist,
        "play_prediction_history": p_hist,
        "champion_prediction_accuracy_pct": c_acc,
        "champion_play_accuracy_pct": c_acc,
        "champion_prediction_history": c_hist,
        # Per-Turn (Order-Independent Card Overlap) Accuracy
        "turn_accuracy_pct": t_acc,
        "turn_history": t_hist,
        "champion_turn_accuracy_pct": ct_acc,
        "champion_turn_history": ct_hist,
        "enable_opp_objective_play": bool(getattr(config, "enable_opp_objective_play", False)),
        "beam_width": config.beam_width,
        "max_depth": config.max_depth,
        "weights": {
            "w_objective": round(config.w_objective, 1),
            "w_hand_flush": round(config.w_hand_flush, 1),
            "w_ladder_perk": round(config.w_ladder_perk, 1),
            "w_anti_feed": round(config.w_anti_feed, 1),
            "w_capping": round(config.w_capping, 1),
            "w_discard_steal": round(config.w_discard_steal, 1),
            "w_opp_obj_sacrifice": round(config.w_opp_obj_sacrifice, 1),
            "w_endgame_threat_veto": round(config.w_endgame_threat_veto, 1),
            "w_joker_conservation": round(config.w_joker_conservation, 1),
            "w_omni_preservation": round(config.w_omni_preservation, 1),
            "w_discard_unlock": round(config.w_discard_unlock, 1),
            "w_hand_diversity": round(config.w_hand_diversity, 1),
        }
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def get_profile_prediction_stats(gamertag: str) -> Dict[str, Any]:
    """Returns dual prediction metrics (per-play and per-turn) for the specified gamertag."""
    path = get_profile_path(gamertag)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "gamertag": gamertag,
                "total_moves": data.get("total_moves_learned", 0),
                "accuracy_pct": data.get("play_accuracy_pct", data.get("prediction_accuracy_pct", 0.0)),
                "play_accuracy_pct": data.get("play_accuracy_pct", data.get("prediction_accuracy_pct", 0.0)),
                "turn_accuracy_pct": data.get("turn_accuracy_pct", 0.0),
                "champion_accuracy_pct": data.get("champion_play_accuracy_pct", data.get("champion_prediction_accuracy_pct", 0.0)),
                "champion_play_accuracy_pct": data.get("champion_play_accuracy_pct", data.get("champion_prediction_accuracy_pct", 0.0)),
                "champion_turn_accuracy_pct": data.get("champion_turn_accuracy_pct", 0.0),
                "tracked_moves": len(data.get("prediction_history", [])),
                "tracked_plays": len(data.get("play_prediction_history", data.get("prediction_history", []))),
                "tracked_turns": len(data.get("turn_history", [])),
            }
        except Exception:
            pass
    return {
        "gamertag": gamertag,
        "total_moves": 0,
        "accuracy_pct": 0.0,
        "play_accuracy_pct": 0.0,
        "turn_accuracy_pct": 0.0,
        "champion_accuracy_pct": 0.0,
        "champion_play_accuracy_pct": 0.0,
        "champion_turn_accuracy_pct": 0.0,
        "tracked_moves": 0,
        "tracked_plays": 0,
        "tracked_turns": 0,
    }


def check_prediction_match(
    action_type: str,
    action_data: Dict[str, Any],
    plan: Optional[TurnPlan],
    engine: Any,
    gamertag: str,
) -> bool:
    """Checks whether the bot planner correctly predicted the human player's action."""
    if plan is None:
        return False

    if action_type == "play_card":
        if not plan.actions:
            return False
        first_act = plan.actions[0]
        target_slot = int(action_data.get("target_slot", -1))
        if first_act.slot_idx != target_slot:
            return False

        source = str(action_data.get("source", "hand"))
        owner_id = str(action_data.get("owner_id", gamertag))

        if source == "hand":
            if first_act.action_type != "hand":
                return False
            card_idx = int(action_data.get("card_index", -1))
            hand = engine.players[gamertag].get("hand", [])
            actual_card = hand[card_idx] if 0 <= card_idx < len(hand) else None
            if first_act.ref is not None and int(first_act.ref) == card_idx:
                return True
            if actual_card and first_act.card:
                return (actual_card.get("type") == first_act.card.get("type") and
                        actual_card.get("value") == first_act.card.get("value"))
            return False

        elif source == "objective":
            if owner_id == gamertag:
                return first_act.action_type == "objective"
            else:
                return first_act.action_type == "opponent_objective" and str(first_act.ref) == owner_id

        elif source == "discard":
            if first_act.action_type != "discard":
                return False
            discard_slot = int(action_data.get("slot_index", action_data.get("discard_slot", 0)))
            if isinstance(first_act.ref, (list, tuple)) and len(first_act.ref) == 2:
                b_owner, b_slot = first_act.ref
                return (b_owner == owner_id and int(b_slot) == discard_slot)
            return True

        return False

    elif action_type in ("initiate_ladder_perk", "decline_ladder_perk"):
        if not plan.actions:
            return False
        first_act = plan.actions[0]
        if action_type == "initiate_ladder_perk":
            return first_act.action_type == "perk"
        else:
            return first_act.action_type == "decline_perk"

    elif action_type == "discard_card":
        h_idx = int(action_data.get("card_index", action_data.get("hand_index", -1)))
        d_slot = int(action_data.get("slot_index", action_data.get("discard_slot", -1)))

        planned_moves = plan.discard_moves or []
        if not planned_moves and plan.discard_card_idx is not None and plan.discard_slot_idx is not None:
            planned_moves = [(plan.discard_card_idx, plan.discard_slot_idx)]
        if not planned_moves:
            return False

        hand = engine.players[gamertag].get("hand", [])
        actual_card = hand[h_idx] if 0 <= h_idx < len(hand) else None

        # Check match against any planned discard move in sequence
        for bot_c_idx, bot_s_idx in planned_moves:
            if bot_s_idx == d_slot:
                if bot_c_idx == h_idx:
                    return True
                if actual_card and 0 <= bot_c_idx < len(hand):
                    c2 = hand[bot_c_idx]
                    if (c2.get("type") == actual_card.get("type") and
                        c2.get("value") == actual_card.get("value")):
                        return True
        return False

    return False


# Active turn state tracking for order-independent per-turn evaluation
# Maps gamertag -> {
#     "p_plan_plays": List[Tuple[str, Any]],
#     "p_plan_discs": List[Tuple[str, Any]],
#     "c_plan_plays": List[Tuple[str, Any]],
#     "c_plan_discs": List[Tuple[str, Any]],
#     "actual_plays": List[Tuple[str, Any]],
#     "actual_discards": List[Tuple[str, Any]],
# }
_ACTIVE_TURNS: Dict[str, Dict[str, Any]] = {}


def _extract_plan_items(
    plan: Optional[TurnPlan],
    engine: Any,
    gamertag: str,
) -> Tuple[List[Tuple[str, Any]], List[Tuple[str, Any]]]:
    """Extracts planned card plays and discards from a TurnPlan for turn-level evaluation."""
    planned_plays: List[Tuple[str, Any]] = []
    planned_discards: List[Tuple[str, Any]] = []
    if not plan:
        return planned_plays, planned_discards

    hand = engine.players.get(gamertag, {}).get("hand", [])
    for act in plan.actions:
        if act.action_type in ("perk", "decline_perk"):
            continue
        c_val = None
        if act.card:
            c_val = act.card.get("value") if act.card.get("type") == "number" else "J"
        elif act.action_type == "hand" and act.ref is not None:
            idx = int(act.ref)
            if 0 <= idx < len(hand):
                c = hand[idx]
                c_val = c.get("value") if c.get("type") == "number" else "J"
        elif act.action_type == "objective":
            top_c = engine.get_objective_top_card(gamertag)
            if top_c:
                c_val = top_c.get("value") if top_c.get("type") == "number" else "J"
        elif act.action_type == "opponent_objective" and act.ref:
            top_c = engine.get_objective_top_card(str(act.ref))
            if top_c:
                c_val = top_c.get("value") if top_c.get("type") == "number" else "J"
        elif act.action_type == "discard" and isinstance(act.ref, (list, tuple)):
            owner_id, s_idx = act.ref
            piles = engine.players.get(owner_id, {}).get("discard_piles", [[], [], [], []])
            if 0 <= s_idx < len(piles) and piles[s_idx]:
                top_c = piles[s_idx][-1]
                c_val = top_c.get("value") if top_c.get("type") == "number" else "J"

        if c_val is not None:
            planned_plays.append((act.action_type, c_val))

    disc_moves = plan.discard_moves or []
    if not disc_moves and plan.discard_card_idx is not None:
        disc_moves = [(plan.discard_card_idx, plan.discard_slot_idx)]

    for c_idx, s_idx in disc_moves:
        if 0 <= c_idx < len(hand):
            c = hand[c_idx]
            c_val = c.get("value") if c.get("type") == "number" else "J"
            planned_discards.append(("discard", c_val))

    return planned_plays, planned_discards


def _calculate_turn_overlap(
    planned_plays: List[Tuple[str, Any]],
    planned_discards: List[Tuple[str, Any]],
    actual_plays: List[Tuple[str, Any]],
    actual_discards: List[Tuple[str, Any]],
) -> float:
    """
    Computes order-independent multiset overlap percentage between planned
    and executed cards during a turn.
    """
    from collections import Counter  # Deferred import: circular dependency
    p_play_counts = Counter(planned_plays)
    a_play_counts = Counter(actual_plays)
    common_plays = sum((p_play_counts & a_play_counts).values())
    max_plays = max(len(planned_plays), len(actual_plays))

    p_disc_counts = Counter(planned_discards)
    a_disc_counts = Counter(actual_discards)
    common_discs = sum((p_disc_counts & a_disc_counts).values())
    max_discs = max(len(planned_discards), len(actual_discards))

    total_common = common_plays + common_discs
    total_max = max_plays + max_discs
    if total_max == 0:
        return 100.0
    return round((total_common / total_max) * 100.0, 1)


def learn_from_move(
    gamertag: str,
    engine: Any,
    action_type: str,
    action_data: Dict[str, Any],
    room: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Analyzes human gameplay decisions against both the player's personal Mimic
    profile and the shadow Evolving Champion candidate.
    Tracks both per-play (move) and per-turn (order-independent card set) accuracy.
    Maintains 100% loyalty to the player's playstyle without Champion overwrites.
    """
    # Guard: Only learn from human players, not automated bot turns
    if not gamertag or gamertag.startswith("Bot "):
        return

    SUPPORTED_ACTIONS = ("play_card", "discard_card", "initiate_ladder_perk", "decline_ladder_perk")
    if action_type not in SUPPORTED_ACTIONS:
        return

    # Check that player exists in engine
    if gamertag not in getattr(engine, "players", {}):
        return

    try:
        # 1. Load player's current profile and prediction history
        path = get_profile_path(gamertag)
        total_moves = 0
        p_history: List[bool] = []
        c_history: List[bool] = []
        t_history: List[float] = []
        ct_history: List[float] = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
                total_moves = int(d.get("total_moves_learned", 0))
                p_history = list(d.get("play_prediction_history", d.get("prediction_history", [])))
                c_history = list(d.get("champion_play_history", d.get("champion_prediction_history", [])))
                t_history = list(d.get("turn_history", []))
                ct_history = list(d.get("champion_turn_history", []))

        config = load_profile(gamertag)
        planner = TurnPlanner(gamertag, config=config)

        # 2. Champion Tuned Variation
        from server.scoreboard import (  # Deferred import: circular dependency
            CHAMPION_EVOLVING_PATH,
            get_evolving_champion_config,
            save_evolving_champion_config,
        )
        champ_candidate_cfg = get_evolving_champion_config()
        champ_planner = TurnPlanner(f"Champ_{gamertag}", config=champ_candidate_cfg)

        # 3. Turn Tracking Initialization (capture initial plans at start of turn)
        turn_data = _ACTIVE_TURNS.get(gamertag)
        if turn_data is None:
            p_init_plan = planner.plan_turn(engine)
            c_init_plan = champ_planner.plan_turn(engine)
            p_plan_plays, p_plan_discs = _extract_plan_items(p_init_plan, engine, gamertag)
            c_plan_plays, c_plan_discs = _extract_plan_items(c_init_plan, engine, gamertag)
            turn_data = {
                "p_plan_plays": p_plan_plays,
                "p_plan_discs": p_plan_discs,
                "c_plan_plays": c_plan_plays,
                "c_plan_discs": c_plan_discs,
                "actual_plays": [],
                "actual_discards": [],
            }
            _ACTIVE_TURNS[gamertag] = turn_data

        # 4. Immediate Per-Play Move Prediction Evaluation
        plan = planner.plan_turn(engine)
        bot_action = plan.actions[0] if plan.actions else None
        p_correct = check_prediction_match(action_type, action_data, plan, engine, gamertag)
        p_history.append(p_correct)
        if len(p_history) > 100:
            p_history.pop(0)

        champ_plan = champ_planner.plan_turn(engine)
        c_correct = check_prediction_match(action_type, action_data, champ_plan, engine, gamertag)
        c_history.append(c_correct)
        if len(c_history) > 100:
            c_history.pop(0)

        p_acc = round((sum(p_history) / len(p_history)) * 100.0, 1) if p_history else 0.0
        c_acc = round((sum(c_history) / len(c_history)) * 100.0, 1) if c_history else 0.0

        # 5. Record executed action into turn state
        if action_type == "play_card":
            src = str(action_data.get("source", "hand"))
            owner = str(action_data.get("owner_id", gamertag))
            c_val = None
            if src == "hand":
                idx = int(action_data.get("card_index", -1))
                h = engine.players.get(gamertag, {}).get("hand", [])
                if 0 <= idx < len(h):
                    c = h[idx]
                    c_val = c.get("value") if c.get("type") == "number" else "J"
            elif src == "objective":
                top_c = engine.get_objective_top_card(owner)
                if top_c:
                    c_val = top_c.get("value") if top_c.get("type") == "number" else "J"
            elif src == "discard":
                slot = int(action_data.get("slot_index", action_data.get("discard_slot", 0)))
                piles = engine.players.get(owner, {}).get("discard_piles", [[], [], [], []])
                if 0 <= slot < len(piles) and piles[slot]:
                    top_c = piles[slot][-1]
                    c_val = top_c.get("value") if top_c.get("type") == "number" else "J"
            if c_val is not None:
                turn_data["actual_plays"].append((src, c_val))

        elif action_type == "discard_card":
            idx = int(action_data.get("card_index", action_data.get("hand_index", -1)))
            h = engine.players.get(gamertag, {}).get("hand", [])
            if 0 <= idx < len(h):
                c = h[idx]
                c_val = c.get("value") if c.get("type") == "number" else "J"
                turn_data["actual_discards"].append(("discard", c_val))

        # 6. Check if this move concludes the turn
        is_turn_end = False
        if action_type == "discard_card":
            end_turn = bool(action_data.get("end_turn", True))
            cur_discs = getattr(engine, "discards_this_turn", 0) + 1
            if end_turn or cur_discs >= 2:
                is_turn_end = True
        elif action_type == "end_turn":
            is_turn_end = True

        if is_turn_end:
            # Order-independent turn accuracy evaluation
            p_turn_score = _calculate_turn_overlap(
                turn_data["p_plan_plays"], turn_data["p_plan_discs"],
                turn_data["actual_plays"], turn_data["actual_discards"]
            )
            t_history.append(p_turn_score)
            if len(t_history) > 30:
                t_history.pop(0)

            c_turn_score = _calculate_turn_overlap(
                turn_data["c_plan_plays"], turn_data["c_plan_discs"],
                turn_data["actual_plays"], turn_data["actual_discards"]
            )
            ct_history.append(c_turn_score)
            if len(ct_history) > 30:
                ct_history.pop(0)

            _ACTIVE_TURNS.pop(gamertag, None)

        # 7. Incremental gradient adjustments to player profile (with decaying learning rate for stability)
        effective_lr = max(1.0, 12.0 / (1.0 + 0.05 * total_moves))
        _apply_tactical_adjustments(config, engine, gamertag, action_type, action_data, bot_action, lr=effective_lr)

        # 8. Save updated player profile
        total_moves += 1
        save_profile(
            gamertag,
            config,
            total_moves=total_moves,
            prediction_history=p_history,
            champion_prediction_history=c_history,
            turn_history=t_history,
            champion_turn_history=ct_history,
        )

        # 6. Hot-Reload Active Mimic Bots in the same room
        if room and "bots" in room:
            for bot_name, bot_inst in room["bots"].items():
                if hasattr(bot_inst, "profile_name") and bot_inst.profile_name.startswith(f"Mimic ({gamertag})"):
                    if hasattr(bot_inst, "update_config"):
                        bot_inst.update_config(config)
                        print(f"[MimicTuner] Live-updated '{bot_name}' with {gamertag}'s new weights (moves: {total_moves}, accuracy: {p_acc}%).")

    except Exception as exc:
        print(f"[MimicTuner] Telemetry update warning: {exc}")


def _apply_tactical_adjustments(
    config: PlannerConfig,
    engine: Any,
    gamertag: str,
    action_type: str,
    action_data: Dict[str, Any],
    bot_action: Optional[Any],
    lr: float = 12.0,
) -> None:
    """Applies heuristic weight adjustments based on player board and discard decisions."""
    if action_type == "play_card":
        target_slot = int(action_data.get("target_slot", 0))
        source = str(action_data.get("source", "hand"))
        owner_id = str(action_data.get("owner_id", gamertag))
        slot = engine.board[target_slot] if target_slot < len(engine.board) else None

        # SENSOR CHECK A: Objective Priority vs Combo Building vs Opponent Objective Play
        if source == "objective":
            if owner_id == gamertag:
                # Human prioritized cashing in their objective card immediately
                config.w_objective = min(2600.0, config.w_objective + 20.0)
            else:
                # Human strategically played opponent's objective card to clear board space/unblock tempo!
                config.enable_opp_objective_play = True
                config.w_opp_obj_sacrifice = min(350.0, config.w_opp_obj_sacrifice + 25.0)
        elif source == "hand":
            # Check if human had their objective card playable on the board, but chose to hold it
            my_obj = engine.get_objective_top_card(gamertag)
            can_play_obj = False
            if my_obj and my_obj.get("type") == "number":
                o_val = int(my_obj.get("value", 0))
                for s in engine.board:
                    if not s.get("pending_perk"):
                        b_val = s.get("value", 0)
                        if (b_val == 0 and not s.get("is_omni_base") and o_val == 1) or (b_val > 0 and o_val == b_val + 1):
                            can_play_obj = True
                            break
            if can_play_obj:
                # Human deferred objective to build hand combos first (Combo Builder style)
                config.w_objective = max(1100.0, config.w_objective - 20.0)
                config.w_hand_flush = min(700.0, config.w_hand_flush + 15.0)

        # SENSOR CHECK B: Discard Steal (Opponent Pile Raiding)
        if source == "discard" and owner_id != gamertag:
            # Human stole a card from an opponent's discard pile
            config.w_discard_steal = min(80.0, config.w_discard_steal + 5.0)
        elif (
            bot_action
            and bot_action.action_type == "discard"
            and isinstance(bot_action.ref, (list, tuple))
            and bot_action.ref[0] != gamertag
            and source == "hand"
        ):
            # Bot recommended a discard steal, but human preferred playing own hand card
            config.w_discard_steal = max(10.0, config.w_discard_steal - 3.0)
        elif (
            bot_action
            and bot_action.action_type == "opponent_objective"
            and source in ("hand", "objective")
            and owner_id == gamertag
        ):
            # Bot recommended playing opponent objective, but human chose own cards
            config.w_opp_obj_sacrifice = max(0.0, config.w_opp_obj_sacrifice - 15.0)
            if config.w_opp_obj_sacrifice <= 0.0:
                config.enable_opp_objective_play = False

        # SENSOR CHECK C: Anti-Feed & Endgame Threat Defense
        is_feeding = False
        for p_id in engine.get_player_list():
            if p_id == gamertag:
                continue
            top_obj = engine.get_objective_top_card(p_id)
            rem = engine.get_objective_remaining(p_id)
            if top_obj and top_obj.get("type") == "number":
                val = int(top_obj.get("value", 0))
                if slot and slot.get("value") == val - 2:
                    is_feeding = True
                    if rem <= 2:
                        config.w_endgame_threat_veto = max(600.0, config.w_endgame_threat_veto - 24.0)
                        config.w_anti_feed = max(180.0, config.w_anti_feed - 12.0)
                    else:
                        config.w_anti_feed = max(180.0, config.w_anti_feed - 6.0)

        # Did bot want to feed, but player avoided it?
        if bot_action and not is_feeding:
            bot_target = bot_action.slot_idx
            if 0 <= bot_target < len(engine.board):
                b_slot = engine.board[bot_target]
                for p_id in engine.get_player_list():
                    if p_id == gamertag:
                        continue
                    top_obj = engine.get_objective_top_card(p_id)
                    rem = engine.get_objective_remaining(p_id)
                    if top_obj and top_obj.get("type") == "number":
                        if b_slot.get("value") == int(top_obj.get("value", 0)) - 2:
                            config.w_anti_feed = min(500.0, config.w_anti_feed + 12.0)
                            if rem <= 2:
                                config.w_endgame_threat_veto = min(2200.0, config.w_endgame_threat_veto + 24.0)

        # SENSOR CHECK D: Joker Conservation
        played_card = None
        if source == "hand":
            h_idx = int(action_data.get("card_index", 0))
            p_hand = engine.players[gamertag].get("hand", [])
            if 0 <= h_idx < len(p_hand):
                played_card = p_hand[h_idx]

        if played_card and played_card.get("type") == "joker":
            is_critical = False
            my_obj = engine.get_objective_top_card(gamertag)
            if my_obj and slot and slot.get("value") == int(my_obj.get("value", 0)) - 1:
                is_critical = True
            if slot and slot.get("value") == 11:
                is_critical = True

            if not is_critical:
                config.w_joker_conservation = max(220.0, config.w_joker_conservation - 12.0)
            else:
                config.w_joker_conservation = min(500.0, config.w_joker_conservation + 8.0)

        # SENSOR CHECK E: Capping (12 on 11)
        played_val = int(played_card.get("value", 0)) if played_card and played_card.get("type") == "number" else 0
        if slot and slot.get("value") == 11 and played_val == 12:
            config.w_capping = min(120.0, config.w_capping + 6.0)

        # SENSOR CHECK F: Omni-Base Slot Preservation
        if slot and slot.get("is_omni_base"):
            val = int(played_card.get("value", 0)) if played_card and played_card.get("type") == "number" else 1
            if val >= 9:
                config.w_omni_preservation = max(80.0, config.w_omni_preservation - 10.0)
            else:
                config.w_omni_preservation = min(350.0, config.w_omni_preservation + 10.0)

        # SENSOR CHECK G: Ladder Perk Priority (Slot at 11)
        if slot and slot.get("value") == 11:
            config.w_ladder_perk = min(350.0, config.w_ladder_perk + 8.0)

        # SENSOR CHECK H: Hand Flush Velocity
        p_hand_len = len(engine.players[gamertag].get("hand", []))
        if p_hand_len == 1:
            config.w_hand_flush = min(600.0, config.w_hand_flush + 15.0)

    elif action_type in ("initiate_ladder_perk", "decline_ladder_perk"):
        my_obj = engine.get_objective_top_card(gamertag)
        obj_val = int(my_obj.get("value", 0)) if my_obj and my_obj.get("type") == "number" else 0
        if action_type == "initiate_ladder_perk":
            # Human accepted the perk jump
            config.w_ladder_perk = min(400.0, config.w_ladder_perk + 12.0)
        else:
            # Human declined the perk
            if obj_val >= 7:
                # Declined even on high card: strong preference for board purity over random jump
                config.w_ladder_perk = max(80.0, config.w_ladder_perk - 10.0)
            else:
                # Smart decline on low objective card
                config.w_ladder_perk = min(350.0, config.w_ladder_perk + 5.0)

    elif action_type == "discard_card":
        d_slot = int(action_data.get("discard_slot", action_data.get("slot_index", 0)))
        h_idx = int(action_data.get("hand_index", action_data.get("card_index", 0)))
        p_hand = engine.players[gamertag].get("hand", [])
        p_discards = engine.players[gamertag].get("discard_piles", [[], [], [], []])

        if 0 <= h_idx < len(p_hand) and 0 <= d_slot < len(p_discards):
            card = p_hand[h_idx]
            c_val = int(card.get("value", 0)) if card.get("type") == "number" else 0
            pile = p_discards[d_slot]
            if pile:
                top_c = pile[-1]
                t_val = int(top_c.get("value", 0)) if top_c.get("type") == "number" else 0
                if t_val >= c_val:
                    # Clean Solitaire descending stack
                    config.w_discard_unlock = min(250.0, config.w_discard_unlock + 6.0)
                else:
                    # Inverted stack (buried lower card)
                    config.w_discard_unlock = max(50.0, config.w_discard_unlock - 6.0)


def get_ai_hint(gamertag: str, engine: Any) -> Optional[Dict[str, Any]]:
    """
    Computes real-time decision recommendations for the player's current turn
    using their personalized mimic profile.
    Returns:
        {
            "play_source": "hand" | "objective" | None,
            "play_card_index": int (hand index or None),
            "play_slot_index": int (target board slot or None),
            "discard_card_index": int (recommended discard hand index or None),
            "discard_slot_index": int (recommended discard pile slot or None),
            "net_score": float,
        }
    """
    if not gamertag or engine.get_active_player() != gamertag:
        return None

    try:
        config = load_profile(gamertag)
        planner = TurnPlanner(gamertag, config=config)
        plan = planner.plan_turn(engine)

        play_source = None
        play_card_index = None
        play_slot_index = None
        play_owner_id = None
        play_discard_slot = None

        if plan.actions:
            first_act = plan.actions[0]
            play_slot_index = first_act.slot_idx
            if first_act.action_type == "hand":
                play_source = "hand"
                play_owner_id = gamertag
                if first_act.ref is not None:
                    play_card_index = int(first_act.ref)
            elif first_act.action_type == "objective":
                play_source = "objective"
                play_owner_id = gamertag
            elif first_act.action_type == "opponent_objective":
                play_source = "objective"
                play_owner_id = str(first_act.ref)
            elif first_act.action_type == "discard":
                play_source = "discard"
                if isinstance(first_act.ref, (list, tuple)) and len(first_act.ref) >= 2:
                    play_owner_id = str(first_act.ref[0])
                    play_discard_slot = int(first_act.ref[1])
                else:
                    play_owner_id = gamertag
                    if first_act.ref is not None:
                        play_discard_slot = int(first_act.ref)
            else:
                play_source = first_act.action_type

        discard_card_index = None
        discard_slot_index = plan.discard_slot_idx
        if plan.discard_moves:
            discard_card_index = int(plan.discard_moves[0][0])
            discard_slot_index = int(plan.discard_moves[0][1])
        elif plan.discard_card_idx is not None:
            discard_card_index = int(plan.discard_card_idx)

        return {
            "play_source": play_source,
            "play_card_index": play_card_index,
            "play_slot_index": play_slot_index,
            "play_owner_id": play_owner_id,
            "play_discard_slot": play_discard_slot,
            "discard_card_index": discard_card_index,
            "discard_slot_index": discard_slot_index,
            "net_score": round(plan.score, 1),
        }
    except Exception as exc:
        print(f"[MimicTuner] get_ai_hint error: {exc}")
        return None
