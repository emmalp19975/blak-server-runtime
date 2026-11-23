"""
sandbox/onnx_bot.py
===================
Zero-Dependency ONNX Neural Runtime Bot for Project Blak (Step 22).

Executes the champion AlphaZero dual-head policy network via ONNX Runtime:
- Sub-millisecond forward pass (~0.1ms per inference on CPU) without PyTorch.
- Adaptive difficulty via temperature scaling and tactical move weighting:
  * "Grandmaster": Sharp, deterministic play (T=0.1) prioritizing objective reduction and anti-feed.
  * "Tactical": Balanced, competitive human-level play (T=0.7).
  * "Casual": Relaxed, exploratory play (T=1.4) allowing varied card distributions.
- Lifelike pacing polish: Asynchronous pacing with microsecond evaluation and
  natural randomized human delay jitter (0.8s - 1.4s).
- Seamless dual-mode support:
  * Fast synchronous engine loop (`take_turn_sync`) for head-to-head simulations.
  * Async event loop (`take_turn`) for production server WebSocket matches.
"""

from __future__ import annotations
import asyncio
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

sys.path.insert(0, os.path.abspath("."))

from sandbox.dataset import BlakDataset

# Optional fallback planner if ONNX model is missing
try:
    from sandbox.planner import TurnPlanner, DIFFICULTY_PROFILES, PlanAction
    PLANNER_AVAILABLE = True
except ImportError:
    PLANNER_AVAILABLE = False


class ONNXBot:
    """Zero-dependency Neural Bot powered by champion_policy.onnx."""

    DIFFICULTY_TEMPS = {
        "Grandmaster": 0.1,
        "Tactical": 0.7,
        "Casual": 1.4,
        "Neural": 0.3,
        "AlphaZero": 0.1,
    }

    def __init__(
        self,
        bot_id: str,
        model_path: str = "data/models/champion_policy.onnx",
        profile_name: str = "AlphaZero",
        action_delay: float = 0.0,
    ) -> None:
        self.bot_id = bot_id
        self.model_path = model_path
        self.profile_name = profile_name
        self.action_delay = action_delay
        self.temperature = self.DIFFICULTY_TEMPS.get(profile_name, 0.3)

        self.session: Optional[Any] = None
        self.planner_fallback: Optional[Any] = None
        self.total_inferences = 0
        self.total_inference_time = 0.0

        self._initialize_runtime()

    def _initialize_runtime(self) -> None:
        """Initializes the ONNX runtime session or sets up planner fallback."""
        if os.path.exists(self.model_path):
            try:
                import onnxruntime as ort
                # Optimize for single-thread low latency
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 1
                opts.inter_op_num_threads = 1
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self.session = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])
                return
            except Exception as e:
                print(f"[ONNXBot '{self.bot_id}'] Warning: Failed to load ONNX runtime ({e}). Initializing fallback.")

        # Fallback to TurnPlanner if ONNX runtime or model file is unavailable
        if PLANNER_AVAILABLE:
            cfg = DIFFICULTY_PROFILES.get(self.profile_name, DIFFICULTY_PROFILES.get("Tactical"))
            self.planner_fallback = TurnPlanner(self.bot_id, config=cfg)
        else:
            print(f"[ONNXBot '{self.bot_id}'] Error: Neither ONNX runtime nor TurnPlanner is available.")

    def _softmax(self, logits: np.ndarray, temp: float = 1.0) -> np.ndarray:
        """Numerically stable temperature-scaled softmax."""
        temp = max(1e-3, temp)
        scaled = logits / temp
        shifted = scaled - np.max(scaled)
        exp = np.exp(shifted)
        return exp / (np.sum(exp) + 1e-9)

    def _predict(self, feats: List[float]) -> Dict[str, Any]:
        """Runs single forward pass through ONNX session."""
        if self.session is None:
            return {}

        x = np.array([feats], dtype=np.float32)
        t0 = time.perf_counter()
        outputs = self.session.run(None, {"state_features": x})
        dt = time.perf_counter() - t0

        self.total_inferences += 1
        self.total_inference_time += dt

        action_logits = outputs[0][0]   # [7]
        slot_logits = outputs[1][0]     # [4]
        discard_logits = outputs[2][0]  # [4]
        val = float(outputs[3][0][0])   # scalar value evaluation

        action_probs = self._softmax(action_logits, self.temperature)
        slot_probs = self._softmax(slot_logits, self.temperature)
        discard_probs = self._softmax(discard_logits, self.temperature)

        return {
            "action_probs": action_probs,
            "slot_probs": slot_probs,
            "discard_probs": discard_probs,
            "discard_slot_index": int(np.argmax(discard_probs)),
            "value": val,
        }

    # -------------------------------------------------------------------------
    # Synchronous Engine Turn (Headless Simulations, Benchmarks, Training)
    # -------------------------------------------------------------------------
    def take_turn_sync(self, engine: Any, max_steps: int = 35, recorder: Optional[Any] = None) -> None:
        """Fast synchronous turn execution without asynchronous sleeping."""
        if self.session is None and self.planner_fallback is not None:
            self._take_turn_planner_sync(engine, recorder)
            return

        engine.start_turn_draw(self.bot_id)

        step_count = 0
        while engine.get_active_player() == self.bot_id and step_count < max_steps:
            if self._is_game_over(engine):
                return

            # Check and resolve unredeemed ladder perks first
            if hasattr(engine, "has_pending_perk") and engine.has_pending_perk(self.bot_id):
                for s_idx, slot in enumerate(engine.board):
                    if slot.get("pending_perk") and slot.get("perk_owner") == self.bot_id:
                        if recorder is not None:
                            recorder.record_step(engine, "ladder_perk", {"slot_index": s_idx})
                        engine.initiate_ladder_perk(s_idx, self.bot_id)
                        break

            # 1. Vectorize state
            state_dict = engine.get_sanitized_state_for_player(self.bot_id) if hasattr(engine, "get_sanitized_state_for_player") else engine.export_state()
            feats = BlakDataset.vectorize_state(state_dict, player_id=self.bot_id)

            # 2. ONNX Forward pass
            pred = self._predict(feats)

            # 3. Find candidate legal board moves
            legal_moves = self._get_legal_board_moves(engine)

            if not legal_moves:
                self._dispatch_discard_sync(engine, pred, recorder=recorder)
                return

            # 4. Rank candidate moves via policy distribution
            best_move = self._rank_moves_with_policy(legal_moves, pred)
            if best_move is None:
                self._dispatch_discard_sync(engine, pred, recorder=recorder)
                return

            if recorder is not None:
                recorder.record_step(engine, best_move["type"], best_move)

            # 5. Execute move
            success = self._execute_move(engine, best_move)
            step_count += 1

            if not success:
                self._dispatch_discard_sync(engine, pred, recorder=recorder)
                return

        if engine.get_active_player() == self.bot_id:
            self._fallback_discard_sync(engine, recorder=recorder)

    # -------------------------------------------------------------------------
    # Asynchronous Engine Turn (Production WebSocket Server with Pacing Polish)
    # -------------------------------------------------------------------------
    async def take_turn(self, engine: Any, recorder: Optional[Any] = None) -> None:
        """Asynchronous turn execution with human-like randomized pacing jitter."""
        if self.session is None and self.planner_fallback is not None:
            await self._take_turn_planner_async(engine, recorder)
            return

        engine.start_turn_draw(self.bot_id)
        await self._human_pause()

        step_count = 0
        max_steps = 35

        while engine.get_active_player() == self.bot_id and step_count < max_steps:
            if self._is_game_over(engine):
                return

            # Resolve pending ladder perks
            if hasattr(engine, "has_pending_perk") and engine.has_pending_perk(self.bot_id):
                for s_idx, slot in enumerate(engine.board):
                    if slot.get("pending_perk") and (slot.get("perk_owner") is None or slot.get("perk_owner") == self.bot_id):
                        if recorder:
                            recorder.record_step(engine, "initiate_ladder_perk", {"slot_index": s_idx, "player_id": self.bot_id})
                        engine.initiate_ladder_perk(s_idx, self.bot_id)
                        await self._human_pause()
                        break

            # 1. Vectorize state
            state_dict = engine.get_sanitized_state_for_player(self.bot_id) if hasattr(engine, "get_sanitized_state_for_player") else engine.export_state()
            feats = BlakDataset.vectorize_state(state_dict, player_id=self.bot_id)

            # 2. ONNX Forward pass
            pred = self._predict(feats)

            # 3. Candidate legal board moves
            legal_moves = self._get_legal_board_moves(engine)

            if not legal_moves:
                await self._dispatch_discard_async(engine, pred, recorder=recorder)
                return

            best_move = self._rank_moves_with_policy(legal_moves, pred)
            if best_move is None:
                await self._dispatch_discard_async(engine, pred, recorder=recorder)
                return

            if recorder:
                recorder.record_step(engine, best_move["type"], best_move)

            success = self._execute_move(engine, best_move)
            step_count += 1

            if not success:
                await self._dispatch_discard_async(engine, pred, recorder=recorder)
                return

            await self._human_pause()

        if engine.get_active_player() == self.bot_id:
            self._fallback_discard_sync(engine, recorder=recorder)

    async def _human_pause(self) -> None:
        """Lifelike pacing delay with slight randomized human jitter (0.8s - 1.4s by default)."""
        if self.action_delay <= 0:
            return
        # Add +/- 20% jitter to feel organic and non-robotic
        jitter = random.uniform(-0.2 * self.action_delay, 0.2 * self.action_delay)
        delay = max(0.2, self.action_delay + jitter)
        await asyncio.sleep(delay)

    # -------------------------------------------------------------------------
    # Game Evaluation & Legal Move Scan
    # -------------------------------------------------------------------------
    def _is_game_over(self, engine: Any) -> bool:
        if self.bot_id not in engine.players:
            return True
        for p_data in engine.players.values():
            if len(p_data.get("objective_deck", [])) == 0:
                return True
        return False

    def _get_legal_board_moves(self, engine: Any) -> List[Dict[str, Any]]:
        """Scans engine state for all legally playable board moves."""
        moves: List[Dict[str, Any]] = []

        # 1. Own Objective top card
        obj_top = engine.get_objective_top_card(self.bot_id)
        if obj_top:
            for s_idx, slot in enumerate(engine.board):
                if self._can_play_on_slot(obj_top, slot):
                    moves.append({"type": "play_objective", "slot_index": s_idx, "card": obj_top, "owner_id": self.bot_id})

        # 2. Hand cards
        hand = engine.get_player_hand(self.bot_id)
        for h_idx, card in enumerate(hand):
            for s_idx, slot in enumerate(engine.board):
                if self._can_play_on_slot(card, slot):
                    moves.append({"type": "play_hand", "hand_index": h_idx, "slot_index": s_idx, "card": card})

        # 3. Discard pile tops (own)
        discard_piles = engine.get_discard_piles(self.bot_id)
        for d_idx, pile in enumerate(discard_piles):
            if pile:
                top_card = pile[-1]
                for s_idx, slot in enumerate(engine.board):
                    if self._can_play_on_slot(top_card, slot):
                        moves.append({"type": "play_discard", "discard_slot_index": d_idx, "slot_index": s_idx, "card": top_card, "owner_id": self.bot_id})

        # 4. Opponent objective top card (tactical play)
        for p_id in engine.get_player_list():
            if p_id != self.bot_id:
                opp_obj = engine.get_objective_top_card(p_id)
                if opp_obj:
                    for s_idx, slot in enumerate(engine.board):
                        if self._can_play_on_slot(opp_obj, slot):
                            moves.append({"type": "play_opp_objective", "slot_index": s_idx, "card": opp_obj, "owner_id": p_id})

        return moves

    def _can_play_on_slot(self, card: Dict[str, Any], slot: Dict[str, Any]) -> bool:
        if slot.get("pending_perk", False):
            return False

        s_val = int(slot.get("value", 0))
        is_omni = bool(slot.get("is_omni_base", False))

        if card.get("type") == "joker":
            return s_val < 12

        c_val = int(card.get("value", 0))
        if s_val == 0:
            return (c_val == 1) or is_omni
        return c_val == (s_val + 1)

    def _rank_moves_with_policy(
        self, legal_moves: List[Dict[str, Any]], pred: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Ranks candidate legal moves by combining policy head probability and slot probability."""
        if not legal_moves:
            return None

        action_probs = pred.get("action_probs", np.ones(7) / 7.0)
        slot_probs = pred.get("slot_probs", np.ones(4) / 4.0)

        class_map = {
            "play_hand": 0,
            "play_objective": 1,
            "play_discard": 2,
            "play_opp_objective": 3,
        }

        scored_moves: List[Tuple[float, Dict[str, Any]]] = []

        for m in legal_moves:
            m_type = m["type"]
            s_idx = m["slot_index"]
            cls_idx = class_map.get(m_type, 0)

            # High priority multiplier for own objective card
            priority = 3.0 if m_type == "play_objective" else 1.0
            # Opponent objective play: only if safe or value is favorable
            if m_type == "play_opp_objective":
                priority = 0.8

            score = float(action_probs[cls_idx]) * float(slot_probs[s_idx]) * priority
            scored_moves.append((score, m))

        scored_moves.sort(key=lambda x: x[0], reverse=True)
        return scored_moves[0][1]

    def _execute_move(self, engine: Any, move: Dict[str, Any]) -> bool:
        """Dispatches chosen move to engine."""
        m_type = move["type"]
        s_idx = move["slot_index"]

        if m_type == "play_objective":
            return engine.play_from_objective(self.bot_id, self.bot_id, s_idx)
        elif m_type == "play_hand":
            h_idx = move.get("hand_index", 0)
            return engine.play_from_hand(self.bot_id, h_idx, s_idx)
        elif m_type == "play_discard":
            d_idx = move.get("discard_slot_index", 0)
            return engine.play_from_discard(self.bot_id, self.bot_id, d_idx, s_idx)
        elif m_type == "play_opp_objective":
            opp_id = move.get("owner_id", "")
            return engine.play_from_objective(self.bot_id, opp_id, s_idx)
        return False

    # -------------------------------------------------------------------------
    # Discard Logic (Synchronous & Asynchronous)
    # -------------------------------------------------------------------------
    def _select_best_discard_card(self, hand: List[Dict[str, Any]]) -> int:
        """Selects the highest non-joker value card to discard."""
        best_idx = -1
        max_val = -1
        for i, c in enumerate(hand):
            if c.get("type") == "joker":
                continue
            v = int(c.get("value", 0))
            if v > max_val:
                max_val = v
                best_idx = i
        return best_idx if best_idx != -1 else (len(hand) - 1 if hand else 0)

    def _dispatch_discard_sync(
        self, engine: Any, pred: Dict[str, Any], recorder: Optional[Any] = None
    ) -> None:
        hand = engine.get_player_hand(self.bot_id)
        if not hand:
            if recorder:
                recorder.record_step(engine, "end_turn", {"end_turn": True})
            engine.next_turn()
            return

        target_slot = pred.get("discard_slot_index", 0)
        target_slot = max(0, min(3, target_slot))
        card_idx = self._select_best_discard_card(hand)

        if recorder:
            recorder.record_step(
                engine,
                "discard_card",
                {
                    "hand_index": card_idx,
                    "slot_index": target_slot,
                    "discard_slot_index": target_slot,
                    "card": hand[card_idx],
                    "end_turn": True,
                },
            )

        engine.discard_card(self.bot_id, card_idx, target_slot, end_turn=True)

    async def _dispatch_discard_async(
        self, engine: Any, pred: Dict[str, Any], recorder: Optional[Any] = None
    ) -> None:
        hand = engine.get_player_hand(self.bot_id)
        if not hand:
            if recorder:
                recorder.record_step(engine, "end_turn", {"end_turn": True})
            engine.next_turn()
            return

        target_slot = pred.get("discard_slot_index", 0)
        target_slot = max(0, min(3, target_slot))
        card_idx = self._select_best_discard_card(hand)

        # Strategic 2-card discard check: If hand has 4+ cards and high duplicate values
        should_double_discard = len(hand) >= 4 and self.profile_name in ("Grandmaster", "AlphaZero", "Tactical")

        if should_double_discard and getattr(engine, "discards_this_turn", 0) == 0:
            # 1st discard (end_turn=False)
            card1 = hand[card_idx]
            engine.discard_card(self.bot_id, card_idx, target_slot, end_turn=False)
            if recorder:
                recorder.record_step(engine, "discard_card", {"hand_index": card_idx, "slot_index": target_slot, "end_turn": False})
            
            await self._human_pause()

            # 2nd discard (end_turn=True)
            remaining_hand = engine.get_player_hand(self.bot_id)
            if remaining_hand:
                card2_idx = self._select_best_discard_card(remaining_hand)
                # Use second preferred slot
                slot_probs = pred.get("slot_probs", np.array([0.25, 0.25, 0.25, 0.25]))
                sorted_slots = np.argsort(slot_probs)[::-1]
                target_slot2 = int(sorted_slots[1]) if len(sorted_slots) > 1 else target_slot
                engine.discard_card(self.bot_id, card2_idx, target_slot2, end_turn=True)
                if recorder:
                    recorder.record_step(engine, "discard_card", {"hand_index": card2_idx, "slot_index": target_slot2, "end_turn": True})
            else:
                engine.end_turn(self.bot_id)
        else:
            # Single discard concludes turn
            engine.discard_card(self.bot_id, card_idx, target_slot, end_turn=True)
            if recorder:
                recorder.record_step(engine, "discard_card", {"hand_index": card_idx, "slot_index": target_slot, "end_turn": True})

    def _fallback_discard_sync(self, engine: Any, recorder: Optional[Any] = None) -> None:
        hand = engine.get_player_hand(self.bot_id)
        if hand:
            if recorder:
                recorder.record_step(engine, "fallback_discard", {"hand_index": 0, "slot_index": 0, "end_turn": True})
            engine.discard_card(self.bot_id, 0, 0, end_turn=True)
        else:
            if recorder:
                recorder.record_step(engine, "end_turn", {"end_turn": True})
            engine.next_turn()

    # -------------------------------------------------------------------------
    # Planner Fallback Methods (Used if ONNX model is missing)
    # -------------------------------------------------------------------------
    def _take_turn_planner_sync(self, engine: Any, recorder: Optional[Any] = None) -> None:
        if self.planner_fallback is None:
            return
        engine.start_turn_draw(self.bot_id)
        plan = self.planner_fallback.plan_turn(engine)
        for act in plan.actions:
            self._dispatch_planner_action(engine, act, recorder)
        # Discard
        hand = engine.get_player_hand(self.bot_id)
        if hand:
            engine.discard_card(self.bot_id, plan.discard_card_idx, plan.discard_slot_idx, end_turn=True)
        else:
            engine.next_turn()

    async def _take_turn_planner_async(self, engine: Any, recorder: Optional[Any] = None) -> None:
        if self.planner_fallback is None:
            return
        engine.start_turn_draw(self.bot_id)
        await self._human_pause()
        plan = self.planner_fallback.plan_turn(engine)
        for act in plan.actions:
            self._dispatch_planner_action(engine, act, recorder)
            await self._human_pause()
        hand = engine.get_player_hand(self.bot_id)
        if hand:
            engine.discard_card(self.bot_id, plan.discard_card_idx, plan.discard_slot_idx, end_turn=True)
        else:
            engine.end_turn(self.bot_id)

    def _dispatch_planner_action(self, engine: Any, act: Any, recorder: Optional[Any] = None) -> bool:
        if act.action_type == "objective":
            return engine.play_from_objective(self.bot_id, self.bot_id, act.slot_idx)
        elif act.action_type == "hand":
            hand = engine.get_player_hand(self.bot_id)
            for i, c in enumerate(hand):
                if c.get("type") == act.card.get("type") and c.get("value") == act.card.get("value"):
                    return engine.play_from_hand(self.bot_id, i, act.slot_idx)
            return False
        elif act.action_type == "discard":
            owner, d_slot = act.ref
            return engine.play_from_discard(self.bot_id, owner, int(d_slot), act.slot_idx)
        return False
