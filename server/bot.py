"""
server/bot.py
=============
Production AI Bot Controller for Blak Game Server.
Uses the combinatorial TurnPlanner from sandbox.planner to execute human-like
strategic turns directly against the server-authoritative CardGame engine.
"""

from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sandbox.planner import PlanAction, PlannerConfig, TurnPlan, TurnPlanner, DIFFICULTY_PROFILES

if TYPE_CHECKING:
    from game_engine import CardGame  # Deferred import: circular dependency


class ServerBot:
    def __init__(
        self,
        bot_id: str,
        profile_name: str = "AlphaZero",
        action_delay: float = 1.2,
        custom_config: Optional[PlannerConfig] = None,
    ) -> None:
        self.bot_id = bot_id
        self.profile_name = profile_name
        self.action_delay = action_delay

        # Check if this profile targets the Neural ONNX runtime (Mimic profiles always use TurnPlanner)
        self.is_neural = any(k in profile_name for k in ("AlphaZero", "Neural")) and not profile_name.startswith("Mimic")
        self.onnx_bot = None

        if self.is_neural:
            try:
                from sandbox.onnx_bot import ONNXBot  # Deferred import: circular dependency
                onnx_profile = "Grandmaster" if "Grandmaster" in profile_name else "AlphaZero"
                self.onnx_bot = ONNXBot(bot_id, profile_name=onnx_profile, action_delay=action_delay)
            except Exception as e:
                print(f"[ServerBot '{bot_id}'] Warning: Could not initialize ONNX runtime ({e}). Falling back to TurnPlanner.")
                self.onnx_bot = None

        if custom_config is not None:
            self.config = custom_config
        else:
            clean_key = profile_name.replace("(Classic)", "").replace("(Neural)", "").replace("(Cheater)", "").strip()
            for key in ("Absolute King", "King", "Master", "Adept", "Apprentice", "Novice", "Oracle", "Houdini", "Apex", "Casual", "Tactical", "Aggressive", "Grandmaster"):
                if key.lower() in clean_key.lower():
                    clean_key = key
                    break
            self.config = DIFFICULTY_PROFILES.get(clean_key, DIFFICULTY_PROFILES["Adept"])
        self.planner = TurnPlanner(bot_id, config=self.config)

    def update_config(self, new_config: PlannerConfig) -> None:
        """Dynamically hot-reloads the bot's planner configuration in real time."""
        self.config = new_config
        self.planner.config = new_config

    async def _pause(self) -> None:
        if self.action_delay > 0:
            import random  # Deferred import: circular dependency
            jitter = random.uniform(-0.15 * self.action_delay, 0.15 * self.action_delay)
            await asyncio.sleep(max(0.2, self.action_delay + jitter))

    async def take_turn(self, engine: "CardGame", recorder: Optional[Any] = None) -> None:
        """Executes a complete strategic bot turn with real-time delays."""
        if self.onnx_bot is not None and self.onnx_bot.session is not None:
            self.onnx_bot.action_delay = self.action_delay
            await self.onnx_bot.take_turn(engine, recorder=recorder)
            return

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
                await self._dispatch_discard(engine, plan, recorder=recorder)
                return

            action = plan.actions[0]
            success = self._dispatch_plan_action(engine, action, recorder=recorder)
            step_count += 1

            if not success:
                # Action failed due to unforeseen state shift: execute safe discard
                await self._dispatch_discard(engine, plan, recorder=recorder)
                return

            await self._pause()

        if engine.get_active_player() == self.bot_id:
            self._fallback_discard(engine, recorder=recorder)

    def _is_game_over(self, engine: "CardGame") -> bool:
        if self.bot_id not in engine.players:
            return True
        for p_data in engine.players.values():
            if len(p_data.get("objective_deck", [])) == 0:
                return True
        return False

    def _dispatch_plan_action(
        self, engine: "CardGame", action: PlanAction, recorder: Optional[Any] = None
    ) -> bool:
        """Executes a single planned action on the production CardGame engine."""
        if action.action_type == "perk":
            res = engine.initiate_ladder_perk(action.slot_idx, self.bot_id)
            if res and self.action_delay > 0:
                print(f"[Server Bot '{self.bot_id}'] Initiated ladder perk on slot {action.slot_idx}!")
            if res and recorder:
                recorder.record_step(engine, "initiate_ladder_perk", {"slot_index": action.slot_idx, "player_id": self.bot_id})
            return res

        elif action.action_type == "decline_perk":
            res = engine.decline_ladder_perk(action.slot_idx, self.bot_id)
            if res and self.action_delay > 0:
                print(f"[Server Bot '{self.bot_id}'] Declined ladder perk on slot {action.slot_idx}!")
            if res and recorder:
                recorder.record_step(engine, "decline_ladder_perk", {"slot_index": action.slot_idx, "player_id": self.bot_id})
            return res

        elif action.action_type == "objective":
            res = engine.play_from_objective(self.bot_id, self.bot_id, action.slot_idx)
            if res and self.action_delay > 0:
                print(
                    f"[Server Bot '{self.bot_id}'] [Objective] Played {action.card} onto slot {action.slot_idx}!"
                )
            if res and recorder:
                recorder.record_step(engine, "play_card", {
                    "source": "objective",
                    "card_index": 0,
                    "target_slot": action.slot_idx,
                    "owner_id": self.bot_id,
                    "player_id": self.bot_id,
                })
            return res

        elif action.action_type == "hand":
            hand = engine.get_player_hand(self.bot_id)
            target_idx = -1

            if action.card is not None:
                for i, c in enumerate(hand):
                    if c.get("type") == action.card.get("type") and c.get("value") == action.card.get("value"):
                        target_idx = i
                        break

            if target_idx == -1 and action.ref is not None and 0 <= int(action.ref) < len(hand):
                target_idx = int(action.ref)

            if target_idx != -1:
                res = engine.play_from_hand(self.bot_id, target_idx, action.slot_idx)
                if res and self.action_delay > 0:
                    print(
                        f"[Server Bot '{self.bot_id}'] [Hand Play] Played {action.card} onto slot {action.slot_idx}!"
                    )
                if res and recorder:
                    recorder.record_step(engine, "play_card", {
                        "source": "hand",
                        "card_index": target_idx,
                        "target_slot": action.slot_idx,
                        "owner_id": self.bot_id,
                        "player_id": self.bot_id,
                    })
                return res
            return False

        elif action.action_type == "discard":
            owner_id, d_slot = action.ref
            res = engine.play_from_discard(self.bot_id, owner_id, int(d_slot), action.slot_idx)
            if res and self.action_delay > 0:
                print(
                    f"[Server Bot '{self.bot_id}'] [Discard Play] Played ({owner_id} slot {d_slot}) {action.card} onto slot {action.slot_idx}!"
                )
            if res and recorder:
                recorder.record_step(engine, "play_card", {
                    "source": "discard",
                    "discard_slot": int(d_slot),
                    "target_slot": action.slot_idx,
                    "owner_id": owner_id,
                    "player_id": self.bot_id,
                })
            return res

        elif action.action_type == "opponent_objective":
            opp_id = str(action.ref)
            res = engine.play_from_objective(self.bot_id, opp_id, action.slot_idx)
            if res and self.action_delay > 0:
                print(
                    f"[Server Bot '{self.bot_id}'] [Tactical Play] Played {opp_id}'s Objective {action.card} onto slot {action.slot_idx}!"
                )
            if res and recorder:
                recorder.record_step(engine, "play_card", {
                    "source": "objective",
                    "card_index": 0,
                    "target_slot": action.slot_idx,
                    "owner_id": opp_id,
                    "player_id": self.bot_id,
                })
            return res

        return False

    async def _dispatch_discard(
        self, engine: "CardGame", plan: TurnPlan, recorder: Optional[Any] = None
    ) -> None:
        """Concludes the bot's turn by executing 1 or 2 planned discards."""
        moves = plan.discard_moves
        if not moves:
            moves = [(plan.discard_card_idx, plan.discard_slot_idx)]

        hand = engine.get_player_hand(self.bot_id)
        if not hand:
            engine.next_turn()
            if recorder:
                recorder.record_step(engine, "end_turn", {"player_id": self.bot_id})
            next_player = engine.get_active_player()
            engine.start_turn_draw(next_player)
            return

        if len(moves) == 1:
            c_idx, s_idx = moves[0]
            c_idx = max(0, min(c_idx, len(hand) - 1))
            s_idx = max(0, min(s_idx, 3))
            card = hand[c_idx]

            if self.action_delay > 0:
                print(
                    f"[Server Bot '{self.bot_id}'] Discarded {card} "
                    f"to slot {s_idx} (Single Discard). Turn complete."
                )
            success = engine.discard_card(self.bot_id, c_idx, s_idx, end_turn=True)
            if success:
                if recorder:
                    recorder.record_step(engine, "discard_card", {
                        "card_index": c_idx,
                        "slot_index": s_idx,
                        "end_turn": True,
                        "player_id": self.bot_id,
                    })
                next_player = engine.get_active_player()
                engine.start_turn_draw(next_player)
        else:
            # 1st discard (flexible mode: +2s timer extension)
            c1_idx, s1_idx = moves[0]
            c1_idx = max(0, min(c1_idx, len(hand) - 1))
            s1_idx = max(0, min(s1_idx, 3))
            card1 = hand[c1_idx]

            if self.action_delay > 0:
                print(
                    f"[Server Bot '{self.bot_id}'] [Flexible 1/2] Discarded {card1} "
                    f"to slot {s1_idx} (+2s timer signal)."
                )
            engine.discard_card(self.bot_id, c1_idx, s1_idx, end_turn=False)
            if recorder:
                recorder.record_step(engine, "discard_card", {
                    "card_index": c1_idx,
                    "slot_index": s1_idx,
                    "end_turn": False,
                    "player_id": self.bot_id,
                })

            # Human-paced pause between discards
            await self._pause()

            # 2nd discard (ends turn)
            remaining_hand = engine.get_player_hand(self.bot_id)
            if not remaining_hand:
                engine.end_turn(self.bot_id)
                if recorder:
                    recorder.record_step(engine, "end_turn", {"player_id": self.bot_id})
                next_player = engine.get_active_player()
                engine.start_turn_draw(next_player)
                return

            c2_idx, s2_idx = moves[1]
            c2_idx = max(0, min(c2_idx, len(remaining_hand) - 1))
            s2_idx = max(0, min(s2_idx, 3))
            card2 = remaining_hand[c2_idx]

            if self.action_delay > 0:
                print(
                    f"[Server Bot '{self.bot_id}'] [Flexible 2/2] Discarded {card2} "
                    f"to slot {s2_idx}. Turn complete."
                )
            success = engine.discard_card(self.bot_id, c2_idx, s2_idx, end_turn=True)
            if success:
                if recorder:
                    recorder.record_step(engine, "discard_card", {
                        "card_index": c2_idx,
                        "slot_index": s2_idx,
                        "end_turn": True,
                        "player_id": self.bot_id,
                    })
                next_player = engine.get_active_player()
                engine.start_turn_draw(next_player)

    def _fallback_discard(self, engine: "CardGame", recorder: Optional[Any] = None) -> None:
        hand = engine.get_player_hand(self.bot_id)
        if not hand:
            engine.next_turn()
            if recorder:
                recorder.record_step(engine, "end_turn", {"player_id": self.bot_id})
            next_player = engine.get_active_player()
            engine.start_turn_draw(next_player)
            return
        if getattr(engine, "discards_this_turn", 0) > 0:
            engine.end_turn(self.bot_id)
            if recorder:
                recorder.record_step(engine, "end_turn", {"player_id": self.bot_id})
            next_player = engine.get_active_player()
            engine.start_turn_draw(next_player)
        else:
            success = engine.end_turn_with_discard(self.bot_id, 0, 0)
            if success:
                if recorder:
                    recorder.record_step(engine, "discard_card", {
                        "card_index": 0,
                        "slot_index": 0,
                        "end_turn": True,
                        "player_id": self.bot_id,
                    })
                next_player = engine.get_active_player()
                engine.start_turn_draw(next_player)
