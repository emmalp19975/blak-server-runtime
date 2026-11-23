from typing import List, Dict, Optional, Any
import random
import copy

class Signal:
    """A simple event system to replicate Godot's signals."""
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args, **kwargs):
        for callback in self._callbacks:
            callback(*args, **kwargs)

class CardGame:
    def __init__(self, seed: Optional[int] = None, verbose: bool = True) -> None:
        # --- SIGNALS ---
        self.state_updated = Signal()
        self.ladder_completed = Signal()
        self.game_started = Signal()
        self.game_over = Signal()

        # --- SEED & PRNG ---
        self.seed = None
        self.verbose = verbose
        self.rng = random.Random()
        if seed is not None:
            self.set_seed(seed)

        # --- DATA STRUCTURE ---
        self.main_deck = []
        self.players = {} # ID -> {hand, objective_deck, discard_pile}
        self.board = [
            {"value": 0, "is_omni_base": False, "cards": [], "pending_perk": False, "perk_owner": None},
            {"value": 0, "is_omni_base": False, "cards": [], "pending_perk": False, "perk_owner": None},
            {"value": 0, "is_omni_base": False, "cards": [], "pending_perk": False, "perk_owner": None},
            {"value": 0, "is_omni_base": False, "cards": [], "pending_perk": False, "perk_owner": None}
        ]

        # Turn Control
        self.player_order = []
        self.current_player_index = 0
        self.total_cards_played = 0
        self.discards_this_turn = 0



# --- INITIAL SETUP ---
    def set_seed(self, seed: int) -> None:
        """Sets a custom seed and initializes an isolated PRNG instance."""
        self.seed = seed
        self.rng = random.Random(seed)

    def get_seed(self) -> Optional[int]:
        """Returns the current game seed."""
        return self.seed

    def _log(self, msg: Any) -> None:
        if self.verbose:
            print(msg)

    def init_game(self, player_ids: List[str], seed: Optional[int] = None) -> None:
        if seed is not None:
            self.set_seed(seed)
        elif self.seed is None:
            # Generate a clean 6-digit random integer seed if none was set
            self.set_seed(random.randint(100000, 999999))

        self.player_order = player_ids
        self.current_player_index = 0
        self.create_full_deck()
        
        for p_id in player_ids:
            self.players[p_id] = {
                "objective_deck": [],
                "hand": [],
                "discard_piles": [[], [], [], []]
            }
            # Deal 15 cards to the objective deck
            for _ in range(15):
                card = self.main_deck.pop()
                card["drawn_by"] = p_id
                self.players[p_id]["objective_deck"].append(card)
        
        self.start_turn_draw(self.get_current_player_id())
        
        self._log(f"init_game called with seed={self.seed}...")
        self.game_started.emit()

    def create_full_deck(self) -> None:
        self.main_deck.clear()
        for _ in range(12):
            for v in range(1, 13):
                self.main_deck.append({"type": "number", "value": v})
            self.main_deck.append({"type": "joker"})
            self.main_deck.append({"type": "joker"})
        self.rng.shuffle(self.main_deck)



# --- TURN MANAGEMENT ---
    def get_current_player_id(self) -> str:
        return self.player_order[self.current_player_index]

    def next_turn(self) -> None:
        self.discards_this_turn = 0
        # Unredeemed ladder perks expire when turn ends: players cannot carry over or hijack perks
        for slot in self.board:
            slot["pending_perk"] = False
            slot["perk_owner"] = None

        # Advance index and wrap around using modulo
        self.current_player_index = (self.current_player_index + 1) % len(self.player_order)
        self.start_turn_draw(self.get_current_player_id())

    def start_turn_draw(self, player_id: str) -> bool:
        self.discards_this_turn = 0
        if self.get_current_player_id() != player_id:
            return False
        
        cards_needed = 5 - len(self.players[player_id]["hand"])
        for _ in range(cards_needed):
            if len(self.main_deck) > 0:
                card = self.main_deck.pop()
                card["drawn_by"] = player_id
                self.players[player_id]["hand"].append(card)
                
        self.state_updated.emit()
        return True

    def remove_player(self, player_id: str) -> None:
        if player_id not in self.players or player_id not in self.player_order:
            return

        # Recycle all cards back to main_deck
        p_data = self.players[player_id]
        cards_to_recycle = []
        cards_to_recycle.extend(p_data.get("hand", []))
        cards_to_recycle.extend(p_data.get("objective_deck", []))
        for pile in p_data.get("discard_piles", []):
            cards_to_recycle.extend(pile)
        if "discard_pile" in p_data:
            cards_to_recycle.extend(p_data["discard_pile"])

        self.main_deck.extend(cards_to_recycle)
        self.rng.shuffle(self.main_deck)

        was_current_player = (self.get_current_player_id() == player_id)
        current_idx = self.current_player_index
        target_idx = self.player_order.index(player_id)

        del self.players[player_id]
        self.player_order.remove(player_id)

        if not self.player_order:
            self.current_player_index = 0
            return

        if was_current_player:
            self.current_player_index = target_idx % len(self.player_order)
            next_player = self.get_current_player_id()
            self.start_turn_draw(next_player)
        else:
            if target_idx < current_idx:
                self.current_player_index -= 1
            self.current_player_index = self.current_player_index % len(self.player_order)

        self.state_updated.emit()

    def discard_card(self, player_id: str, hand_index: int, discard_slot_index: int = 0, end_turn: bool = True) -> bool:
        if self.get_current_player_id() != player_id:
            return False
        if self.discards_this_turn >= 2:
            return False
        if not end_turn and self.has_pending_perk(player_id):
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
                self.next_turn() # Conclude turn
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

# --- GAME SOURCES (Where the player pulls the card from) ---

    # 1. Play from Hand
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
            hand.pop(hand_index) # Remove from hand if legal
            
            if len(hand) == 0:
                self._log("¡Mano vacía! Robando 5 cartas nuevas...")
                self.start_turn_draw(player_id)
            else:
                self.state_updated.emit()
            return True
        return False

    # 2. Play from Objective Deck (Top visible card)
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
        
        card_obj = obj_deck[-1] # Equivalent to back()
        
        if self.play_card_to_slot(player_id, slot_index, card_obj):
            obj_deck.pop() # Reveal/remove from objective deck
            self.state_updated.emit()
            
            if len(obj_deck) == 0: # Victory condition
                self._log(f"¡EL JUGADOR {owner_id} HA GANADO LA PARTIDA!")
                self.game_over.emit(owner_id)
            return True
            
        return False

    # 3. Play from Discard Pile (Top card of chosen slot)
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
            pile.pop() # Remove top card
            self.state_updated.emit()
            return True
        return False




# --- CORE RULES (Board) ---
    def play_card_to_slot(self, player_id: str, slot_index: int, card_obj: Dict[str, Any]) -> bool:
        slot = self.board[slot_index]
        is_valid = False
        
        if slot["value"] == 0 and not slot["is_omni_base"]:
            if (card_obj["type"] == "number" and card_obj["value"] == 1) or card_obj["type"] == "joker":
                slot["value"] = 1
                is_valid = True
        elif slot["is_omni_base"]:
            if card_obj["type"] == "number":
                slot["value"] = card_obj["value"]
                slot["is_omni_base"] = False
                is_valid = True
            elif card_obj["type"] == "joker":
                is_valid = True
        else:
            if (card_obj["type"] == "number" and card_obj["value"] == slot["value"] + 1) or card_obj["type"] == "joker":
                if card_obj["type"] == "number":
                    slot["value"] = card_obj["value"]
                else:
                    slot["value"] += 1
                is_valid = True

        if is_valid:
            card_obj["played_by"] = player_id
            if "drawn_by" not in card_obj:
                card_obj["drawn_by"] = player_id
            slot["cards"].append(card_obj)
            slot["pending_perk"] = False
            slot["perk_owner"] = None
            self.total_cards_played += 1
            is_completed = self.check_ladder_completion(slot_index, player_id)
            if is_completed:
                self.ladder_completed.emit(player_id, slot_index)
            return True
        return False

    def check_ladder_completion(self, slot_index: int, player_id: Optional[str] = None) -> bool:
        slot = self.board[slot_index]
        if slot["value"] >= 12:
            for card in slot["cards"]:
                self.main_deck.append(card)
            self.rng.shuffle(self.main_deck)
            
            # Reset the slot
            slot["value"] = 0
            slot["is_omni_base"] = False
            slot["cards"] = []
            
            # Flag that this slot earned the perk and record ownership
            slot["pending_perk"] = True
            slot["perk_owner"] = player_id
            return True
        return False

    def initiate_ladder_perk(self, slot_index: int, player_id: Optional[str] = None) -> bool:
        if slot_index < 0 or slot_index >= len(self.board):
            return False
        slot = self.board[slot_index]
        
        # Only allow the perk if the flag is True and caller is the owner / active player
        if slot.get("pending_perk", False):
            perk_owner = slot.get("perk_owner")
            active_player = self.get_current_player_id()
            if player_id is not None and perk_owner is not None and player_id != perk_owner:
                return False
            if player_id is not None and player_id != active_player:
                return False
            if perk_owner is not None and perk_owner != active_player:
                return False

            if len(self.main_deck) > 0:
                drawn_card = self.main_deck.pop()
                drawn_card["drawn_by"] = player_id
                cartas_apartadas = []

                while drawn_card["type"] == "number" and drawn_card["value"] == 12:
                    cartas_apartadas.append(drawn_card)

                    if len(self.main_deck) == 0:
                        for carta in cartas_apartadas:
                            self.main_deck.append(carta)
                        return False
                        
                    drawn_card = self.main_deck.pop()
                    drawn_card["drawn_by"] = player_id

                self.main_deck.extend(cartas_apartadas)
                if len(cartas_apartadas) > 0:
                    self.rng.shuffle(self.main_deck)

                if drawn_card["type"] == "joker":
                    slot["is_omni_base"] = True
                    slot["cards"].append(drawn_card)
                else:
                    slot["value"] = drawn_card["value"]
                    slot["cards"].append(drawn_card)
                    
                # Turn off the perk flag once used
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




# --- GETTERS FOR AI AND NETWORK ---

    def get_player_hand(self, player_id: str) -> List[Dict[str, Any]]:
        if player_id in self.players:
            return copy.deepcopy(self.players[player_id]["hand"]) # Deep copy
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
        """Returns the top card of each non-empty discard slot for this player."""
        tops = []
        if player_id in self.players:
            for slot_idx, slot in enumerate(self.players[player_id].get("discard_piles", [])):
                if slot:
                    top_card = copy.deepcopy(slot[-1])
                    top_card["slot_index"] = slot_idx
                    tops.append(top_card)
        return tops

    def get_discard_pile(self, player_id: str) -> List[Dict[str, Any]]:
        """Legacy helper returning active top cards for backwards compatibility."""
        return self.get_discard_tops(player_id)

    def get_board_state(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.board) # Deep copy

    def get_active_player(self) -> str:
        return self.get_current_player_id()

    def get_player_list(self) -> List[str]:
        """Returns the list of player IDs in turn order."""
        return copy.deepcopy(self.player_order)

    def get_main_deck_size(self) -> int:
        """Returns how many cards are left in the central draw pile."""
        return len(self.main_deck)

    def get_sanitized_state_for_player(self, requestor_id: str) -> Dict[str, Any]:
        """Builds a complete snapshot of the game tailored for one specific client."""
        
        state = {
            "active_player": self.get_active_player(),
            "board": self.get_board_state(),
            "main_deck_size": self.get_main_deck_size(),
            "total_cards_played": self.total_cards_played,
            "discards_this_turn": self.discards_this_turn,
            "seed": self.seed,
            "players": {}
        }
        
        for p_id in self.player_order:
            player_data = {
                "objective_top_card": self.get_objective_top_card(p_id),
                "objective_remaining": self.get_objective_remaining(p_id),
                "discard_piles": self.get_discard_piles(p_id),
                "discard_pile": self.get_discard_tops(p_id),
                "hand_size": self.get_player_hand_size(p_id)
            }
            
            # Only include the actual hand data if the requestor is the owner
            if p_id == requestor_id:
                player_data["hand"] = self.get_player_hand(p_id)
                
            state["players"][p_id] = player_data
            
        return state