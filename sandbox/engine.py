"""
sandbox/engine.py
=================
Isolated copy of the GameEngine for "The Sandbox" parallel test build.
Maintains 100% rule parity with production while providing full arbitrary state
injection capabilities for automated and manual scenario testing.
"""

from __future__ import annotations
import copy
import random
from typing import Any, Callable, Dict, List, Optional


class Signal:
    """Simple callback-based signal system replicating Godot signals in Python."""

    def __init__(self) -> None:
        self._callbacks: List[Callable[..., Any]] = []

    def connect(self, callback: Callable[..., Any]) -> None:
        self._callbacks.append(callback)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        for callback in self._callbacks:
            callback(*args, **kwargs)


class SandboxCardGame:
    """Sandbox Card Game Engine with State Injection support."""

    def __init__(self, seed: Optional[Any] = None) -> None:
        # Signals
        self.state_updated = Signal()
        self.ladder_completed = Signal()
        self.game_started = Signal()
        self.game_over = Signal()

        # Seed & PRNG
        self.seed: Optional[Any] = None
        self.rng = random.Random()
        if seed is not None:
            self.set_seed(seed)

        # State structures
        self.main_deck: List[Dict[str, Any]] = []
        self.players: Dict[str, Dict[str, Any]] = {}
        self.board: List[Dict[str, Any]] = [
            {"value": 0, "is_omni_base": False, "cards": [], "pending_perk": False, "perk_owner": None},
            {"value": 0, "is_omni_base": False, "cards": [], "pending_perk": False, "perk_owner": None},
            {"value": 0, "is_omni_base": False, "cards": [], "pending_perk": False, "perk_owner": None},
            {"value": 0, "is_omni_base": False, "cards": [], "pending_perk": False, "perk_owner": None},
        ]
        self.player_order: List[str] = []
        self.current_player_index: int = 0
        self.total_cards_played: int = 0
        self.discards_this_turn: int = 0

    # -------------------------------------------------------------------------
    # Standard Setup
    # -------------------------------------------------------------------------
    def set_seed(self, seed: Any) -> None:
        """Sets a custom seed and initializes an isolated PRNG instance."""
        self.seed = seed
        self.rng = random.Random(seed)

    def get_seed(self) -> Optional[Any]:
        """Returns the current game seed."""
        return self.seed

    def init_game(self, player_ids: List[str], seed: Optional[Any] = None) -> None:
        """Standard production-style initialization with optional seed."""
        if seed is not None:
            self.set_seed(seed)
        elif self.seed is None:
            self.set_seed(random.randint(100000, 999999))

        self.player_order = list(player_ids)
        self.current_player_index = 0
        self.create_full_deck()

        for p_id in player_ids:
            self.players[p_id] = {
                "objective_deck": [],
                "hand": [],
                "discard_piles": [[], [], [], []],
            }
            for _ in range(15):
                if self.main_deck:
                    card = self.main_deck.pop()
                    card["drawn_by"] = p_id
                    self.players[p_id]["objective_deck"].append(card)

        if self.player_order:
            self.start_turn_draw(self.get_current_player_id())

        self.game_started.emit()
        self.state_updated.emit()

    def create_full_deck(self) -> None:
        self.main_deck.clear()
        for _ in range(12):
            for v in range(1, 13):
                self.main_deck.append({"type": "number", "value": v})
            self.main_deck.append({"type": "joker"})
            self.main_deck.append({"type": "joker"})
        self.rng.shuffle(self.main_deck)

    # -------------------------------------------------------------------------
    # STATE INJECTION (The Sandbox Core Feature)
    # -------------------------------------------------------------------------
    def inject_state(self, state: Dict[str, Any]) -> None:
        """
        Injects an arbitrary game state directly into the engine.
        Accepts board states, hands, 4-slot discard piles, objective decks,
        active player turn, and main deck count.
        """
        # 1. Reset state
        if "seed" in state and state["seed"] is not None:
            self.set_seed(state["seed"])
        self.main_deck.clear()
        self.players.clear()
        self.player_order.clear()
        self.total_cards_played = int(state.get("total_cards_played", 0))

        if state.get("fresh_game"):
            order = state.get("player_order")
            if not order and isinstance(state.get("players"), dict):
                order = [str(k) for k in state["players"].keys()]
            if not order:
                order = ["Tester", "Bot1", "Bot2"]
            self.init_game(order, seed=state.get("seed"))
            return

        # 2. Ingest Board
        raw_board = state.get("board", [])
        self.board = []
        for i in range(4):
            if i < len(raw_board) and isinstance(raw_board[i], dict):
                slot_data = raw_board[i]
                cards = list(slot_data.get("cards", []))
                val = int(slot_data.get("value", 0))
                # Auto-populate card in cards list if cards is empty but value > 0 or omni_base
                if val > 0 and not cards:
                    cards.append({"type": "number", "value": val})
                elif bool(slot_data.get("is_omni_base", False)) and not cards:
                    cards.append({"type": "joker"})
                self.board.append({
                    "value": val,
                    "is_omni_base": bool(slot_data.get("is_omni_base", False)),
                    "cards": cards,
                    "pending_perk": bool(slot_data.get("pending_perk", False)),
                    "perk_owner": slot_data.get("perk_owner"),
                })
            else:
                self.board.append({"value": 0, "is_omni_base": False, "cards": [], "pending_perk": False, "perk_owner": None})

        # 3. Ingest Players
        raw_players = state.get("players", {})
        if isinstance(raw_players, dict):
            for p_id, p_info in raw_players.items():
                p_id_str = str(p_id)
                self.player_order.append(p_id_str)

                # Discard piles: ensure exactly 4 slots
                raw_discard_piles = p_info.get("discard_piles", [[], [], [], []])
                clean_discard_piles: List[List[Dict[str, Any]]] = []
                for s in range(4):
                    if s < len(raw_discard_piles) and isinstance(raw_discard_piles[s], list):
                        clean_discard_piles.append(list(raw_discard_piles[s]))
                    else:
                        clean_discard_piles.append([])

                # Objective deck
                obj_deck: List[Dict[str, Any]] = []
                if "objective_deck" in p_info and isinstance(p_info["objective_deck"], list):
                    obj_deck = list(p_info["objective_deck"])
                else:
                    # Synthesize from top card + remaining count
                    remaining = int(p_info.get("objective_remaining", 1))
                    top_card = p_info.get("objective_top_card", {"type": "number", "value": 1})
                    for _ in range(max(0, remaining - 1)):
                        obj_deck.append({"type": "number", "value": 5})
                    if remaining > 0:
                        obj_deck.append(top_card)

                # Hand
                raw_hand = p_info.get("hand", [])
                hand_list: List[Dict[str, Any]] = []
                if not raw_hand and "hand_size" in p_info:
                    for _ in range(int(p_info["hand_size"])):
                        hand_list.append({"type": "number", "value": self.rng.randint(1, 12)})
                else:
                    for h in raw_hand:
                        if isinstance(h, dict):
                            hand_list.append(dict(h))
                        elif isinstance(h, (int, str)):
                            if str(h).upper() == "J":
                                hand_list.append({"type": "joker"})
                            else:
                                hand_list.append({"type": "number", "value": int(h)})

                self.players[p_id_str] = {
                    "objective_deck": obj_deck,
                    "hand": hand_list,
                    "discard_piles": clean_discard_piles,
                }

        # Ensure at least 2 players exist for a full playable match
        if len(self.player_order) < 2:
            bot_id = "Opponent1"
            if bot_id not in self.players:
                self.player_order.append(bot_id)
                self.players[bot_id] = {
                    "objective_deck": [{"type": "number", "value": self.rng.randint(1, 12)} for _ in range(5)],
                    "hand": [{"type": "number", "value": self.rng.randint(1, 12)} for _ in range(5)],
                    "discard_piles": [[], [], [], []],
                }

        # 4. Ingest Player Order & Active Player
        custom_order = state.get("player_order")
        if isinstance(custom_order, list) and custom_order:
            self.player_order = [str(p) for p in custom_order if str(p) in self.players]

        active_player = str(state.get("active_player", ""))
        if active_player in self.player_order:
            self.current_player_index = self.player_order.index(active_player)
        else:
            self.current_player_index = 0

        # 5. Ingest Main Deck
        if "main_deck" in state and isinstance(state["main_deck"], list):
            self.main_deck = [dict(c) for c in state["main_deck"]]
        else:
            deck_size = max(60, int(state.get("main_deck_size", 60)))
            for _ in range(deck_size):
                self.main_deck.append({"type": "number", "value": self.rng.randint(1, 12)})
            for _ in range(8):
                self.main_deck.append({"type": "joker"})
            self.rng.shuffle(self.main_deck)

        self.game_started.emit()
        self.state_updated.emit()

    def _replenish_main_deck(self) -> None:
        """Refills main deck with fresh cards if depleted during long testing sessions."""
        for _ in range(4):
            for v in range(1, 13):
                self.main_deck.append({"type": "number", "value": v})
            self.main_deck.append({"type": "joker"})
        self.rng.shuffle(self.main_deck)

    # -------------------------------------------------------------------------
    # Turn Management
    # -------------------------------------------------------------------------
    def get_current_player_id(self) -> str:
        if not self.player_order:
            return ""
        return self.player_order[self.current_player_index]

    def next_turn(self) -> None:
        self.discards_this_turn = 0
        # Unredeemed ladder perks expire when turn ends: players cannot carry over or hijack perks
        for slot in self.board:
            slot["pending_perk"] = False
            slot["perk_owner"] = None

        if self.player_order:
            self.current_player_index = (self.current_player_index + 1) % len(self.player_order)
            next_player = self.get_current_player_id()
            self.start_turn_draw(next_player)

    def start_turn_draw(self, player_id: str, force: bool = False) -> bool:
        self.discards_this_turn = 0
        if self.get_current_player_id() != player_id:
            return False

        if player_id not in self.players:
            return False

        hand = self.players[player_id]["hand"]
        cards_needed = max(0, 5 - len(hand))
        for _ in range(cards_needed):
            if len(self.main_deck) == 0:
                self._replenish_main_deck()
            if len(self.main_deck) > 0:
                card = self.main_deck.pop()
                card["drawn_by"] = player_id
                hand.append(card)

        self.state_updated.emit()
        return True

    def discard_card(self, player_id: str, hand_index: int, discard_slot_index: int = 0, end_turn: bool = True) -> bool:
        if self.get_current_player_id() != player_id:
            return False
        if self.discards_this_turn >= 2:
            return False
        if not end_turn and self.has_pending_perk(player_id):
            return False

        if player_id not in self.players:
            return False

        hand = self.players[player_id]["hand"]
        discard_piles = self.players[player_id]["discard_piles"]

        if discard_slot_index < 0 or discard_slot_index >= len(discard_piles):
            return False

        if 0 <= hand_index < len(hand):
            discarded_card = hand.pop(hand_index)
            if "drawn_by" not in discarded_card:
                discarded_card["drawn_by"] = player_id
            discard_piles[discard_slot_index].append(discarded_card)
            self.discards_this_turn += 1
            if not end_turn and self.discards_this_turn < 2:
                # First discard in flexible mode: gives +2s to timer just like playing a card!
                self.total_cards_played += 1
                self.state_updated.emit()
            else:
                self.next_turn()
            return True

        return False

    def end_turn_with_discard(self, player_id: str, hand_index: int, discard_slot_index: int = 0) -> bool:
        return self.discard_card(player_id, hand_index, discard_slot_index, end_turn=True)

    def end_turn(self, player_id: str) -> bool:
        if self.get_current_player_id() != player_id:
            return False
        self.next_turn()
        return True

    def has_pending_perk(self, player_id: Optional[str] = None) -> bool:
        """Returns True if any board slot has an unredeemed ladder perk owned by the target player."""
        target = player_id if player_id is not None else self.get_current_player_id()
        for slot in self.board:
            if slot.get("pending_perk", False) and (slot.get("perk_owner") is None or slot.get("perk_owner") == target):
                return True
        return False

    # -------------------------------------------------------------------------
    # Card Plays
    # -------------------------------------------------------------------------
    def play_from_hand(self, player_id: str, hand_index: int, slot_index: int) -> bool:
        if self.get_current_player_id() != player_id:
            return False
        if self.discards_this_turn > 0:
            return False
        if self.has_pending_perk(player_id):
            return False

        hand = self.players[player_id]["hand"]
        if hand_index < 0 or hand_index >= len(hand):
            return False

        card_obj = hand[hand_index]
        if self.play_card_to_slot(player_id, slot_index, card_obj):
            hand.pop(hand_index)
            if len(hand) == 0:
                self.start_turn_draw(player_id, force=True)
            else:
                self.state_updated.emit()
            return True
        return False

    def play_from_objective(self, player_id: str, owner_id: str, slot_index: int) -> bool:
        if self.get_current_player_id() != player_id:
            return False
        if self.discards_this_turn > 0:
            return False
        if self.has_pending_perk(player_id):
            return False

        if owner_id not in self.players:
            return False

        obj_deck = self.players[owner_id]["objective_deck"]
        if len(obj_deck) == 0:
            return False

        card_obj = obj_deck[-1]
        if self.play_card_to_slot(player_id, slot_index, card_obj):
            obj_deck.pop()
            self.state_updated.emit()

            if len(obj_deck) == 0:
                self.game_over.emit(owner_id)
            return True
        return False

    def play_from_discard(self, player_id: str, owner_id: str, discard_slot_index: int, slot_index: int) -> bool:
        if self.get_current_player_id() != player_id:
            return False
        if self.discards_this_turn > 0:
            return False
        if self.has_pending_perk(player_id):
            return False

        if owner_id not in self.players:
            return False

        discard_piles = self.players[owner_id]["discard_piles"]
        if discard_slot_index < 0 or discard_slot_index >= len(discard_piles):
            return False

        pile = discard_piles[discard_slot_index]
        if len(pile) == 0:
            return False

        card_obj = pile[-1]
        if self.play_card_to_slot(player_id, slot_index, card_obj):
            pile.pop()
            self.state_updated.emit()
            return True
        return False

    # -------------------------------------------------------------------------
    # Core Board Validation & Ladder Perk
    # -------------------------------------------------------------------------
    def can_play_card_to_slot(self, slot_index: int, card_obj: Dict[str, Any]) -> bool:
        """Checks if a card can legally be played to a board slot without mutating state."""
        if slot_index < 0 or slot_index >= len(self.board):
            return False

        slot = self.board[slot_index]

        card_type = card_obj.get("type", "number")
        card_val = card_obj.get("value", 0)

        if slot["value"] == 0 and not slot["is_omni_base"]:
            return (card_type == "number" and card_val == 1) or card_type == "joker"
        elif slot["is_omni_base"]:
            return True
        else:
            return (card_type == "number" and card_val == slot["value"] + 1) or card_type == "joker"

    def play_card_to_slot(self, player_id: str, slot_index: int, card_obj: Dict[str, Any]) -> bool:
        if not self.can_play_card_to_slot(slot_index, card_obj):
            return False

        slot = self.board[slot_index]
        slot["pending_perk"] = False  # Consumed/cleared when a new card starts or builds on this slot
        slot["perk_owner"] = None
        card_type = card_obj.get("type", "number")
        card_val = card_obj.get("value", 0)

        if slot["value"] == 0 and not slot["is_omni_base"]:
            slot["value"] = 1
        elif slot["is_omni_base"]:
            if card_type == "number":
                slot["value"] = card_val
                slot["is_omni_base"] = False
            elif card_type == "joker":
                pass
        else:
            if card_type == "number":
                slot["value"] = card_val
            else:
                slot["value"] += 1

        card_obj["played_by"] = player_id
        if "drawn_by" not in card_obj:
            card_obj["drawn_by"] = player_id
        slot["cards"].append(card_obj)
        self.total_cards_played += 1
        if slot["value"] >= 12:
            self.handle_ladder_completion(slot_index, player_id)
        return True

    def handle_ladder_completion(self, slot_index: int, player_id: str) -> None:
        slot = self.board[slot_index]
        self.main_deck.extend(slot["cards"])
        self.rng.shuffle(self.main_deck)

        slot["cards"].clear()
        slot["value"] = 0
        slot["is_omni_base"] = False
        slot["pending_perk"] = True
        slot["perk_owner"] = player_id

        self.ladder_completed.emit(player_id, slot_index)

    def initiate_ladder_perk(self, slot_index: int, player_id: Optional[str] = None) -> bool:
        if 0 <= slot_index < len(self.board):
            slot = self.board[slot_index]
            if slot.get("pending_perk", False):
                perk_owner = slot.get("perk_owner")
                active_player = self.get_current_player_id()
                if player_id is not None and perk_owner is not None and player_id != perk_owner:
                    return False
                if player_id is not None and player_id != active_player:
                    return False
                if perk_owner is not None and perk_owner != active_player:
                    return False

                if len(self.main_deck) == 0:
                    self._replenish_main_deck()

                drawn_card = self.main_deck.pop()
                drawn_card["drawn_by"] = player_id
                cartas_apartadas = []

                while drawn_card.get("type") == "number" and drawn_card.get("value") == 12:
                    cartas_apartadas.append(drawn_card)
                    if len(self.main_deck) == 0:
                        self._replenish_main_deck()
                    drawn_card = self.main_deck.pop()
                    drawn_card["drawn_by"] = player_id

                self.main_deck.extend(cartas_apartadas)
                if cartas_apartadas:
                    self.rng.shuffle(self.main_deck)

                if drawn_card.get("type") == "joker":
                    slot["value"] = 0
                    slot["is_omni_base"] = True
                    slot["cards"].append(drawn_card)
                else:
                    slot["value"] = drawn_card.get("value", 0)
                    slot["cards"].append(drawn_card)

                slot["pending_perk"] = False
                slot["perk_owner"] = None
                self.state_updated.emit()
                return True
        return False

    def decline_ladder_perk(self, slot_index: int, player_id: Optional[str] = None) -> bool:
        """Declines the ladder perk, leaving the slot at value 0 and removing the perk flag."""
        if 0 <= slot_index < len(self.board):
            slot = self.board[slot_index]
            if slot.get("pending_perk", False):
                perk_owner = slot.get("perk_owner")
                active_player = self.get_current_player_id()
                if player_id is not None and perk_owner is not None and player_id != perk_owner:
                    return False
                if player_id is not None and player_id != active_player:
                    return False

                slot["pending_perk"] = False
                slot["perk_owner"] = None
                self.state_updated.emit()
                return True
        return False

    # -------------------------------------------------------------------------
    # Getters & Sanitized State Export
    # -------------------------------------------------------------------------
    def get_player_hand(self, player_id: str) -> List[Dict[str, Any]]:
        if player_id in self.players:
            return copy.deepcopy(self.players[player_id]["hand"])
        return []

    def get_player_hand_size(self, player_id: str) -> int:
        if player_id in self.players:
            return len(self.players[player_id]["hand"])
        return 0

    def get_objective_top_card(self, player_id: str) -> Dict[str, Any]:
        if player_id in self.players and len(self.players[player_id]["objective_deck"]) > 0:
            return copy.deepcopy(self.players[player_id]["objective_deck"][-1])
        return {}

    def get_objective_remaining(self, player_id: str) -> int:
        if player_id in self.players:
            return len(self.players[player_id]["objective_deck"])
        return 0

    def get_discard_piles(self, player_id: str) -> List[List[Dict[str, Any]]]:
        if player_id in self.players:
            return copy.deepcopy(self.players[player_id].get("discard_piles", [[], [], [], []]))
        return [[], [], [], []]

    def get_discard_tops(self, player_id: str) -> List[Dict[str, Any]]:
        tops: List[Dict[str, Any]] = []
        if player_id in self.players:
            for slot_idx, slot in enumerate(self.players[player_id].get("discard_piles", [])):
                if slot:
                    top_card = copy.deepcopy(slot[-1])
                    top_card["slot_index"] = slot_idx
                    tops.append(top_card)
        return tops

    def get_board_state(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.board)

    def get_active_player(self) -> str:
        return self.get_current_player_id()

    def get_player_list(self) -> List[str]:
        return list(self.player_order)

    def get_main_deck_size(self) -> int:
        return len(self.main_deck)

    def get_sanitized_state_for_player(self, requestor_id: str) -> Dict[str, Any]:
        state: Dict[str, Any] = {
            "active_player": self.get_active_player(),
            "board": self.get_board_state(),
            "main_deck_size": self.get_main_deck_size(),
            "total_cards_played": self.total_cards_played,
            "discards_this_turn": self.discards_this_turn,
            "seed": self.seed,
            "players": {},
        }

        for p_id in self.player_order:
            player_data: Dict[str, Any] = {
                "objective_top_card": self.get_objective_top_card(p_id),
                "objective_remaining": self.get_objective_remaining(p_id),
                "discard_piles": self.get_discard_piles(p_id),
                "discard_pile": self.get_discard_tops(p_id),
                "hand_size": self.get_player_hand_size(p_id),
            }

            if p_id == requestor_id:
                player_data["hand"] = self.get_player_hand(p_id)

            state["players"][p_id] = player_data

        return state
