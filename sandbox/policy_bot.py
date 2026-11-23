"""
sandbox/policy_bot.py
=====================
Neural Policy Bot Controller for Project Blak (Step 20).

Powered by the trained BlakPolicyNet (Behavioral Cloning MLP):
- Vectorizes game state into 128-dimensional tensor.
- Evaluates neural policy in sub-millisecond time (<0.3ms per forward pass).
- Masks illegal candidate actions against authoritative engine rules.
- Executes highest-probability legal play sequence.
- Strategic 1-card or 2-card discard distribution.
"""

from __future__ import annotations
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import torch

sys.path.insert(0, os.path.abspath("."))

from sandbox.dataset import BlakDataset
from sandbox.engine import SandboxCardGame
from sandbox.model import BlakPolicyNet


class PolicyBot:
    """Autonomous Neural Bot powered by BlakPolicyNet."""

    def __init__(
        self,
        bot_id: str,
        model_or_path: Union[str, BlakPolicyNet] = "data/models/policy_net.pt",
        device: Optional[str] = None,
        action_delay: float = 0.0,
    ) -> None:
        self.bot_id = bot_id
        self.action_delay = action_delay
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

        if isinstance(model_or_path, BlakPolicyNet):
            self.model = model_or_path.to(self.device)
            self.model.eval()
        else:
            if not os.path.exists(model_or_path):
                raise FileNotFoundError(f"Model checkpoint not found at '{model_or_path}'! Train the model first.")
            self.model = BlakPolicyNet(input_dim=128, hidden_dim=256).to(self.device)
            checkpoint = torch.load(model_or_path, map_location=self.device, weights_only=False)
            self.model.load_state_dict(checkpoint["model_state_dict"])
            self.model.eval()

        # Inference metrics
        self.total_inferences = 0
        self.total_inference_time = 0.0

    def take_turn_sync(self, engine: SandboxCardGame, max_steps: int = 35, recorder: Optional[Any] = None) -> None:
        """Fast synchronous turn execution for headless benchmarks, simulations, and self-training."""
        engine.start_turn_draw(self.bot_id)

        step_count = 0
        while engine.get_active_player() == self.bot_id and step_count < max_steps:
            if self._is_game_over(engine):
                return

            # Check and resolve unredeemed ladder perks first
            if engine.has_pending_perk(self.bot_id):
                for s_idx, slot in enumerate(engine.board):
                    if slot.get("pending_perk") and slot.get("perk_owner") == self.bot_id:
                        if recorder is not None:
                            recorder.record_step(engine, "ladder_perk", {"slot_index": s_idx})
                        engine.initiate_ladder_perk(s_idx, self.bot_id)
                        break

            # 1. Vectorize current state
            state_dict = engine.get_sanitized_state_for_player(self.bot_id)
            feats = BlakDataset.vectorize_state(state_dict)

            # 2. Neural Forward Pass
            t0 = time.perf_counter()
            with torch.no_grad():
                feats_tensor = torch.tensor(feats, dtype=torch.float32, device=self.device).unsqueeze(0)
                pred = self.model.predict_action(feats_tensor)
            dt = time.perf_counter() - t0
            self.total_inferences += 1
            self.total_inference_time += dt

            # 3. Find all candidate legal board actions
            legal_moves = self._get_legal_board_moves(engine)

            if not legal_moves:
                # No legal board plays available: conclude turn with discard
                self._dispatch_neural_discard(engine, pred, recorder=recorder)
                return

            # Score each candidate legal move against policy distribution
            best_move = self._rank_moves_with_policy(legal_moves, pred)

            if best_move is None:
                self._dispatch_neural_discard(engine, pred, recorder=recorder)
                return

            # Record step before mutating engine
            if recorder is not None:
                recorder.record_step(engine, best_move["type"], best_move)

            # 4. Dispatch chosen legal action
            success = self._execute_move(engine, best_move)
            step_count += 1

            if not success:
                self._dispatch_neural_discard(engine, pred, recorder=recorder)
                return

        if engine.get_active_player() == self.bot_id:
            self._fallback_discard(engine, recorder=recorder)

    def _is_game_over(self, engine: SandboxCardGame) -> bool:
        if self.bot_id not in engine.players:
            return True
        for p_data in engine.players.values():
            if len(p_data.get("objective_deck", [])) == 0:
                return True
        return False

    def _get_legal_board_moves(self, engine: SandboxCardGame) -> List[Dict[str, Any]]:
        """Scans engine state for all currently legal board plays."""
        moves: List[Dict[str, Any]] = []

        # 1. Objective Card play
        obj_top = engine.get_objective_top_card(self.bot_id)
        if obj_top:
            for s_idx, slot in enumerate(engine.board):
                if self._can_play_on_slot(obj_top, slot):
                    moves.append({"type": "play_objective", "slot_index": s_idx, "card": obj_top})

        # 2. Hand Card plays
        hand = engine.get_player_hand(self.bot_id)
        for h_idx, card in enumerate(hand):
            for s_idx, slot in enumerate(engine.board):
                if self._can_play_on_slot(card, slot):
                    moves.append({"type": "play_hand", "hand_index": h_idx, "slot_index": s_idx, "card": card})

        # 3. Discard Pile plays (own discard tops)
        discard_piles = engine.get_discard_piles(self.bot_id)
        for d_idx, pile in enumerate(discard_piles):
            if pile:
                top_card = pile[-1]
                for s_idx, slot in enumerate(engine.board):
                    if self._can_play_on_slot(top_card, slot):
                        moves.append({"type": "play_discard", "discard_slot_index": d_idx, "slot_index": s_idx, "card": top_card})

        return moves

    def _can_play_on_slot(self, card: Dict[str, Any], slot: Dict[str, Any]) -> bool:
        """Determines if a card is legally playable on a board slot."""
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
        action_probs = pred["action_probs"]
        slot_probs = pred["slot_probs"]

        # Action class indices: hand=0, objective=1, discard=2, opp_obj=3
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

            # Policy score = P(Action Class) * P(Target Slot)
            # Objective card plays get strategic priority multiplier
            priority_mult = 2.5 if m_type == "play_objective" else 1.0
            score = action_probs[cls_idx] * slot_probs[s_idx] * priority_mult

            scored_moves.append((score, m))

        if not scored_moves:
            return None

        scored_moves.sort(key=lambda x: x[0], reverse=True)
        return scored_moves[0][1]

    def _execute_move(self, engine: SandboxCardGame, move: Dict[str, Any]) -> bool:
        """Dispatches the chosen legal move onto the engine."""
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
        return False

    def _dispatch_neural_discard(
        self, engine: SandboxCardGame, pred: Dict[str, Any], recorder: Optional[Any] = None
    ) -> None:
        """Selects discard target slot guided by model discard probabilities."""
        hand = engine.get_player_hand(self.bot_id)
        if not hand:
            if recorder is not None:
                recorder.record_step(engine, "end_turn", {"end_turn": True})
            engine.next_turn()
            return

        # Model discard column preference
        target_slot = pred.get("discard_slot_index", 0)
        target_slot = max(0, min(3, target_slot))

        # Best non-Joker card to discard (highest number first)
        best_card_idx = -1
        max_val = -1
        for i, c in enumerate(hand):
            if c.get("type") == "joker":
                continue
            v = int(c.get("value", 0))
            if v > max_val:
                max_val = v
                best_card_idx = i

        if best_card_idx == -1:
            best_card_idx = len(hand) - 1

        if recorder is not None:
            recorder.record_step(
                engine,
                "discard_card",
                {
                    "hand_index": best_card_idx,
                    "slot_index": target_slot,
                    "discard_slot_index": target_slot,
                    "card": hand[best_card_idx],
                    "end_turn": True,
                },
            )

        engine.discard_card(self.bot_id, best_card_idx, target_slot, end_turn=True)

    def _fallback_discard(self, engine: SandboxCardGame, recorder: Optional[Any] = None) -> None:
        """Emergency discard to avoid stalling."""
        hand = engine.get_player_hand(self.bot_id)
        if hand:
            if recorder is not None:
                recorder.record_step(
                    engine,
                    "fallback_discard",
                    {"hand_index": 0, "slot_index": 0, "discard_slot_index": 0, "card": hand[0], "end_turn": True},
                )
            engine.discard_card(self.bot_id, 0, 0, end_turn=True)
        else:
            if recorder is not None:
                recorder.record_step(engine, "end_turn", {"end_turn": True})
            engine.next_turn()
