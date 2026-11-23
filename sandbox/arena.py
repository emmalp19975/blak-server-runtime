"""
sandbox/arena.py
================
Competitive Gating & Evaluation Arena for Project Blak.

Conducts rigorous head-to-head match series between neural policy candidates and
benchmark baselines (Grandmaster, previous generation Champion) to evaluate:
- Win rate percentage
- Average objectives cleared (out of 15)
- Average turns per match
- Model promotion validation (e.g. >= 55% win rate)

Alternates player seat order (first-player vs second-player) to completely
eliminate starting player bias.
"""

from __future__ import annotations
import copy
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

sys.path.insert(0, os.path.abspath("."))

from sandbox.engine import SandboxCardGame
from sandbox.bot import SandboxBot
from sandbox.policy_bot import PolicyBot
from sandbox.planner import DIFFICULTY_PROFILES


class Arena:
    """Evaluation harness for comparing two bot models or configurations."""

    @staticmethod
    def play_single_duel(
        bot_a: Union[PolicyBot, SandboxBot],
        bot_b: Union[PolicyBot, SandboxBot],
        seed: int,
        max_turns: int = 150,
    ) -> Dict[str, Any]:
        """Runs a single 1v1 match between two bot controllers."""
        engine = SandboxCardGame(seed=seed)
        p1_id = bot_a.bot_id
        p2_id = bot_b.bot_id
        players = [p1_id, p2_id]
        engine.init_game(players, seed=seed)

        bots = {p1_id: bot_a, p2_id: bot_b}

        turn_count = 0
        winner: Optional[str] = None

        def on_game_over(winner_id: str) -> None:
            nonlocal winner
            winner = winner_id

        engine.game_over.connect(on_game_over)

        t0 = time.perf_counter()
        while winner is None and turn_count < max_turns:
            active = engine.get_active_player()
            if not active or active not in bots:
                break

            turn_count += 1
            bots[active].take_turn_sync(engine)

            # Check if someone cleared their objective deck
            for p in players:
                if len(engine.players[p]["objective_deck"]) == 0:
                    winner = p
                    break

        elapsed = time.perf_counter() - t0

        obj_remaining_a = len(engine.players[p1_id]["objective_deck"])
        obj_remaining_b = len(engine.players[p2_id]["objective_deck"])
        obj_cleared_a = 15 - obj_remaining_a
        obj_cleared_b = 15 - obj_remaining_b

        return {
            "seed": seed,
            "winner": winner,
            "turns": turn_count,
            "elapsed_seconds": elapsed,
            "p1_id": p1_id,
            "p2_id": p2_id,
            "p1_cleared": obj_cleared_a,
            "p2_cleared": obj_cleared_b,
        }

    @classmethod
    def evaluate_bots(
        cls,
        candidate_bot: Union[PolicyBot, SandboxBot],
        opponent_bot: Union[PolicyBot, SandboxBot],
        num_matches: int = 20,
        start_seed: int = 10000,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """
        Runs an evaluation series of `num_matches` games.
        Alternates seat position to ensure fairness.
        """
        c_id = candidate_bot.bot_id
        o_id = opponent_bot.bot_id

        c_wins = 0
        o_wins = 0
        draws = 0
        total_turns = 0
        c_cleared_total = 0
        o_cleared_total = 0
        total_time = 0.0

        if verbose:
            print(f"\n{'=' * 65}")
            print(f"  ARENA DUEL BENCHMARK: {c_id} vs {o_id}")
            print(f"  Matches: {num_matches} | Fair Seat Alternation: Enabled")
            print(f"{'=' * 65}")

        for i in range(num_matches):
            seed = start_seed + i * 37

            # Alternate who is assigned to slot 0 (goes first)
            if i % 2 == 0:
                # Candidate goes first
                bot_1 = candidate_bot
                bot_2 = opponent_bot
            else:
                # Opponent goes first
                bot_1 = opponent_bot
                bot_2 = candidate_bot

            res = cls.play_single_duel(bot_1, bot_2, seed=seed)
            total_turns += res["turns"]
            total_time += res["elapsed_seconds"]

            # Map results back to candidate and opponent
            if res["p1_id"] == c_id:
                c_cleared = res["p1_cleared"]
                o_cleared = res["p2_cleared"]
            else:
                c_cleared = res["p2_cleared"]
                o_cleared = res["p1_cleared"]

            c_cleared_total += c_cleared
            o_cleared_total += o_cleared

            winner = res["winner"]
            if winner == c_id:
                c_wins += 1
                result_str = f"[WIN]  {c_id}"
            elif winner == o_id:
                o_wins += 1
                result_str = f"[LOSS] {o_id}"
            else:
                draws += 1
                result_str = "[DRAW]"

            if verbose:
                print(
                    f"  Match {i+1:02d}/{num_matches:02d} | "
                    f"Seed: {seed:<6} | {result_str:<15} | "
                    f"Objs: {c_cleared:02d}-{o_cleared:02d} | "
                    f"Turns: {res['turns']:02d} ({res['elapsed_seconds']:.2f}s)"
                )

        win_rate = (c_wins / num_matches) * 100.0 if num_matches > 0 else 0.0
        avg_c_cleared = c_cleared_total / num_matches if num_matches > 0 else 0.0
        avg_o_cleared = o_cleared_total / num_matches if num_matches > 0 else 0.0
        avg_turns = total_turns / num_matches if num_matches > 0 else 0.0

        stats: Dict[str, Any] = {
            "candidate_id": c_id,
            "opponent_id": o_id,
            "num_matches": num_matches,
            "candidate_wins": c_wins,
            "opponent_wins": o_wins,
            "draws": draws,
            "win_rate_pct": win_rate,
            "avg_candidate_cleared": avg_c_cleared,
            "avg_opponent_cleared": avg_o_cleared,
            "avg_turns_per_match": avg_turns,
            "total_benchmark_time": total_time,
            "avg_match_time": total_time / num_matches if num_matches > 0 else 0.0,
        }

        if verbose:
            print(f"{'-' * 65}")
            print(f"  FINAL BENCHMARK SCORE:")
            print(f"  {c_id} Record: {c_wins} Wins / {o_wins} Losses / {draws} Draws")
            print(f"  Win Rate:     {win_rate:.1f}%")
            print(f"  Avg Objs:     {avg_c_cleared:.1f}/15 vs {avg_o_cleared:.1f}/15")
            print(f"  Avg Turns:    {avg_turns:.1f} per match ({stats['avg_match_time']:.2f}s/match)")
            print(f"{'=' * 65}\n")

        return stats


def benchmark_against_grandmaster(
    bot_or_model: Union[PolicyBot, str],
    num_matches: int = 20,
    start_seed: int = 5000,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Convenience helper to benchmark any policy bot directly against Grandmaster."""
    if isinstance(bot_or_model, str):
        candidate = PolicyBot("CandidateBot", model_or_path=bot_or_model)
    else:
        candidate = bot_or_model

    grandmaster = SandboxBot(
        "Grandmaster",
        action_delay=0.0,
        config=DIFFICULTY_PROFILES["Grandmaster"],
    )

    return Arena.evaluate_bots(
        candidate_bot=candidate,
        opponent_bot=grandmaster,
        num_matches=num_matches,
        start_seed=start_seed,
        verbose=verbose,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run Arena Duel Benchmark")
    parser.add_argument("--model", type=str, default="data/models/policy_net.pt", help="Path to policy model")
    parser.add_argument("--matches", type=int, default=10, help="Number of benchmark matches")
    parser.add_argument("--seed", type=int, default=42, help="Starting random seed")
    args = parser.parse_args()

    benchmark_against_grandmaster(args.model, num_matches=args.matches, start_seed=args.seed)
