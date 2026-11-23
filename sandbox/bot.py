"""
sandbox/bot.py
==============
Human-Like Strategic Sandbox Bot Controller.

Powered by sandbox.planner.TurnPlanner:
1. Combinatorial Turn-Graph Planning (multi-step combo searches).
2. Dynamic Risk/Reward Threat Mitigation (prevents infinite deadlock stalls).
3. Hand-Flush Tempo Optimization (empties hand to trigger immediate 5-card redraw).
4. Solitaire Column Discard Strategy (builds descending columns to prevent burying low cards).
5. Configurable Real-time Delays for lifelike human pacing or instant headless balance runs.
"""

from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from sandbox.planner import PlanAction, PlannerConfig, TurnPlan, TurnPlanner

if TYPE_CHECKING:
    from sandbox.engine import SandboxCardGame


class SandboxBot:
    def __init__(
        self,
        bot_id: str,
        action_delay: float = 1.5,
        config: Optional[PlannerConfig] = None,
    ) -> None:
        self.bot_id = bot_id
        self.action_delay = action_delay
        self.config = config
        self.planner = TurnPlanner(bot_id, config=config)

    async def _pause(self) -> None:
        if self.action_delay > 0:
            await asyncio.sleep(self.action_delay)

    # -------------------------------------------------------------------------
    # Public Turn Execution
    # -------------------------------------------------------------------------
    async def take_turn(self, engine: "SandboxCardGame") -> None:
        """Executes a complete human-like strategic bot turn with real-time delays."""
        engine.start_turn_draw(self.bot_id)
        await self._pause()

        step_count = 0
        max_steps = 35

        while engine.get_active_player() == self.bot_id and step_count < max_steps:
            if self._is_game_over(engine):
                return

            plan = self.planner.plan_turn(engine)
            if not plan.actions:
                # No further profitable plays: conclude turn with strategic 1-card or 2-card discard
                await self._dispatch_discard_async(engine, plan)
                return

            # Execute the first planned action
            action = plan.actions[0]
            success = self._dispatch_plan_action(engine, action)
            step_count += 1

            if not success:
                # Action failed due to unforeseen state change: execute safe discard
                await self._dispatch_discard_async(engine, plan)
                return

            await self._pause()

        if engine.get_active_player() == self.bot_id:
            self._fallback_discard(engine)

    def take_turn_sync(self, engine: "SandboxCardGame") -> None:
        """Fast synchronous turn execution for headless balance simulations."""
        engine.start_turn_draw(self.bot_id)

        step_count = 0
        max_steps = 35

        while engine.get_active_player() == self.bot_id and step_count < max_steps:
            if self._is_game_over(engine):
                return

            plan = self.planner.plan_turn(engine)
            if not plan.actions:
                self._dispatch_discard_sync(engine, plan)
                return

            action = plan.actions[0]
            success = self._dispatch_plan_action(engine, action)
            step_count += 1

            if not success:
                self._dispatch_discard_sync(engine, plan)
                return

        if engine.get_active_player() == self.bot_id:
            self._fallback_discard(engine)

    def _is_game_over(self, engine: "SandboxCardGame") -> bool:
        if self.bot_id not in engine.players:
            return True
        for p_data in engine.players.values():
            if len(p_data.get("objective_deck", [])) == 0:
                return True
        return False

    # -------------------------------------------------------------------------
    # Action Dispatching
    # -------------------------------------------------------------------------
    def _dispatch_plan_action(self, engine: "SandboxCardGame", action: PlanAction) -> bool:
        """Executes a single planned action on the Sandbox engine."""
        if action.action_type == "perk":
            res = engine.initiate_ladder_perk(action.slot_idx, self.bot_id)
            if res and self.action_delay > 0:
                print(f"[Sandbox Bot '{self.bot_id}'] Initiated ladder perk on slot {action.slot_idx}!")
            return res

        elif action.action_type == "decline_perk":
            res = engine.decline_ladder_perk(action.slot_idx, self.bot_id)
            if res and self.action_delay > 0:
                print(f"[Sandbox Bot '{self.bot_id}'] Declined ladder perk on slot {action.slot_idx}!")
            return res

        elif action.action_type == "objective":
            res = engine.play_from_objective(self.bot_id, self.bot_id, action.slot_idx)
            if res and self.action_delay > 0:
                print(
                    f"[Sandbox Bot '{self.bot_id}'] [Objective] Played {action.card} onto slot {action.slot_idx}!"
                )
            return res

        elif action.action_type == "hand":
            hand = engine.get_player_hand(self.bot_id)
            target_idx = -1

            # Match by card value and type
            if action.card is not None:
                for i, c in enumerate(hand):
                    if c.get("type") == action.card.get("type") and c.get("value") == action.card.get("value"):
                        target_idx = i
                        break

            # Fallback to ref index
            if target_idx == -1 and action.ref is not None and 0 <= int(action.ref) < len(hand):
                target_idx = int(action.ref)

            if target_idx != -1:
                res = engine.play_from_hand(self.bot_id, target_idx, action.slot_idx)
                if res and self.action_delay > 0:
                    print(
                        f"[Sandbox Bot '{self.bot_id}'] [Hand Play] Played {action.card} onto slot {action.slot_idx}!"
                    )
                return res
            return False

        elif action.action_type == "discard":
            owner_id, d_slot = action.ref
            res = engine.play_from_discard(self.bot_id, owner_id, int(d_slot), action.slot_idx)
            if res and self.action_delay > 0:
                print(
                    f"[Sandbox Bot '{self.bot_id}'] [Discard Play] Played ({owner_id} slot {d_slot}) {action.card} onto slot {action.slot_idx}!"
                )
            return res

        elif action.action_type == "opponent_objective":
            opp_id = str(action.ref)
            res = engine.play_from_objective(self.bot_id, opp_id, action.slot_idx)
            if res and self.action_delay > 0:
                print(
                    f"[Sandbox Bot '{self.bot_id}'] [Tactical Play] Played {opp_id}'s Objective {action.card} onto slot {action.slot_idx}!"
                )
            return res

        return False

    async def _dispatch_discard_async(self, engine: "SandboxCardGame", plan: TurnPlan) -> None:
        """Concludes the turn with 1 or 2 flexible discards and human-paced pauses."""
        moves = plan.discard_moves
        if not moves:
            moves = [(plan.discard_card_idx, plan.discard_slot_idx)]

        hand = engine.get_player_hand(self.bot_id)
        if not hand:
            engine.next_turn()
            return

        if len(moves) == 1:
            c_idx, s_idx = moves[0]
            c_idx = max(0, min(c_idx, len(hand) - 1))
            s_idx = max(0, min(s_idx, 3))
            card = hand[c_idx]
            if self.action_delay > 0:
                print(
                    f"[Sandbox Bot '{self.bot_id}'] Discarded {card} "
                    f"to slot {s_idx} (Single Discard). Turn complete."
                )
            engine.discard_card(self.bot_id, c_idx, s_idx, end_turn=True)
        else:
            # 1st discard (flexible mode: +2s timer extension)
            c1_idx, s1_idx = moves[0]
            c1_idx = max(0, min(c1_idx, len(hand) - 1))
            s1_idx = max(0, min(s1_idx, 3))
            card1 = hand[c1_idx]
            if self.action_delay > 0:
                print(
                    f"[Sandbox Bot '{self.bot_id}'] [Flexible 1/2] Discarded {card1} "
                    f"to slot {s1_idx} (+2s timer signal)."
                )
            engine.discard_card(self.bot_id, c1_idx, s1_idx, end_turn=False)

            # Human-paced pause between discards
            await self._pause()

            # 2nd discard (ends turn)
            remaining_hand = engine.get_player_hand(self.bot_id)
            if not remaining_hand:
                engine.end_turn(self.bot_id)
                return

            c2_idx, s2_idx = moves[1]
            c2_idx = max(0, min(c2_idx, len(remaining_hand) - 1))
            s2_idx = max(0, min(s2_idx, 3))
            card2 = remaining_hand[c2_idx]
            if self.action_delay > 0:
                print(
                    f"[Sandbox Bot '{self.bot_id}'] [Flexible 2/2] Discarded {card2} "
                    f"to slot {s2_idx}. Turn complete."
                )
            engine.discard_card(self.bot_id, c2_idx, s2_idx, end_turn=True)

    def _dispatch_discard_sync(self, engine: "SandboxCardGame", plan: TurnPlan) -> None:
        """Instant synchronous execution of 1 or 2 flexible discards for headless balance runs."""
        moves = plan.discard_moves
        if not moves:
            moves = [(plan.discard_card_idx, plan.discard_slot_idx)]

        hand = engine.get_player_hand(self.bot_id)
        if not hand:
            engine.next_turn()
            return

        if len(moves) == 1:
            c_idx, s_idx = moves[0]
            c_idx = max(0, min(c_idx, len(hand) - 1))
            s_idx = max(0, min(s_idx, 3))
            engine.discard_card(self.bot_id, c_idx, s_idx, end_turn=True)
        else:
            c1_idx, s1_idx = moves[0]
            c1_idx = max(0, min(c1_idx, len(hand) - 1))
            s1_idx = max(0, min(s1_idx, 3))
            engine.discard_card(self.bot_id, c1_idx, s1_idx, end_turn=False)

            remaining_hand = engine.get_player_hand(self.bot_id)
            if not remaining_hand:
                engine.end_turn(self.bot_id)
                return

            c2_idx, s2_idx = moves[1]
            c2_idx = max(0, min(c2_idx, len(remaining_hand) - 1))
            s2_idx = max(0, min(s2_idx, 3))
            engine.discard_card(self.bot_id, c2_idx, s2_idx, end_turn=True)

    def _dispatch_discard(self, engine: "SandboxCardGame", card_idx: int, slot_idx: int) -> None:
        """Legacy helper for direct single-discard invocations."""
        hand = engine.get_player_hand(self.bot_id)
        if not hand:
            engine.next_turn()
            return

        c_idx = max(0, min(card_idx, len(hand) - 1))
        s_idx = max(0, min(slot_idx, 3))
        engine.discard_card(self.bot_id, c_idx, s_idx, end_turn=True)

    def _fallback_discard(self, engine: "SandboxCardGame") -> None:
        hand = engine.get_player_hand(self.bot_id)
        if not hand:
            engine.next_turn()
            return
        if getattr(engine, "discards_this_turn", 0) > 0:
            engine.end_turn(self.bot_id)
        else:
            engine.end_turn_with_discard(self.bot_id, 0, 0)
