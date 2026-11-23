"""
sandbox/planner.py
==================
Combinatorial Turn-Graph Planner for Blak / The Sandbox.

Computes optimal multi-step turn sequences using forward search over
simulated turn states, dynamic risk/reward threat evaluation, tactical
opponent objective plays (Stepping Stone & Perk Snatch), and Solitaire-column
discard organization.
"""

from __future__ import annotations
import copy
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class MatchPhase(str, Enum):
    EARLY = "early"
    MID = "mid"
    ENDGAME = "endgame"


@dataclass
class MatchStateContext:
    phase: MatchPhase
    own_remaining: int
    opp_min_remaining: int
    score_differential: int
    urgency: float
    board_entropy: float
    is_trailing_urgently: bool


class MatchStateEvaluator:
    """Evaluates the macro game state and classifies match lifecycle phases."""

    @staticmethod
    def evaluate(engine: Any, player_id: str) -> MatchStateContext:
        own_rem = engine.get_objective_remaining(player_id) if hasattr(engine, "get_objective_remaining") else 15
        opp_rems: List[int] = []
        if hasattr(engine, "get_player_list"):
            for p in engine.get_player_list():
                if p != player_id:
                    opp_rems.append(engine.get_objective_remaining(p))
        elif hasattr(engine, "players") and isinstance(engine.players, dict):
            for p, p_data in engine.players.items():
                if p != player_id:
                    opp_rems.append(p_data.get("objective_remaining", len(p_data.get("objective_cards", []))))
        opp_min = min(opp_rems) if opp_rems else 15

        if own_rem >= 11 and opp_min >= 11:
            phase = MatchPhase.EARLY
        elif own_rem <= 4 or opp_min <= 4:
            phase = MatchPhase.ENDGAME
        else:
            phase = MatchPhase.MID

        diff = own_rem - opp_min
        urgency = (16.0 - max(1, min(own_rem, opp_min))) / 15.0

        board_vals: List[int] = []
        if hasattr(engine, "board") and isinstance(engine.board, list):
            for s in engine.board:
                v = s.get("value", 0) if isinstance(s, dict) else getattr(s, "value", 0)
                board_vals.append(int(v))
        board_entropy = sum(board_vals) / len(board_vals) if board_vals else 0.0

        is_trailing = (diff > 0 and opp_min <= 3)

        return MatchStateContext(
            phase=phase,
            own_remaining=own_rem,
            opp_min_remaining=opp_min,
            score_differential=diff,
            urgency=urgency,
            board_entropy=board_entropy,
            is_trailing_urgently=is_trailing,
        )


@dataclass
class PlannerConfig:
    """Configurable weights and search parameters defining bot personality and difficulty."""
    name: str = "Grandmaster"
    beam_width: int = 18
    max_depth: int = 12
    w_objective: float = 1500.0
    w_hand_flush: float = 350.0
    w_ladder_perk: float = 120.0
    w_anti_feed: float = 300.0
    w_capping: float = 60.0
    w_discard_steal: float = 30.0
    w_opp_obj_sacrifice: float = 200.0
    enable_opp_objective_play: bool = True
    enable_phase_modulation: bool = True  # Dynamically adapts weights across Early, Mid, and Endgame match phases
    random_noise: float = 0.0

    # Advanced Strategic Sensors & Weights
    w_endgame_threat_veto: float = 1200.0  # Decisive veto when opponent has <= 4 cards left
    w_joker_conservation: float = 280.0    # Penalizes wasting Jokers on non-milestone plays
    w_omni_preservation: float = 150.0     # Penalizes burning Omni-Base slots on high dead cards
    w_discard_unlock: float = 50.0         # Rewards priming board slots to unlock own discard piles
    w_hand_diversity: float = 30.0         # Rewards maintaining diverse card values for next turn
    # Dialable Cheating / Server-Side Hidden State Peeking
    cheat_level: float = 0.0               # Master dial: 0.0 (honest) to 1.0 (fully omniscient)
    cheat_hand_vision: float = 0.0        # Probability [0.0..1.0] of seeing each opponent hand card
    cheat_objective_lookahead: int = 0    # Number of face-down upcoming objective cards revealed [0..3]
    cheat_deck_lookahead: int = 0         # Number of main deck cards known in advance [0..5]
    cheat_rubberband: bool = False        # Dynamic comeback mode: scales cheat parameters based on score deficit
    enable_hand_peeking: bool = False     # Legacy flag (maps to cheat_hand_vision >= 1.0)


DIFFICULTY_PROFILES: Dict[str, PlannerConfig] = {
    # =========================================================================
    # THE 5 OFFICIAL CALIBRATED TIERS (Tournament & Ladder Verified)
    # =========================================================================
    "Novice": PlannerConfig(
        name="Novice",
        beam_width=2,
        max_depth=2,
        w_objective=900.0,
        w_hand_flush=60.0,
        w_ladder_perk=20.0,
        w_anti_feed=0.0,
        w_capping=0.0,
        w_discard_steal=0.0,
        enable_opp_objective_play=False,
        random_noise=25.0,
        w_endgame_threat_veto=0.0,
        w_joker_conservation=0.0,
        w_omni_preservation=0.0,
        w_discard_unlock=0.0,
        w_hand_diversity=0.0,
    ),
    "Apprentice": PlannerConfig(
        name="Apprentice",
        beam_width=3,
        max_depth=3,
        w_objective=1000.0,
        w_hand_flush=150.0,
        w_ladder_perk=60.0,
        w_anti_feed=30.0,
        w_capping=10.0,
        w_discard_steal=10.0,
        enable_opp_objective_play=True,
        random_noise=8.0,
        w_endgame_threat_veto=100.0,
        w_joker_conservation=50.0,
        w_omni_preservation=20.0,
        w_discard_unlock=10.0,
        w_hand_diversity=5.0,
    ),
    "Adept": PlannerConfig(
        name="Adept",
        beam_width=7,
        max_depth=6,
        w_objective=1300.0,
        w_hand_flush=250.0,
        w_ladder_perk=90.0,
        w_anti_feed=280.0,
        w_capping=60.0,
        w_discard_steal=20.0,
        w_opp_obj_sacrifice=240.0,
        enable_opp_objective_play=True,
        random_noise=0.0,
        w_endgame_threat_veto=1000.0,
        w_joker_conservation=220.0,
        w_omni_preservation=120.0,
        w_discard_unlock=30.0,
        w_hand_diversity=20.0,
    ),
    "Master": PlannerConfig(
        name="Master",
        beam_width=10,
        max_depth=9,
        w_objective=1600.0,
        w_hand_flush=350.0,
        w_ladder_perk=130.0,
        w_anti_feed=260.0,
        w_capping=60.0,
        w_discard_steal=35.0,
        w_opp_obj_sacrifice=200.0,
        enable_opp_objective_play=True,
        random_noise=0.0,
        w_endgame_threat_veto=1100.0,
        w_joker_conservation=240.0,
        w_omni_preservation=140.0,
        w_discard_unlock=50.0,
        w_hand_diversity=25.0,
        cheat_level=0.35,
        cheat_hand_vision=0.35,
        cheat_deck_lookahead=3,
    ),
    "Absolute King": PlannerConfig(
        name="Absolute King",
        beam_width=14,
        max_depth=12,
        w_objective=1900.0,
        w_hand_flush=440.0,
        w_ladder_perk=170.0,
        w_anti_feed=340.0,
        w_capping=75.0,
        w_discard_steal=42.0,
        w_opp_obj_sacrifice=210.0,
        enable_opp_objective_play=True,
        random_noise=0.0,
        w_endgame_threat_veto=1400.0,
        w_joker_conservation=270.0,
        w_omni_preservation=155.0,
        w_discard_unlock=65.0,
        w_hand_diversity=30.0,
        cheat_level=0.75,
        cheat_hand_vision=0.75,
        cheat_deck_lookahead=5,
        enable_hand_peeking=True,
    ),

    # =========================================================================
    # CLASSIC / LEGACY PROFILES & ALIASES
    # =========================================================================
    "Grandmaster": PlannerConfig(
        name="Grandmaster",
        beam_width=18,
        max_depth=12,
        w_objective=1750.0,
        w_hand_flush=400.0,
        w_ladder_perk=150.0,
        w_anti_feed=300.0,
        w_capping=80.0,
        w_discard_steal=35.0,
        w_opp_obj_sacrifice=200.0,
        enable_opp_objective_play=True,
        random_noise=0.0,
        w_endgame_threat_veto=1500.0,
        w_joker_conservation=280.0,
        w_omni_preservation=150.0,
        w_discard_unlock=50.0,
        w_hand_diversity=30.0,
    ),
    "Casual": PlannerConfig(
        name="Casual",
        beam_width=2,
        max_depth=3,
        w_objective=1000.0,
        w_hand_flush=150.0,
        w_ladder_perk=60.0,
        w_anti_feed=30.0,
        w_capping=10.0,
        w_discard_steal=10.0,
        enable_opp_objective_play=False,
        random_noise=10.0,
        w_endgame_threat_veto=100.0,
        w_joker_conservation=50.0,
        w_omni_preservation=20.0,
        w_discard_unlock=10.0,
        w_hand_diversity=5.0,
    ),
    "Tactical": PlannerConfig(
        name="Tactical",
        beam_width=8,
        max_depth=6,
        w_objective=1400.0,
        w_hand_flush=300.0,
        w_ladder_perk=100.0,
        w_anti_feed=180.0,
        w_capping=40.0,
        w_discard_steal=25.0,
        w_opp_obj_sacrifice=220.0,
        enable_opp_objective_play=True,
        random_noise=0.0,
        w_endgame_threat_veto=700.0,
        w_joker_conservation=180.0,
        w_omni_preservation=100.0,
        w_discard_unlock=35.0,
        w_hand_diversity=20.0,
    ),
    "Aggressive": PlannerConfig(
        name="Aggressive",
        beam_width=12,
        max_depth=10,
        w_objective=1800.0,
        w_hand_flush=500.0,
        w_ladder_perk=150.0,
        w_anti_feed=120.0,
        w_capping=30.0,
        w_discard_steal=35.0,
        w_opp_obj_sacrifice=140.0,
        enable_opp_objective_play=True,
        random_noise=0.0,
        w_endgame_threat_veto=350.0,
        w_joker_conservation=120.0,
        w_omni_preservation=80.0,
        w_discard_unlock=30.0,
        w_hand_diversity=15.0,
    ),
    "Oracle": PlannerConfig(
        name="Oracle",
        beam_width=18,
        max_depth=12,
        w_objective=1800.0,
        w_hand_flush=450.0,
        w_ladder_perk=160.0,
        w_anti_feed=350.0,
        w_capping=85.0,
        w_discard_steal=40.0,
        w_opp_obj_sacrifice=200.0,
        enable_opp_objective_play=True,
        random_noise=0.0,
        w_endgame_threat_veto=1600.0,
        w_joker_conservation=300.0,
        w_omni_preservation=160.0,
        w_discard_unlock=60.0,
        w_hand_diversity=35.0,
        cheat_rubberband=True,
    ),
    "Houdini": PlannerConfig(
        name="Houdini",
        beam_width=18,
        max_depth=12,
        w_objective=1800.0,
        w_hand_flush=450.0,
        w_ladder_perk=160.0,
        w_anti_feed=350.0,
        w_capping=85.0,
        w_discard_steal=40.0,
        w_opp_obj_sacrifice=200.0,
        enable_opp_objective_play=True,
        random_noise=0.0,
        w_endgame_threat_veto=1600.0,
        w_joker_conservation=300.0,
        w_omni_preservation=160.0,
        w_discard_unlock=60.0,
        w_hand_diversity=35.0,
        cheat_level=0.5,
        cheat_hand_vision=0.5,
        cheat_objective_lookahead=1,
        cheat_deck_lookahead=2,
    ),
    "Apex": PlannerConfig(
        name="Apex",
        beam_width=20,
        max_depth=14,
        w_objective=2200.0,
        w_hand_flush=500.0,
        w_ladder_perk=200.0,
        w_anti_feed=450.0,
        w_capping=90.0,
        w_discard_steal=45.0,
        w_opp_obj_sacrifice=220.0,
        enable_opp_objective_play=True,
        random_noise=0.0,
        w_endgame_threat_veto=2000.0,
        w_joker_conservation=350.0,
        w_omni_preservation=200.0,
        w_discard_unlock=80.0,
        w_hand_diversity=40.0,
        cheat_level=1.0,
        cheat_hand_vision=1.0,
        cheat_objective_lookahead=3,
        cheat_deck_lookahead=10,
        enable_hand_peeking=True,
    ),
}

# Aliases for Tier Labels
DIFFICULTY_PROFILES["Level 1: Novice"] = DIFFICULTY_PROFILES["Novice"]
DIFFICULTY_PROFILES["Level 2: Apprentice"] = DIFFICULTY_PROFILES["Apprentice"]
DIFFICULTY_PROFILES["Level 3: Adept"] = DIFFICULTY_PROFILES["Adept"]
DIFFICULTY_PROFILES["Level 4: Master"] = DIFFICULTY_PROFILES["Master"]
DIFFICULTY_PROFILES["Level 5: Absolute King"] = DIFFICULTY_PROFILES["Absolute King"]
DIFFICULTY_PROFILES["Level 6: Apex Mutant"] = DIFFICULTY_PROFILES["Apex"]
DIFFICULTY_PROFILES["Apex Mutant"] = DIFFICULTY_PROFILES["Apex"]
DIFFICULTY_PROFILES["Mutant"] = DIFFICULTY_PROFILES["Apex"]
DIFFICULTY_PROFILES["AbsoluteKing"] = DIFFICULTY_PROFILES["Absolute King"]
DIFFICULTY_PROFILES["King"] = DIFFICULTY_PROFILES["Absolute King"]


@dataclass
class SlotSim:
    value: int
    is_omni_base: bool
    pending_perk: bool
    cards_count: int = 0
    perk_owner: Optional[str] = None


@dataclass
class PlanAction:
    action_type: str  # "perk", "objective", "hand", "discard", "opponent_objective"
    slot_idx: int
    ref: Any = None  # hand_idx, opp_id, or (owner_id, discard_slot)
    card: Optional[Dict[str, Any]] = None
    description: str = ""


@dataclass
class TurnPlan:
    actions: List[PlanAction]
    discard_card_idx: int
    discard_slot_idx: int
    score: float
    objectives_scored: int = 0
    refills_triggered: int = 0
    discard_moves: List[Tuple[int, int]] = field(default_factory=list)


class TurnPlanner:
    """
    Evaluates whole-turn permutations to select the highest-utility
    multi-step action sequence for the active bot.
    """

    def __init__(self, bot_id: str, config: Optional[PlannerConfig] = None) -> None:
        self.bot_id = bot_id
        self.config = config or DIFFICULTY_PROFILES["Grandmaster"]

    # -------------------------------------------------------------------------
    # Public Entry Point
    # -------------------------------------------------------------------------
    def plan_turn(self, engine: Any) -> TurnPlan:
        """
        Plans the complete turn from current engine state.
        Returns a TurnPlan containing ordered actions and the optimal final discard.
        """
        # 1. Extract initial state snapshot
        board = [
            SlotSim(
                value=int(s.get("value", 0)),
                is_omni_base=bool(s.get("is_omni_base", False)),
                pending_perk=bool(s.get("pending_perk", False)),
                cards_count=len(s.get("cards", [])),
                perk_owner=s.get("perk_owner"),
            )
            for s in engine.board
        ]

        # Macro Match State Evaluation & Phase-Aware Modulation
        original_config = self.config
        ctx = MatchStateEvaluator.evaluate(engine, self.bot_id)
        if getattr(self.config, "enable_phase_modulation", True):
            active_cfg = copy.deepcopy(self.config)
            if ctx.phase == MatchPhase.EARLY:
                active_cfg.w_hand_flush *= 1.35
                active_cfg.w_discard_unlock *= 1.30
                active_cfg.w_anti_feed *= 0.60
            elif ctx.phase == MatchPhase.MID:
                active_cfg.w_ladder_perk *= 1.25
                active_cfg.w_capping *= 1.15
                active_cfg.w_discard_steal *= 1.25
            elif ctx.phase == MatchPhase.ENDGAME:
                active_cfg.w_endgame_threat_veto *= 1.80
                active_cfg.w_anti_feed *= 1.50
                active_cfg.w_capping *= 1.50
                active_cfg.w_hand_flush *= 0.70
                if ctx.is_trailing_urgently:
                    active_cfg.w_anti_feed *= 1.40
                    active_cfg.w_capping *= 1.40
                if ctx.own_remaining <= 2:
                    active_cfg.w_objective *= 1.40
            self.config = active_cfg

        player_data = engine.players.get(self.bot_id, {})
        hand: List[Dict[str, Any]] = copy.deepcopy(player_data.get("hand", []))
        own_discards: List[List[Dict[str, Any]]] = copy.deepcopy(
            player_data.get("discard_piles", [[], [], [], []])
        )

        # Resolve effective dialable cheat parameters
        effective_hand_vision = getattr(self.config, "cheat_hand_vision", 0.0)
        effective_obj_lookahead = getattr(self.config, "cheat_objective_lookahead", 0)
        effective_deck_lookahead = getattr(self.config, "cheat_deck_lookahead", 0)

        if getattr(self.config, "enable_hand_peeking", False):
            effective_hand_vision = max(effective_hand_vision, 1.0)

        if getattr(self.config, "cheat_rubberband", False):
            bot_rem = engine.get_objective_remaining(self.bot_id)
            opp_rems = [engine.get_objective_remaining(p) for p in engine.get_player_list() if p != self.bot_id]
            min_opp_rem = min(opp_rems, default=bot_rem)
            deficit = bot_rem - min_opp_rem  # Positive when bot is losing

            if deficit <= 0:
                # Ahead or tied: honest play
                effective_hand_vision = 0.0
                effective_obj_lookahead = 0
                effective_deck_lookahead = 0
            elif deficit == 1:
                effective_hand_vision = 0.35
                effective_obj_lookahead = 1
                effective_deck_lookahead = 0
            elif deficit == 2:
                effective_hand_vision = 0.70
                effective_obj_lookahead = 2
                effective_deck_lookahead = 2
            else:
                effective_hand_vision = 1.0
                effective_obj_lookahead = 3
                effective_deck_lookahead = 5
        elif getattr(self.config, "cheat_level", 0.0) > 0.0:
            c_lvl = float(self.config.cheat_level)
            effective_hand_vision = max(effective_hand_vision, c_lvl)
            effective_obj_lookahead = max(effective_obj_lookahead, int(round(c_lvl * 3)))
            effective_deck_lookahead = max(effective_deck_lookahead, int(round(c_lvl * 5)))

        # Enforce Fog-of-War: Only the top objective card is face-up and visible.
        # Face-down cards underneath are unknown UNLESS objective lookahead is dialed in.
        top_obj = engine.get_objective_top_card(self.bot_id)
        rem = engine.get_objective_remaining(self.bot_id)
        obj_deck: List[Dict[str, Any]] = []

        raw_obj_deck = player_data.get("objective_deck", [])
        for i in range(max(0, rem - 1)):
            steps_from_top = (rem - 1) - i
            card_idx = len(raw_obj_deck) - 1 - steps_from_top
            if steps_from_top <= effective_obj_lookahead and 0 <= card_idx < len(raw_obj_deck):
                obj_deck.append(copy.deepcopy(raw_obj_deck[card_idx]))
            else:
                obj_deck.append({"type": "unknown", "value": -1})
        if rem > 0 and top_obj:
            obj_deck.append(copy.deepcopy(top_obj))

        # Opponents' information (discard tops & objective top cards)
        opp_discards: List[Tuple[str, int, Dict[str, Any]]] = []
        opponents_info: List[Dict[str, Any]] = []

        deck_size = engine.get_main_deck_size() if hasattr(engine, "get_main_deck_size") else len(getattr(engine, "main_deck", []))

        # Draw deck peek (deterministic foresight on upcoming redraws)
        draw_deck_peek: List[Dict[str, Any]] = []
        if effective_deck_lookahead > 0 and hasattr(engine, "main_deck") and engine.main_deck:
            peek_count = min(len(engine.main_deck), effective_deck_lookahead)
            draw_deck_peek = [copy.deepcopy(c) for c in reversed(engine.main_deck[-peek_count:])]

        # Determine the table seat distance and turn order for each player
        player_order = engine.player_order if hasattr(engine, "player_order") else engine.get_player_list()
        num_players = len(player_order)
        bot_idx = player_order.index(self.bot_id) if self.bot_id in player_order else 0
        next_player_id = player_order[(bot_idx + 1) % num_players] if num_players > 1 else None

        for p_id in engine.get_player_list():
            if p_id == self.bot_id:
                continue
            rem = engine.get_objective_remaining(p_id)
            top_obj = engine.get_objective_top_card(p_id)
            val = int(top_obj.get("value", 0)) if top_obj.get("type") == "number" else 0
            p_discards = [copy.deepcopy(d_top) for d_top in engine.get_discard_tops(p_id)]
            is_next = (p_id == next_player_id)
            p_idx = player_order.index(p_id) if p_id in player_order else 0
            seat_distance = (p_idx - bot_idx) % num_players if num_players > 1 else 1

            opp_dict = {
                "id": p_id,
                "val": val,
                "rem": rem,
                "card": copy.deepcopy(top_obj) if top_obj else None,
                "discards": p_discards,
                "is_next": is_next,
                "seat_distance": seat_distance,
            }
            if effective_hand_vision > 0.0:
                opp_player_data = engine.players.get(p_id, {})
                full_hand = opp_player_data.get("hand", [])
                if effective_hand_vision >= 1.0:
                    opp_dict["hand"] = copy.deepcopy(full_hand)
                else:
                    known_k = max(1, int(round(len(full_hand) * effective_hand_vision)))
                    opp_dict["hand"] = copy.deepcopy(full_hand[:known_k])

                if is_next and effective_deck_lookahead > 0:
                    opp_dict["cards_needed"] = max(0, 5 - len(full_hand))
            opponents_info.append(opp_dict)

            for d_top in p_discards:
                s_idx = int(d_top.get("slot_index", 0))
                opp_discards.append((p_id, s_idx, copy.deepcopy(d_top)))

        # Mark table leader (fewest objective cards remaining)
        if opponents_info:
            min_rem = min(p["rem"] for p in opponents_info)
            for p in opponents_info:
                p["is_leader"] = (p["rem"] == min_rem and min_rem <= 6)

        opponents_info.sort(key=lambda x: x["rem"])  # Fewest remaining first

        # 2. Run Forward Search
        try:
            best_plan = self._search_best_plan(
                board=board,
                hand=hand,
                own_discards=own_discards,
                obj_deck=obj_deck,
                opp_discards=opp_discards,
                opponents_info=opponents_info,
                max_depth=self.config.max_depth,
                beam_width=self.config.beam_width,
                deck_size=deck_size,
                draw_deck_peek=draw_deck_peek,
            )
            return best_plan
        finally:
            self.config = original_config

    # -------------------------------------------------------------------------
    # Core Beam Search
    # -------------------------------------------------------------------------
    def _search_best_plan(
        self,
        board: List[SlotSim],
        hand: List[Dict[str, Any]],
        own_discards: List[List[Dict[str, Any]]],
        obj_deck: List[Dict[str, Any]],
        opp_discards: List[Tuple[str, int, Dict[str, Any]]],
        opponents_info: List[Dict[str, Any]],
        max_depth: int = 12,
        beam_width: int = 18,
        deck_size: int = 999,
        draw_deck_peek: Optional[List[Dict[str, Any]]] = None,
    ) -> TurnPlan:
        # Evaluate no-play baseline
        base_moves, base_discard_score = self._select_flexible_discards(
            hand=hand,
            discard_piles=own_discards,
            obj_deck=obj_deck,
            board=board,
            opponents_info=opponents_info,
            deck_size=deck_size,
        )
        base_discard_idx = base_moves[0][0] if base_moves else 0
        base_discard_slot = base_moves[0][1] if base_moves else 0
        base_terminal_score = self._evaluate_terminal_state(
            board=board,
            hand=hand,
            objs_cleared=0,
            refills=0,
            discard_score=base_discard_score,
            opponents_info=opponents_info,
            obj_deck=obj_deck,
            own_discards=own_discards,
            draw_deck_peek=draw_deck_peek,
        )
        best_plan = TurnPlan(
            actions=[],
            discard_card_idx=base_discard_idx,
            discard_slot_idx=base_discard_slot,
            score=base_terminal_score,
            objectives_scored=0,
            refills_triggered=0,
            discard_moves=base_moves,
        )
        has_mandatory_perk = any(
            s.pending_perk and (s.perk_owner is None or s.perk_owner == self.bot_id)
            for s in board
        )
        best_overall_score = -1e9 if has_mandatory_perk else base_terminal_score

        # Node in beam: (node_score, cum_step_bonus, actions_taken, board, hand, own_discards, obj_deck, opp_discards, opp_info, objs_cleared, refills)
        initial_node = (
            base_terminal_score,
            0.0,
            [],
            board,
            hand,
            own_discards,
            obj_deck,
            opp_discards,
            opponents_info,
            0,
            0,
        )

        beam = [initial_node]

        for depth in range(max_depth):
            next_candidates = []

            for (
                node_score,
                cum_step_bonus,
                actions,
                c_board,
                c_hand,
                c_discards,
                c_obj_deck,
                c_opp_discards,
                c_opp_info,
                objs_cleared,
                refills,
            ) in beam:
                legal_plays = self._generate_legal_plays(
                    board=c_board,
                    hand=c_hand,
                    own_discards=c_discards,
                    obj_deck=c_obj_deck,
                    opp_discards=c_opp_discards,
                    opponents_info=c_opp_info,
                )

                if not legal_plays:
                    d_moves, d_score = self._select_flexible_discards(
                        hand=c_hand,
                        discard_piles=c_discards,
                        obj_deck=c_obj_deck,
                        board=c_board,
                        opponents_info=c_opp_info,
                        deck_size=deck_size,
                    )
                    d_idx = d_moves[0][0] if d_moves else 0
                    d_slot = d_moves[0][1] if d_moves else 0
                    terminal_score = self._evaluate_terminal_state(
                        board=c_board,
                        hand=c_hand,
                        objs_cleared=objs_cleared,
                        refills=refills,
                        discard_score=d_score,
                        opponents_info=c_opp_info,
                        obj_deck=c_obj_deck,
                        own_discards=c_discards,
                        draw_deck_peek=draw_deck_peek,
                    ) + cum_step_bonus
                    if terminal_score > best_overall_score:
                        best_overall_score = terminal_score
                        best_plan = TurnPlan(
                            actions=list(actions),
                            discard_card_idx=d_idx,
                            discard_slot_idx=d_slot,
                            score=terminal_score,
                            objectives_scored=objs_cleared,
                            refills_triggered=refills,
                            discard_moves=d_moves,
                        )
                    continue

                for play in legal_plays:
                    (
                        new_board,
                        new_hand,
                        new_discards,
                        new_obj_deck,
                        new_opp_discards,
                        new_opp_info,
                        new_objs,
                        new_refills,
                        step_bonus,
                    ) = self._apply_play(
                        play=play,
                        board=c_board,
                        hand=c_hand,
                        own_discards=c_discards,
                        obj_deck=c_obj_deck,
                        opp_discards=c_opp_discards,
                        opponents_info=c_opp_info,
                        draw_deck_peek=draw_deck_peek,
                    )

                    new_actions = actions + [play]
                    total_objs = objs_cleared + new_objs
                    total_refills = refills + new_refills
                    new_cum_step_bonus = cum_step_bonus + step_bonus

                    d_moves, d_score = self._select_flexible_discards(
                        hand=new_hand,
                        discard_piles=new_discards,
                        obj_deck=new_obj_deck,
                        board=new_board,
                        opponents_info=new_opp_info,
                        deck_size=deck_size,
                    )
                    d_idx = d_moves[0][0] if d_moves else 0
                    d_slot = d_moves[0][1] if d_moves else 0
                    curr_terminal_score = self._evaluate_terminal_state(
                        board=new_board,
                        hand=new_hand,
                        objs_cleared=total_objs,
                        refills=total_refills,
                        discard_score=d_score,
                        opponents_info=new_opp_info,
                        obj_deck=new_obj_deck,
                        own_discards=new_discards,
                        draw_deck_peek=draw_deck_peek,
                    ) + new_cum_step_bonus

                    if curr_terminal_score > best_overall_score:
                        best_overall_score = curr_terminal_score
                        best_plan = TurnPlan(
                            actions=list(new_actions),
                            discard_card_idx=d_idx,
                            discard_slot_idx=d_slot,
                            score=curr_terminal_score,
                            objectives_scored=total_objs,
                            refills_triggered=total_refills,
                            discard_moves=d_moves,
                        )

                    next_candidates.append((
                        curr_terminal_score,
                        new_cum_step_bonus,
                        new_actions,
                        new_board,
                        new_hand,
                        new_discards,
                        new_obj_deck,
                        new_opp_discards,
                        new_opp_info,
                        total_objs,
                        total_refills,
                    ))

            if not next_candidates:
                break

            next_candidates.sort(key=lambda x: x[0], reverse=True)
            beam = next_candidates[:beam_width]

        return best_plan

    # -------------------------------------------------------------------------
    # Legal Move Generation & Transition
    # -------------------------------------------------------------------------
    def _generate_legal_plays(
        self,
        board: List[SlotSim],
        hand: List[Dict[str, Any]],
        own_discards: List[List[Dict[str, Any]]],
        obj_deck: List[Dict[str, Any]],
        opp_discards: List[Tuple[str, int, Dict[str, Any]]],
        opponents_info: List[Dict[str, Any]],
    ) -> List[PlanAction]:
        actions: List[PlanAction] = []

        # 1. Perks (strictly restricted to perks earned by this bot, not opponents)
        # Rule: When prompted to ladder perk, that is the ONLY time you can either take it or leave it!
        # No other moves (hand, objective, discard) are allowed while a perk is pending.
        pending_perk_slots = [
            slot_idx
            for slot_idx, slot in enumerate(board)
            if slot.pending_perk and (slot.perk_owner is None or slot.perk_owner == self.bot_id)
        ]
        if pending_perk_slots:
            for s_idx in pending_perk_slots:
                actions.append(
                    PlanAction(
                        action_type="perk",
                        slot_idx=s_idx,
                        description=f"Initiate perk on slot {s_idx}",
                    )
                )
                actions.append(
                    PlanAction(
                        action_type="decline_perk",
                        slot_idx=s_idx,
                        description=f"Decline perk on slot {s_idx}",
                    )
                )
            return actions

        # 2. Objective Top Card
        if obj_deck:
            top_obj = obj_deck[-1]
            for slot_idx, slot in enumerate(board):
                if not slot.pending_perk and self._can_play_card(slot, top_obj):
                    actions.append(
                        PlanAction(
                            action_type="objective",
                            slot_idx=slot_idx,
                            card=top_obj,
                            description=f"Play Objective {top_obj} onto slot {slot_idx}",
                        )
                    )

        # 3. Hand Cards
        for h_idx, card in enumerate(hand):
            for slot_idx, slot in enumerate(board):
                if not slot.pending_perk and self._can_play_card(slot, card):
                    actions.append(
                        PlanAction(
                            action_type="hand",
                            slot_idx=slot_idx,
                            ref=h_idx,
                            card=card,
                            description=f"Play Hand[{h_idx}] {card} onto slot {slot_idx}",
                        )
                    )

        # 4. Own Discard Tops
        for s_idx, pile in enumerate(own_discards):
            if pile:
                top_card = pile[-1]
                for slot_idx, slot in enumerate(board):
                    if not slot.pending_perk and self._can_play_card(slot, top_card):
                        actions.append(
                            PlanAction(
                                action_type="discard",
                                slot_idx=slot_idx,
                                ref=(self.bot_id, s_idx),
                                card=top_card,
                                description=f"Play own discard slot {s_idx} {top_card} onto slot {slot_idx}",
                            )
                        )

        # 5. Opponent Discard Tops (Denial & Bridging)
        for opp_id, s_idx, top_card in opp_discards:
            for slot_idx, slot in enumerate(board):
                if not slot.pending_perk and self._can_play_card(slot, top_card):
                    actions.append(
                        PlanAction(
                            action_type="discard",
                            slot_idx=slot_idx,
                            ref=(opp_id, s_idx),
                            card=top_card,
                            description=f"Play {opp_id}'s discard slot {s_idx} {top_card} onto slot {slot_idx}",
                        )
                    )

        # 6. Tactical Opponent Objective Cards (Stepping Stone & Perk Snatch)
        if self.config.enable_opp_objective_play:
            for opp in opponents_info:
                # VICTORY FIREWALL: Never touch opponent's card if they have <= 1 card remaining!
                if opp["rem"] > 1 and opp.get("card"):
                    opp_card = opp["card"]
                    opp_id = opp["id"]
                    for slot_idx, slot in enumerate(board):
                        if not slot.pending_perk and self._can_play_card(slot, opp_card):
                            # STRICT CRITERIA: allow opponent objective play if:
                            # 1. Bot is down to 1 objective card (endgame winning burst).
                            # 2. Board unlock mid-game: bot has >= 3 cards left, opponent has >= 4 cards left,
                            #    and opponent's card is near bot's own objective (within 3 steps).
                            is_bot_winning_burst = (len(obj_deck) == 1)
                            is_board_unlock = False
                            if len(obj_deck) >= 3 and opp["rem"] >= 4 and obj_deck:
                                top_b_obj = obj_deck[-1]
                                if top_b_obj.get("type") == "number" and opp_card.get("type") == "number":
                                    b_target = int(top_b_obj.get("value", 0))
                                    o_val = int(opp_card.get("value", 0))
                                    if o_val > 0 and abs(o_val - b_target) <= 3:
                                        is_board_unlock = True

                            if is_bot_winning_burst or is_board_unlock:
                                reason = "Winning Burst" if is_bot_winning_burst else "Board Unlock"
                                actions.append(
                                    PlanAction(
                                        action_type="opponent_objective",
                                        slot_idx=slot_idx,
                                        ref=opp_id,
                                        card=opp_card,
                                        description=f"{reason}: Play {opp_id}'s Objective {opp_card} onto slot {slot_idx}",
                                    )
                                )

        # Deduplicate identical moves to prevent duplicate cards (e.g. multiple Jokers or 4s)
        # from flooding the beam search and squeezing out distinct multi-step combo paths
        seen_sigs = set()
        deduped_actions: List[PlanAction] = []
        for a in actions:
            card_key = (a.card.get("type"), a.card.get("value")) if a.card else None
            if a.action_type == "hand":
                sig = (a.action_type, a.slot_idx, card_key)
            elif a.action_type == "discard":
                sig = (a.action_type, a.slot_idx, a.ref[0], a.ref[1], card_key)
            else:
                sig = (a.action_type, a.slot_idx, str(a.ref), card_key)

            if sig not in seen_sigs:
                seen_sigs.add(sig)
                deduped_actions.append(a)

        return deduped_actions

    def _can_play_card(self, slot: SlotSim, card: Dict[str, Any]) -> bool:

        if not card or card.get("type") == "unknown":
            return False
        c_type = card.get("type", "number")
        c_val = int(card.get("value", 0))

        if slot.pending_perk:
            return False
        if slot.value == 0 and not slot.is_omni_base:
            return (c_type == "number" and c_val == 1) or c_type == "joker"
        elif slot.is_omni_base:
            return True
        else:
            return (c_type == "number" and c_val == slot.value + 1) or c_type == "joker"

    def _apply_play(
        self,
        play: PlanAction,
        board: List[SlotSim],
        hand: List[Dict[str, Any]],
        own_discards: List[List[Dict[str, Any]]],
        obj_deck: List[Dict[str, Any]],
        opp_discards: List[Tuple[str, int, Dict[str, Any]]],
        opponents_info: List[Dict[str, Any]],
        draw_deck_peek: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[
        List[SlotSim],
        List[Dict[str, Any]],
        List[List[Dict[str, Any]]],
        List[Dict[str, Any]],
        List[Tuple[str, int, Dict[str, Any]]],
        List[Dict[str, Any]],
        int,
        int,
        float,
    ]:
        new_board = [
            SlotSim(s.value, s.is_omni_base, s.pending_perk, s.cards_count, s.perk_owner)
            for s in board
        ]
        new_hand = list(hand)
        new_own_discards = [list(pile) for pile in own_discards]
        new_obj_deck = list(obj_deck)
        new_opp_discards = list(opp_discards)
        new_opp_info = [dict(o) for o in opponents_info]

        slot = new_board[play.slot_idx]
        slot_before_val = slot.value
        objs_cleared = 0
        refills = 0
        step_bonus = 0.0

        obj_val = 0
        if new_obj_deck:
            top_o = new_obj_deck[-1]
            if top_o.get("type") == "number":
                obj_val = int(top_o.get("value", 0))

        has_other_empty = any(
            i != play.slot_idx and s.value == 0 and not s.pending_perk
            for i, s in enumerate(new_board)
        )

        if play.action_type == "perk":
            slot.pending_perk = False
            slot.perk_owner = None
            step_bonus += self.config.w_ladder_perk

            # Simulate the card drawn onto this slot from the perk
            drawn_card = None
            if draw_deck_peek:
                drawn_card = draw_deck_peek[0]

            if drawn_card and drawn_card.get("type") == "joker":
                slot.is_omni_base = True
                slot.value = 1
                step_bonus += 350.0  # Massive omni-base perk value
            else:
                drawn_val = int(drawn_card.get("value", 7)) if drawn_card and drawn_card.get("type") == "number" else 7
                slot.value = drawn_val
                slot.is_omni_base = False

            slot.cards_count = 1

            # Tactical Perk Rule:
            if has_other_empty:
                # We already have an empty foundation elsewhere: taking perk is strictly superior!
                step_bonus += 250.0
            elif obj_val >= 7:
                # High objective: perk jump helps us reach mid/high number faster
                step_bonus += 200.0
            elif 1 <= obj_val <= 4:
                # Low objective and no other 0 foundation exists: slight penalty since 0 is lost
                step_bonus -= 100.0
            else:
                step_bonus += 30.0

        elif play.action_type == "decline_perk":
            slot.pending_perk = False
            slot.perk_owner = None
            slot.value = 0
            slot.is_omni_base = False
            slot.cards_count = 0

            # Tactical Perk Rule:
            if has_other_empty:
                # Wasting a free perk card when another 0 slot already exists is a severe blunder!
                step_bonus -= 450.0
            else:
                step_bonus += self.config.w_ladder_perk
                if 1 <= obj_val <= 4:
                    step_bonus += 250.0
                elif obj_val >= 7:
                    step_bonus -= 180.0
                else:
                    step_bonus += 10.0

        elif play.action_type == "objective":
            if new_obj_deck:
                new_obj_deck.pop()
                objs_cleared = 1
                step_bonus += self.config.w_objective
            self._update_slot_with_card(slot, play.card)

        elif play.action_type == "hand":
            h_idx = int(play.ref)
            if 0 <= h_idx < len(new_hand):
                new_hand.pop(h_idx)

            was_omni = slot.is_omni_base
            self._update_slot_with_card(slot, play.card)

            # Purposeful Hand Momentum: reward plays that bridge towards objective, complete ladders, or empty hand
            is_purposeful = (
                slot.pending_perk
                or len(new_hand) == 0
                or (obj_val > 0 and 0 < slot.value <= obj_val)
            )
            step_bonus += 35.0 if is_purposeful else 10.0

            # SENSOR 1: Omni-Base Slot Preservation (avoid burning flexible slots on high numbers)
            if was_omni:
                c_val = int(play.card.get("value", 0)) if play.card and play.card.get("type") == "number" else 1
                if c_val >= 8 and not slot.pending_perk and objs_cleared == 0:
                    step_bonus -= self.config.w_omni_preservation

            # SENSOR 2: Joker Conservation (enforce minimum 180.0 floor to stop wasting trumps on turn 1)
            if play.card and play.card.get("type") == "joker":
                if not (slot.pending_perk or objs_cleared > 0 or len(new_hand) == 0):
                    # When actively building a high ladder (>= 7) or bridging towards our objective,
                    # soften the immediate step penalty so beam search can discover multi-step combos (e.g. Joker -> 11 -> 12).
                    if slot.value >= 7 or (obj_val > 0 and 0 < slot.value <= obj_val):
                        step_bonus -= max(180.0, self.config.w_joker_conservation) * 0.25
                    else:
                        step_bonus -= max(180.0, self.config.w_joker_conservation)


            if len(new_hand) == 0:
                refills = 1
                step_bonus += self.config.w_hand_flush

        elif play.action_type == "discard":
            owner_id, s_idx = play.ref
            if owner_id == self.bot_id:
                if 0 <= s_idx < len(new_own_discards) and new_own_discards[s_idx]:
                    new_own_discards[s_idx].pop()
            else:
                new_opp_discards = [
                    (o, s, c)
                    for (o, s, c) in new_opp_discards
                    if not (o == owner_id and s == s_idx)
                ]
                step_bonus += self.config.w_discard_steal  # Resource denial bonus

            self._update_slot_with_card(slot, play.card)
            step_bonus += 20.0  # Tempo bonus for unlocking buried discards

        elif play.action_type == "opponent_objective":
            opp_id = str(play.ref)
            for o in new_opp_info:
                if o["id"] == opp_id:
                    o["rem"] = max(0, o["rem"] - 1)
                    o["card"] = None  # Consumed for this simulation turn
                    opp_rem = o["rem"]
                    if opp_rem == 0:
                        # Immediate suicide: emptying opponent's objective deck grants them the victory!
                        step_bonus -= 1000000.0
                    else:
                        urgency = (16.0 - opp_rem) / 15.0
                        # Baseline sacrifice cost: higher if opponent has fewer cards left
                        step_bonus -= (self.config.w_opp_obj_sacrifice + 250.0 * (urgency ** 2))
                    break

            self._update_slot_with_card(slot, play.card)

            # Extra reward if playing opponent's card completes a ladder to 12 (snatching the perk!)
            if slot.pending_perk:
                step_bonus += 350.0
            elif len(new_obj_deck) > 0:
                # Severe anti-feed veto: helping opponent without completing a ladder or winning is banned!
                step_bonus -= 5000.0

        # SENSOR 6: Active Ramp-Kill bonus (rewards proactively capping an opponent's ramp)
        if slot_before_val > 0 and slot.value != slot_before_val:
            for opp in new_opp_info:
                opp_t = int(opp.get("val", 0))
                opp_rem = int(opp.get("rem", 10))
                if opp_t > 0 and slot_before_val == opp_t - 1:
                    urgency = (16.0 - max(1, opp_rem)) / 15.0
                    step_bonus += self.config.w_anti_feed * 1.2 * (urgency ** 1.5)

        return (
            new_board,
            new_hand,
            new_own_discards,
            new_obj_deck,
            new_opp_discards,
            new_opp_info,
            objs_cleared,
            refills,
            step_bonus,
        )

    def _update_slot_with_card(self, slot: SlotSim, card: Optional[Dict[str, Any]]) -> None:
        if not card:
            return
        c_type = card.get("type", "number")
        c_val = int(card.get("value", 0))

        if slot.value == 0 and not slot.is_omni_base:
            slot.value = 1
        elif slot.is_omni_base:
            slot.value = c_val if c_type == "number" else 1
            slot.is_omni_base = False
        else:
            if c_type == "number":
                slot.value = c_val
            else:
                slot.value += 1

        slot.cards_count += 1

        # Check ladder completion
        if slot.value >= 12:
            slot.value = 0
            slot.is_omni_base = False
            slot.cards_count = 0
            slot.pending_perk = True
            slot.perk_owner = self.bot_id

    # -------------------------------------------------------------------------
    # Evaluation Functions
    # -------------------------------------------------------------------------
    def _evaluate_terminal_state(
        self,
        board: List[SlotSim],
        hand: List[Dict[str, Any]],
        objs_cleared: int,
        refills: int,
        discard_score: float,
        opponents_info: List[Dict[str, Any]],
        obj_deck: List[Dict[str, Any]],
        own_discards: Optional[List[List[Dict[str, Any]]]] = None,
        draw_deck_peek: Optional[List[Dict[str, Any]]] = None,
    ) -> float:
        score = 0.0

        # 1. Objective Progress
        score += objs_cleared * self.config.w_objective
        if objs_cleared > 0 and len(obj_deck) == 0:
            return 100000.0  # Instant victory!

        # 2. Hand Refills (gated by board warmth and draw deck foresight)
        if refills > 0:
            target_val = int(obj_deck[-1].get("value", 0)) if (obj_deck and obj_deck[-1].get("type") == "number") else 0
            board_warm = any(0 < s.value < target_val for s in board if not s.pending_perk) if target_val > 0 else any(s.value > 0 for s in board if not s.pending_perk)
            flush_reward = self.config.w_hand_flush if board_warm else (self.config.w_hand_flush * 0.4)

            # Draw deck foresight bonus: evaluate if upcoming cards can be absorbed immediately
            if draw_deck_peek:
                board_vals = [s.value for s in board if not s.pending_perk]
                playable_draws = sum(
                    1 for c in draw_deck_peek
                    if c.get("type") == "joker" or
                    (c.get("type") == "number" and any((b == 0 and int(c.get("value", 0)) == 1) or int(c.get("value", 0)) == b + 1 for b in board_vals))
                )
                flush_reward += playable_draws * 80.0

            score += refills * flush_reward

        # 3. Ladder Completion / Pending Perks
        for slot in board:
            if slot.pending_perk:
                score += self.config.w_ladder_perk
            elif slot.value > 0:
                score += slot.value * 2.0

        # SENSOR 7: Uncompleted High-Ladder Danger Zone / Anti-Perk-Feeding Penalty
        # Any slot left at 10 or 11 without being finished to 12 gifts the ladder perk to opponents!
        for slot in board:
            if not slot.pending_perk and slot.value in (10, 11):
                # Is this slot actively matching/clearing our current objective?
                is_obj_match = (
                    obj_deck
                    and obj_deck[-1].get("type") == "number"
                    and int(obj_deck[-1].get("value", 0)) == slot.value
                )
                is_hand_flush = (len(hand) == 0)
                if not is_obj_match and not is_hand_flush:
                    for opp in opponents_info:
                        opp_rem = int(opp.get("rem", 10))
                        urgency = (16.0 - max(1, opp_rem)) / 15.0
                        opp_hand = opp.get("hand")
                        effective_hand = list(opp_hand) if opp_hand is not None else None
                        incoming_cards: List[Dict[str, Any]] = []
                        if effective_hand is not None and opp.get("is_next") and draw_deck_peek:
                            cards_needed = opp.get("cards_needed", 0)
                            if cards_needed > 0:
                                deck_offset = refills * 5
                                available_peek = draw_deck_peek[deck_offset :]
                                incoming_cards = available_peek[:cards_needed]
                                effective_hand.extend(incoming_cards)

                        opp_has_finisher = False
                        if effective_hand is not None:
                            # Cheating / peeking hand vision: check if opponent holds finisher
                            num_jokers = sum(1 for c in effective_hand if c.get("type") == "joker")
                            has_11 = any(c.get("type") == "number" and int(c.get("value", 0)) == 11 for c in effective_hand)
                            has_12 = any(c.get("type") == "number" and int(c.get("value", 0)) == 12 for c in effective_hand)

                            if opp.get("discards"):
                                for d_top in opp["discards"]:
                                    if d_top.get("type") == "joker":
                                        num_jokers += 1
                                    elif d_top.get("type") == "number":
                                        if int(d_top.get("value", 0)) == 11:
                                            has_11 = True
                                        elif int(d_top.get("value", 0)) == 12:
                                            has_12 = True

                            # To finish from 10 to 12 in one turn, opponent needs:
                            # 2 Jokers, OR 1 Joker + (11 or 12), OR both 11 and 12!
                            if slot.value == 10:
                                opp_has_finisher = (num_jokers >= 2) or (num_jokers >= 1 and (has_11 or has_12)) or (has_11 and has_12)
                            elif slot.value == 11:
                                opp_has_finisher = (num_jokers >= 1) or has_12

                            if not opp_has_finisher:
                                unpeeked_draws = max(0, opp.get("cards_needed", 0) - len(incoming_cards)) if opp.get("is_next") else 0
                                if unpeeked_draws > 0:
                                    if slot.value == 10:
                                        has_one_piece = (num_jokers >= 1) or has_11 or has_12
                                        if has_one_piece or unpeeked_draws >= 2:
                                            opp_has_finisher = True
                                            urgency *= min(1.0, 0.15 * unpeeked_draws)
                                    else:
                                        opp_has_finisher = True
                                        urgency *= min(1.0, 0.25 * unpeeked_draws)
                        else:
                            # Standard fog-of-war risk estimate
                            opp_has_finisher = True
                            urgency *= 0.7

                        if opp_has_finisher:
                            score -= (self.config.w_ladder_perk * 1.2 + 80.0) * urgency


        # 3.5 Hand Emptying Tempo
        score += (5 - len(hand)) * 8.0

        # 4. Proximity to Current Objective & Board Velocity
        if obj_deck:
            top_obj = obj_deck[-1]
            if top_obj.get("type") == "number":
                target_val = int(top_obj.get("value", 0))
                has_warm_slot = False
                for slot in board:
                    if 0 < slot.value < target_val:
                        steps_away = target_val - slot.value
                        if steps_away <= 3:
                            score += (4 - steps_away) * 30.0 + 46.0  # Monotonic tier 1: 136, 106, 76
                        elif steps_away <= 6:
                            score += (7 - steps_away) * 12.0 + 10.0  # Monotonic tier 2: 46, 34, 22
                        elif steps_away <= 8:
                            score += (9 - steps_away) * 5.0          # Monotonic tier 3: 10, 5
                        if steps_away <= 4:
                            has_warm_slot = True

                # Cold-start penalty when target is high and board has no warm bridging slot
                if target_val >= 7 and not has_warm_slot:
                    score -= 80.0

        # 5. Dynamic Threat Mitigation (Opponent Anti-Feeding & Capping)
        threat_penalty = 0.0
        for opp in opponents_info:
            opp_val = int(opp.get("val", 0))
            rem_cards = int(opp.get("rem", 10))
            opp_hand = opp.get("hand")
            if opp_val <= 0:
                continue

            effective_hand = list(opp_hand) if opp_hand is not None else None
            incoming_cards: List[Dict[str, Any]] = []
            if effective_hand is not None and opp.get("is_next") and draw_deck_peek:
                cards_needed = opp.get("cards_needed", 0)
                if cards_needed > 0:
                    deck_offset = refills * 5
                    available_peek = draw_deck_peek[deck_offset :]
                    incoming_cards = available_peek[:cards_needed]
                    effective_hand.extend(incoming_cards)

            seat_dist = opp.get("seat_distance", 1)
            is_leader = opp.get("is_leader", False)

            for slot in board:
                if slot.value == opp_val - 1 and slot.value > 0:
                    urgency = (16.0 - max(1, rem_cards)) / 15.0

                    # Table Order & Trust Factor for 3-4 Players:
                    # If this opponent acts later in the turn order (seat_distance >= 2):
                    # Check if any intervening player (acting before this opponent) can cap or play on this slot!
                    if seat_dist >= 2:
                        intervening = [p for p in opponents_info if p.get("seat_distance", 1) < seat_dist]
                        can_intermediate_cap = False
                        for inter_p in intervening:
                            inter_hand = inter_p.get("hand")
                            if inter_hand is not None:
                                if any(
                                    c.get("type") == "joker"
                                    or (c.get("type") == "number" and int(c.get("value", 0)) == opp_val)
                                    for c in inter_hand
                                ):
                                    can_intermediate_cap = True
                                    break
                            if not can_intermediate_cap and inter_p.get("discards"):
                                for d_top in inter_p["discards"]:
                                    if d_top.get("type") == "joker" or (
                                        d_top.get("type") == "number" and int(d_top.get("value", 0)) == opp_val
                                    ):
                                        can_intermediate_cap = True
                                        break

                        if can_intermediate_cap:
                            # Intervening player is confirmed to hold the capping card:
                            # Apply 80% Trust Discount unless the leader is at match point (rem <= 2)!
                            if rem_cards > 2:
                                urgency *= 0.20
                        else:
                            # Intervening player cannot cap: slight positional decay
                            urgency *= 0.85

                    if is_leader:
                        # Prioritize defense against the table leader
                        urgency *= 1.35

                    # Cheating Hand Vision: if opponent hand is visible, verify if threat is real!
                    if effective_hand is not None:
                        has_card = any(
                            c.get("type") == "joker" or (c.get("type") == "number" and int(c.get("value", 0)) == opp_val)
                            for c in effective_hand
                        )
                        # Also check accessible discard pile tops (opponent's or bot's own piles)
                        if not has_card and opp.get("discards"):
                            for d_top in opp["discards"]:
                                if d_top.get("type") == "joker" or (d_top.get("type") == "number" and int(d_top.get("value", 0)) == opp_val):
                                    has_card = True
                                    break
                        if not has_card and own_discards:
                            for pile in own_discards:
                                if pile and (pile[-1].get("type") == "joker" or (pile[-1].get("type") == "number" and int(pile[-1].get("value", 0)) == opp_val)):
                                    has_card = True
                                    break

                        if not has_card:
                            unpeeked_draws = max(0, opp.get("cards_needed", 0) - len(incoming_cards)) if opp.get("is_next") else 0
                            if unpeeked_draws > 0:
                                # Opponent draws unrevealed cards from deck: discount urgency proportionally
                                urgency *= min(1.0, 0.25 * unpeeked_draws)
                            else:
                                # 100% of opponent's hand and incoming draws are known and do NOT contain the card: safe!
                                continue
                        else:
                            # Confirmed threat: apply calibrated urgency multiplier
                            urgency *= 1.25

                    threat_penalty += self.config.w_anti_feed * (urgency ** 2)

                    # SENSOR 3: Extended Endgame Threat Veto (Active when opponent has <= 4 cards left)
                    if rem_cards <= 4:
                        mults = {1: 3.0, 2: 2.0, 3: 1.0, 4: 0.5}
                        threat_penalty += self.config.w_endgame_threat_veto * mults.get(rem_cards, 0.5)

                elif slot.value == opp_val and slot.value > 0:
                    urgency = (16.0 - max(1, rem_cards)) / 15.0
                    if is_leader:
                        urgency *= 1.4
                    score += self.config.w_capping * urgency

        score -= threat_penalty

        # SENSOR 4: Discard Unlock Priming (Board primed to unlock own discard piles next turn)
        if own_discards:
            for pile in own_discards:
                if pile:
                    d_top = pile[-1]
                    d_val = int(d_top.get("value", 0)) if d_top.get("type") == "number" else 0
                    if d_val > 1:
                        for slot in board:
                            if slot.value == d_val - 1 and slot.value > 0:
                                score += self.config.w_discard_unlock
                                break

        # SENSOR 5: Hand Diversity / Parity Spread (Maintains fluid hands for next turn)
        if hand:
            unique_vals = set()
            for c in hand:
                if c.get("type") == "joker":
                    unique_vals.add("J")
                else:
                    unique_vals.add(c.get("value"))
            diversity_ratio = len(unique_vals) / len(hand)
            score += diversity_ratio * self.config.w_hand_diversity

        # 6. Discard Selection Quality (calibrated so discard polish supports rather than drowns out card play)
        score += discard_score * 0.15

        # 7. Imperfection jitter for casual difficulty
        if self.config.random_noise > 0.0:
            score += random.uniform(-self.config.random_noise, self.config.random_noise)

        return score

    # -------------------------------------------------------------------------
    # Solitaire-Style Discard Selection with Anti-Feed & Exposure Sensors
    # -------------------------------------------------------------------------
    def _select_best_discard(
        self,
        hand: List[Dict[str, Any]],
        discard_piles: List[List[Dict[str, Any]]],
        obj_deck: List[Dict[str, Any]],
        board: Optional[List[SlotSim]] = None,
        opponents_info: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[int, int, float]:
        if not hand:
            return (0, 0, 0.0)

        obj_val = 0
        if obj_deck:
            top = obj_deck[-1]
            if top.get("type") == "number":
                obj_val = int(top.get("value", 0))

        opp_obj_vals = set()
        if opponents_info:
            for opp in opponents_info:
                if opp.get("card") and opp["card"].get("type") == "number":
                    opp_obj_vals.add(int(opp["card"].get("value", 0)))

        board_vals = []
        if board:
            for slot in board:
                if not slot.pending_perk:
                    board_vals.append(slot.value)

        best_card_idx = 0
        best_card_penalty = 1e9

        for idx, card in enumerate(hand):
            c_type = card.get("type", "number")
            c_val = int(card.get("value", 0))
            penalty = 0.0

            if c_type == "joker":
                penalty += 10000.0
            elif c_val == obj_val:
                penalty += 600.0
            elif c_val == obj_val - 1:
                penalty += 350.0
            elif c_val in opp_obj_vals:
                # SENSOR: Critical Anti-Feed - Never discard opponent's exact objective!
                # Cooperative Assist Discard in 3-4 Player Tables:
                # If discarding c_val allows the next player (d=1) to steal it and cap a downstream leader's ramp (d>=2):
                is_assist_discard = False
                if opponents_info and len(opponents_info) > 1 and any(b_val == c_val - 1 for b_val in board_vals):
                    next_p = next((p for p in opponents_info if p.get("seat_distance") == 1 or p.get("is_next")), None)
                    downstream_threats = [p for p in opponents_info if p.get("seat_distance", 1) >= 2 and p.get("val") == c_val]
                    if next_p and downstream_threats:
                        next_p_has_card = False
                        if next_p.get("hand") is not None:
                            next_p_has_card = any(
                                c.get("type") == "joker" or (c.get("type") == "number" and int(c.get("value", 0)) == c_val)
                                for c in next_p["hand"]
                            )
                        if not next_p_has_card:
                            is_assist_discard = True

                if is_assist_discard:
                    # ASSIST DISCARD BONUS: Feed intermediate player to cap the leader!
                    penalty -= 300.0
                else:
                    penalty += 900.0
            elif any(c_val == opp_v - 1 for opp_v in opp_obj_vals if opp_v > 1):
                # SENSOR: Anti-Setup - Discarding the stepping stone directly into opponent's objective!
                penalty += 400.0
            elif c_val == 1:
                # SENSOR: Cornerstone 1 - Universal ladder starter on empty 0 foundations
                penalty += 350.0
            elif any(c_val == b_val + 1 for b_val in board_vals):
                # SENSOR: Anti-Theft - Discarding a card immediately playable from board slot
                # Check for Assist Discard: can the intermediate player use it to advance or cap a ladder?
                is_assist_theft = False
                if opponents_info and len(opponents_info) > 1:
                    next_p = next((p for p in opponents_info if p.get("seat_distance") == 1 or p.get("is_next")), None)
                    downstream_threats = [p for p in opponents_info if p.get("seat_distance", 1) >= 2]
                    if next_p and downstream_threats:
                        next_p_has_card = False
                        if next_p.get("hand") is not None:
                            next_p_has_card = any(
                                c.get("type") == "joker" or (c.get("type") == "number" and int(c.get("value", 0)) == c_val)
                                for c in next_p["hand"]
                            )
                        # Caps or denies a downstream threat target
                        if not next_p_has_card and any(t.get("val") == c_val for t in downstream_threats):
                            is_assist_theft = True

                if is_assist_theft:
                    penalty -= 500.0
                else:
                    penalty += 450.0
            elif any(c_val == b_val + 2 for b_val in board_vals):
                # SENSOR: 1-card bridge threat to opponent
                penalty += 120.0
            elif c_val in (11, 12) and not any(b_val >= 9 for b_val in board_vals):
                # SENSOR: Shield Card - Discarding unplayable high cards when board is low is extremely safe
                penalty -= 80.0
            else:
                penalty += (13 - c_val) * 12.0

            if penalty < best_card_penalty:
                best_card_penalty = penalty
                best_card_idx = idx

        card_to_discard = hand[best_card_idx]
        c_val = int(card_to_discard.get("value", 0))
        is_joker = (card_to_discard.get("type") == "joker")

        best_slot = 0
        best_slot_score = -1e9

        has_empty_slot = any(len(p) == 0 for p in discard_piles)

        for s_idx in range(len(discard_piles)):
            pile = discard_piles[s_idx]
            slot_score = 0.0

            if not pile:
                # Strong priority: open empty discard columns so all 4 slots are accessible
                slot_score = 120.0 + (c_val * 2.0 if not is_joker else 0.0)
            else:
                top_card = pile[-1]
                top_val = 13 if top_card.get("type") == "joker" else int(top_card.get("value", 0))

                if is_joker:
                    slot_score = -100.0
                elif top_val >= c_val:
                    diff = top_val - c_val
                    if diff == 1:
                        # Perfect consecutive descending solitaire stack (e.g. 9 on 10)
                        slot_score = 150.0
                    elif diff == 0:
                        # Duplicate rank (e.g. 10 on 10)
                        slot_score = 100.0
                    elif has_empty_slot:
                        # Severely penalize burying lower cards (e.g. 3 on 12) when empty columns exist!
                        slot_score = -50.0 - diff * 8.0
                    else:
                        slot_score = 75.0 - diff * 4.0
                else:
                    # Inverted stack (putting higher card over lower card) blocks the column
                    slot_score = -60.0 - len(pile) * 15.0

            # Solitaire stack quality determines optimal column placement

            if slot_score > best_slot_score:
                best_slot_score = slot_score
                best_slot = s_idx

        net_score = best_slot_score - (best_card_penalty * 0.05)
        return (best_card_idx, best_slot, net_score)

    def _select_flexible_discards(
        self,
        hand: List[Dict[str, Any]],
        discard_piles: List[List[Dict[str, Any]]],
        obj_deck: List[Dict[str, Any]],
        board: List[SlotSim],
        opponents_info: Optional[List[Dict[str, Any]]] = None,
        deck_size: int = 999,
    ) -> Tuple[List[Tuple[int, int]], float]:
        """
        Determines the optimal discard sequence: either 1 single discard or 2 flexible discards.
        Returns:
            (discard_moves, total_discard_score)
            where discard_moves is [(card_idx_1, slot_idx_1)] or [(card_idx_1, slot_idx_1), (card_idx_2_sub, slot_idx_2)]
            Note: card_idx_2_sub is the hand index in the remaining hand AFTER card 1 is removed.
        """
        if not hand:
            return ([], 0.0)

        # 1. Best 1st Discard
        c1_idx, s1_idx, score_1 = self._select_best_discard(
            hand, discard_piles, obj_deck, board=board, opponents_info=opponents_info
        )
        moves = [(c1_idx, s1_idx)]

        if len(hand) <= 1:
            return (moves, score_1)

        # 2. Evaluate Candidate 2nd Discard
        card_1 = hand[c1_idx]
        remaining_hand = [c for i, c in enumerate(hand) if i != c1_idx]
        sim_piles = [list(pile) for pile in discard_piles]
        sim_piles[s1_idx].append(card_1)

        c2_sub_idx, s2_idx, score_2 = self._select_best_discard(
            remaining_hand, sim_piles, obj_deck, board=board, opponents_info=opponents_info
        )
        card_2 = remaining_hand[c2_sub_idx]

        c2_val = int(card_2.get("value", 0))
        c2_type = card_2.get("type", "number")

        obj_val = 0
        if obj_deck:
            top = obj_deck[-1]
            if top.get("type") == "number":
                obj_val = int(top.get("value", 0))

        # Check if card 2 is critically valuable to keep in hand
        must_keep = False
        if c2_type == "joker":
            must_keep = True
        elif obj_val > 0 and (c2_val == obj_val or c2_val == obj_val - 1):
            must_keep = True
        else:
            # Check if card 2 could be played immediately next turn on any current board slot
            for slot in board:
                if not slot.pending_perk:
                    if slot.value == 0 and not slot.is_omni_base and c2_val == 1:
                        must_keep = True
                        break
                    elif slot.value > 0 and c2_val == slot.value + 1:
                        must_keep = True
                        break

        # Hand cycling tempo reward: discarding a 2nd card empties the hand faster,
        # guaranteeing the bot draws 2 fresh cards from the deck next turn instead of 1.
        hand_tempo_reward = 45.0 + (len(hand) - 2) * 10.0

        # MODIFIER A (Threat Mitigation): Throttle or veto 2nd discard if opponent is near victory
        if opponents_info:
            min_opp_rem = min((int(opp.get("rem", 10)) for opp in opponents_info), default=10)
            if min_opp_rem <= 2:
                hand_tempo_reward -= 120.0  # Decisive veto: don't expose 2nd card at match point
            elif min_opp_rem <= 4:
                hand_tempo_reward -= 60.0   # Caution: opponent is close to victory

        # MODIFIER B (Playability / Dead Hand Urgency):
        playable_count = 0
        for c in hand:
            c_val = int(c.get("value", 0)) if c.get("type") == "number" else 0
            if c.get("type") == "joker":
                playable_count += 1
            elif any(
                (s.value == 0 and not s.is_omni_base and c_val == 1) or
                (s.value > 0 and c_val == s.value + 1)
                for s in board if not s.pending_perk
            ):
                playable_count += 1
        dead_ratio = 1.0 - (playable_count / max(1, len(hand)))
        # Dead hand (>50% dead cards) increases urge to cycle (+20); good hand decreases it (-20)
        hand_tempo_reward += (dead_ratio - 0.5) * 40.0

        # MODIFIER C (Deck Depletion Throttle):
        if deck_size < 25:
            hand_tempo_reward -= 30.0

        # Solitaire placement quality + draw velocity
        net_2nd_utility = score_2 + hand_tempo_reward

        # Only discard 2nd card if it's not a prized card AND placement utility is positive
        if not must_keep and net_2nd_utility > 20.0:
            moves.append((c2_sub_idx, s2_idx))
            return (moves, score_1 + net_2nd_utility)

        return (moves, score_1)
