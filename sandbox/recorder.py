"""
sandbox/recorder.py
===================
Match & Turn Event Recorder for Blak.

Captures complete game trajectories (State-Action-Reward-Outcome tuples)
in high-performance, append-only JSON Lines (.jsonl) format.
Enables offline reinforcement learning (policy/value networks),
behavioral cloning, and heuristic balance analysis.
"""

from __future__ import annotations
import copy
import datetime
import json
import os
import uuid
from typing import Any, Dict, List, Optional


class GameRecorder:
    """
    Records game trajectories per match and writes to JSONL.
    """

    def __init__(
        self,
        match_id: Optional[str] = None,
        output_file: str = "data/training_data/matches.jsonl",
        source: str = "simulation",  # "simulation" | "production"
    ) -> None:
        self.match_id = match_id or str(uuid.uuid4())[:8]
        self.output_file = output_file
        self.source = source
        self.start_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.seed: Optional[int] = None
        self.player_count: int = 0
        self.players: List[Dict[str, Any]] = []
        self.turns: List[Dict[str, Any]] = []
        self.current_turn: Optional[Dict[str, Any]] = None
        self.winner: Optional[str] = None
        self.winner_defeated: str = "none"  # "human" | "dumbot" | "human_and_dumbot"
        self.match_type: str = "bot_self_play"  # "pvp" | "bot_self_play" | "mixed"
        self.total_turns: int = 0
        self.is_completed: bool = False

    def start_match(
        self,
        player_ids: List[str],
        seed: Optional[int] = None,
        player_types: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> None:
        """Initializes a new match recording session."""
        self.seed = seed
        self.player_count = len(player_ids)
        self.players = []
        types = player_types or {}

        for p_id in player_ids:
            p_info = types.get(p_id, {"type": "bot", "profile": "Tactical"})
            self.players.append({
                "id": p_id,
                "type": p_info.get("type", "bot"),
                "profile": p_info.get("profile"),
            })

    def start_turn(self, turn_number: int, active_player: str) -> None:
        """Marks the start of a player's turn."""
        if self.current_turn is not None and self.current_turn.get("steps"):
            self.turns.append(self.current_turn)

        self.current_turn = {
            "turn_number": turn_number,
            "player_id": active_player,
            "steps": [],
        }

    def record_step(
        self,
        engine: Any,
        action_type: str,
        action_params: Dict[str, Any],
        success: bool = True,
        reward: float = 0.0,
    ) -> None:
        """Records a single state-action transition within the active turn."""
        act_player = action_params.get("player_id") or engine.get_active_player()
        if self.current_turn is None or self.current_turn.get("player_id") != act_player:
            self.start_turn(len(self.turns) + 1, act_player)

        p_id = self.current_turn["player_id"]
        state_snapshot = self._capture_state_snapshot(engine, p_id)

        step_record = {
            "step_index": len(self.current_turn["steps"]),
            "state": state_snapshot,
            "action": {
                "type": action_type,
                **action_params,
            },
            "success": success,
            "reward": reward,
        }

        self.current_turn["steps"].append(step_record)

        if action_params.get("end_turn") is True or action_type == "end_turn":
            self.turns.append(self.current_turn)
            self.current_turn = None

    def _capture_state_snapshot(self, engine: Any, requestor_id: str) -> Dict[str, Any]:
        """Extracts sanitized state representation suitable for ML vectorization."""
        # 1. Board Slots
        board_data = []
        for s in engine.board:
            board_data.append({
                "value": int(s.get("value", 0)),
                "is_omni": bool(s.get("is_omni_base", False)),
                "pending_perk": bool(s.get("pending_perk", False)),
                "cards_count": len(s.get("cards", [])),
                "perk_owner": s.get("perk_owner"),
            })

        # 2. Player Hand & Objective
        player_dict = engine.players.get(requestor_id, {})
        hand = copy.deepcopy(player_dict.get("hand", []))
        discards = copy.deepcopy(player_dict.get("discard_piles", [[], [], [], []]))
        obj_rem = engine.get_objective_remaining(requestor_id)
        obj_top = copy.deepcopy(engine.get_objective_top_card(requestor_id))

        # 3. Visible Opponent States
        opponents = []
        for other_id in engine.get_player_list():
            if other_id == requestor_id:
                continue
            opponents.append({
                "id": other_id,
                "objective_remaining": engine.get_objective_remaining(other_id),
                "objective_top": copy.deepcopy(engine.get_objective_top_card(other_id)),
                "discard_tops": copy.deepcopy(engine.get_discard_tops(other_id)),
            })

        return {
            "board": board_data,
            "hand": hand,
            "discard_piles": discards,
            "objective_remaining": obj_rem,
            "objective_top_card": obj_top,
            "opponents": opponents,
            "discards_this_turn": getattr(engine, "discards_this_turn", 0),
            "main_deck_size": engine.get_main_deck_size(),
        }

    def end_match(self, winner_id: Optional[str]) -> None:
        """Concludes the match and assigns outcome rewards and opponent defeated tags."""
        if self.current_turn is not None and self.current_turn.get("steps"):
            self.turns.append(self.current_turn)
            self.current_turn = None

        self.winner = winner_id
        self.total_turns = len(self.turns)
        self.is_completed = True

        # Tag defeated opponent types
        if winner_id:
            opponents = [p for p in self.players if p.get("id") != winner_id]
            has_human_opp = any(p.get("type") == "human" for p in opponents)
            has_bot_opp = any(p.get("type") == "bot" for p in opponents)

            if has_human_opp and has_bot_opp:
                self.winner_defeated = "human_and_dumbot"
            elif has_human_opp:
                self.winner_defeated = "human"
            elif has_bot_opp:
                self.winner_defeated = "dumbot"
            else:
                self.winner_defeated = "none"
        else:
            self.winner_defeated = "none"

        has_any_human = any(p.get("type") == "human" for p in self.players)
        has_any_bot = any(p.get("type") == "bot" for p in self.players)
        if has_any_human and has_any_bot:
            self.match_type = "mixed"
        elif has_any_human:
            self.match_type = "pvp"
        else:
            self.match_type = "bot_self_play"

        # Assign terminal outcome to each step: +1.0 for winner, -1.0 for others
        for turn in self.turns:
            is_winner = (turn["player_id"] == winner_id)
            for step in turn["steps"]:
                step["match_outcome"] = 1.0 if is_winner else -1.0

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the entire match trajectory with first-class dataset tags."""
        return {
            "match_id": self.match_id,
            "source": self.source,
            "match_type": self.match_type,
            "winner": self.winner,
            "winner_defeated": self.winner_defeated,
            "seed": self.seed,
            "start_time": self.start_time,
            "player_count": self.player_count,
            "players": self.players,
            "total_turns": self.total_turns,
            "turns": self.turns,
        }

    def save(self, filepath: Optional[str] = None) -> str:
        """Appends the completed match as a JSON line to disk."""
        target_path = filepath or self.output_file
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        payload = json.dumps(self.to_dict())
        with open(target_path, "a", encoding="utf-8") as f:
            f.write(payload + "\n")

        return target_path
