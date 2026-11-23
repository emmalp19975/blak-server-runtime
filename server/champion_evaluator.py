"""
server/champion_evaluator.py
============================
Automated Post-Match Tournament & Champion Evaluation Engine for Project Blak.

At the end of each match:
1. Evaluates human player profiles updated by the match.
2. Evaluates the collective Evolving Champion candidate (tuned by every move on the server).
3. Pits challengers against benchmark bots to calibrate leaderboard ratings.
4. Executes a 50-seed Title Fight against the Reigning Champion (alternating seats 25/25).
5. Regression Immunity: Requires strict majority (>= 26 / 50 wins) to dethrone the Reigning
   Champion and crown a new Titleholder.
"""

from __future__ import annotations
import copy
import json
import os
import random
from typing import Any, Dict, List, Optional, Tuple

from sandbox.arena import Arena
from sandbox.bot import SandboxBot
from sandbox.planner import DIFFICULTY_PROFILES, PlannerConfig
from server.logger import log_tournament
from server.mimic_tuner import get_profile_path, load_profile
from server.scoreboard import (
    CHAMPION_EVOLVING_PATH,
    get_champion_config,
    get_current_champion,
    get_evolving_champion_config,
    get_public_scoreboard,
    load_scoreboard,
    record_champion_defense,
    record_champion_evolution,
    record_duel_result,
    reset_evolving_candidate,
    update_champion,
)


def evaluate_challenger(
    challenger_name: str,
    challenger_cfg: PlannerConfig,
    challenger_type: str = "player",
    num_seeds: int = 50,
    source_player: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluates a candidate against the reigning champion in a 50-seed title series
    and against standard benchmark bots for leaderboard ranking.
    Attributes successful coronations to the player name mutation.
    """
    curr_champ = get_current_champion()
    champ_name = curr_champ.get("name", "Grandmaster")
    champ_type = curr_champ.get("type", "bot")
    champ_cfg = get_champion_config()

    print(f"[ChampionTournament] Starting evaluation for '{challenger_name}' ({challenger_type}) vs Champion '{champ_name}' across {num_seeds} seeds...")
    log_tournament(f"Starting evaluation for '{challenger_name}' ({challenger_type}) vs Champion '{champ_name}' across {num_seeds} seeds...")

    # Guard: If challenger is already reigning champion, skip self-play
    if challenger_name == champ_name:
        msg = f"Reigning Champion '{champ_name}' is already the titleholder. Skipping self-play."
        print(f"[ChampionTournament] {msg}")
        log_tournament(msg)
        return {
            "challenger": challenger_name,
            "champ_match_record": "0-0",
            "benchmark_record": "0-0",
            "total_wins": 0,
            "total_matches": 0,
            "win_rate": 0.0,
            "new_champion": False,
            "announcement": msg,
        }

    base_seed = random.randint(10000, 80000)

    # -------------------------------------------------------------------------
    # 1. Benchmark Gauntlet Duels (Calibrate Leaderboard Elo)
    # -------------------------------------------------------------------------
    benchmarks: List[Tuple[str, PlannerConfig]] = [
        ("Grandmaster", DIFFICULTY_PROFILES["Grandmaster"]),
        ("Tactical", DIFFICULTY_PROFILES["Tactical"]),
        ("Aggressive", DIFFICULTY_PROFILES["Aggressive"]),
        ("Casual", DIFFICULTY_PROFILES["Casual"]),
    ]
    # Filter out current champion if present in benchmarks to avoid self-play
    benchmarks = [(b_name, b_cfg) for b_name, b_cfg in benchmarks if b_name != champ_name and b_name != challenger_name]

    benchmark_wins = 0
    total_benchmark_matches = 0

    challenger_bot = SandboxBot(challenger_name, action_delay=0.0, config=challenger_cfg)

    for b_idx, (b_name, b_cfg) in enumerate(benchmarks):
        b_bot = SandboxBot(b_name, action_delay=0.0, config=b_cfg)
        # 2 games: 1 as Player 1, 1 as Player 2
        d1 = Arena.play_single_duel(challenger_bot, b_bot, seed=base_seed + 100 + b_idx * 2)
        d2 = Arena.play_single_duel(b_bot, challenger_bot, seed=base_seed + 101 + b_idx * 2)

        for d in (d1, d2):
            total_benchmark_matches += 1
            if d["winner"] == challenger_name:
                benchmark_wins += 1
                record_duel_result(challenger_name, b_name, challenger_type, "bot")
            elif d["winner"]:
                record_duel_result(b_name, challenger_name, "bot", challenger_type)

    # -------------------------------------------------------------------------
    # 2. 50-Seed Title Series vs Reigning Champion (Fair Alternating Seats)
    # -------------------------------------------------------------------------
    champ_bot_id = f"Champ_{champ_name}"
    champ_bot = SandboxBot(champ_bot_id, action_delay=0.0, config=champ_cfg)

    challenger_wins = 0
    champ_wins = 0
    draws = 0

    for i in range(num_seeds):
        seed = base_seed + i * 37
        # Alternate seating: challenger starts on even turns, champion on odd turns
        if i % 2 == 0:
            duel = Arena.play_single_duel(challenger_bot, champ_bot, seed=seed)
        else:
            duel = Arena.play_single_duel(champ_bot, challenger_bot, seed=seed)

        winner = duel.get("winner")
        if winner == challenger_bot.bot_id:
            challenger_wins += 1
        elif winner == champ_bot.bot_id:
            champ_wins += 1
        else:
            draws += 1

    # Strict majority condition to dethrone champion (e.g. >= 26 out of 50)
    win_threshold = (num_seeds // 2) + 1
    new_champion_crowned = False

    sb_now = load_scoreboard()
    if challenger_wins >= win_threshold:
        # Title change: Challenger won across 50 seeds
        player_attribution = source_player or challenger_name
        crowned_name = f"Champion ({player_attribution})" if challenger_name == "Champion_Evolving" else challenger_name

        if challenger_name == "Champion_Evolving":
            c_rating = record_champion_evolution(crowned_name, curr_champ.get("rating", 1600))
        else:
            record_duel_result(challenger_name, champ_name, challenger_type, champ_type)
            sb_now = load_scoreboard()
            c_entry = next((e for e in sb_now.get("leaderboard", []) if e.get("name") == challenger_name), None)
            c_rating = c_entry["rating"] if c_entry else 1600

        weights_dict = {
            "w_objective": challenger_cfg.w_objective,
            "w_hand_flush": challenger_cfg.w_hand_flush,
            "w_ladder_perk": challenger_cfg.w_ladder_perk,
            "w_anti_feed": challenger_cfg.w_anti_feed,
            "w_capping": challenger_cfg.w_capping,
            "w_discard_steal": challenger_cfg.w_discard_steal,
            "w_opp_obj_sacrifice": challenger_cfg.w_opp_obj_sacrifice,
            "w_endgame_threat_veto": challenger_cfg.w_endgame_threat_veto,
            "w_joker_conservation": challenger_cfg.w_joker_conservation,
            "w_omni_preservation": challenger_cfg.w_omni_preservation,
            "w_discard_unlock": challenger_cfg.w_discard_unlock,
            "w_hand_diversity": challenger_cfg.w_hand_diversity,
        }
        final_tag = update_champion(
            crowned_name,
            challenger_type,
            c_rating,
            weights_dict,
            source_player=player_attribution,
            is_coronation=True,
        )
        new_champion_crowned = True
        announcement = (
            f"*** NEW REIGNING CHAMPION! *** '{crowned_name}' [{final_tag}] "
            f"(Player Mutation of '{player_attribution}') defeated '{champ_name}' "
            f"({challenger_wins}-{champ_wins} across {num_seeds} seeds) to claim the crown!"
        )
        print(f"[ChampionTournament] {announcement}")
        log_tournament(announcement)
    else:
        # Title defense: Reigning Champion defends title and avoids tuning regression
        if challenger_name == "Champion_Evolving":
            defended_rating = record_champion_defense(champ_name, champ_type, curr_champ.get("rating", 1600))
            update_champion(champ_name, champ_type, defended_rating, is_coronation=False)
        else:
            record_duel_result(champ_name, challenger_name, champ_type, challenger_type)
            sb_now = load_scoreboard()
            ch_entry = next((e for e in sb_now.get("leaderboard", []) if e.get("name") == champ_name), None)
            champ_rating = ch_entry["rating"] if ch_entry else curr_champ.get("rating", 1600)
            update_champion(champ_name, champ_type, champ_rating, is_coronation=False)

        announcement = (
            f"Reigning Champion '{champ_name}' defended the title against '{challenger_name}' "
            f"({champ_wins}-{challenger_wins} across {num_seeds} seeds)."
        )
        print(f"[ChampionTournament] {announcement}")
        log_tournament(announcement)

    total_wins = challenger_wins + benchmark_wins
    total_matches = num_seeds + total_benchmark_matches
    win_rate = round((total_wins / total_matches) * 100.0, 1) if total_matches > 0 else 0.0

    return {
        "challenger": challenger_name,
        "champ_match_record": f"{challenger_wins}-{champ_wins}",
        "benchmark_record": f"{benchmark_wins}-{(total_benchmark_matches - benchmark_wins)}",
        "total_wins": total_wins,
        "total_matches": total_matches,
        "win_rate": win_rate,
        "new_champion": new_champion_crowned,
        "announcement": announcement,
    }


def run_post_match_tournament(gamertags: List[str], num_seeds: int = 50) -> Optional[Dict[str, Any]]:
    """
    Evaluates human player profiles that participated in a match along with
    the collective Evolving Champion candidate.
    """
    curr_champ = get_current_champion()
    champ_name = curr_champ.get("name", "Grandmaster")
    source_p = curr_champ.get("source_player", champ_name)

    challengers: List[Tuple[str, PlannerConfig, str]] = []

    # 1. Gather human player profiles updated by the match
    for tag in gamertags:
        if not tag or tag.startswith("Bot "):
            continue
        # Skip if human player is already the reigning champion (they don't challenge themselves)
        if tag == champ_name or tag == source_p:
            continue
        p_path = get_profile_path(tag)
        if os.path.isfile(p_path):
            challengers.append((tag, load_profile(tag), "player"))

    # 2. Check if collective Evolving Champion candidate has moves to test
    evolving_eligible = False
    if os.path.isfile(CHAMPION_EVOLVING_PATH):
        try:
            with open(CHAMPION_EVOLVING_PATH, "r", encoding="utf-8") as f:
                ev_data = json.load(f)
            moves_learned = int(ev_data.get("total_moves_learned", 0))
            if moves_learned > 0:
                evolving_eligible = True
        except Exception:
            pass

    if not challengers and not evolving_eligible:
        msg = "[ChampionTournament] No challengers or evolving moves to evaluate after match."
        print(msg)
        log_tournament(msg)
        return None

    results_summary: List[Dict[str, Any]] = []

    # Evaluate human challengers first
    for name, cfg, p_type in challengers:
        res = evaluate_challenger(name, cfg, p_type, num_seeds=num_seeds, source_player=name)
        results_summary.append(res)

    # Evaluate collective Evolving Champion candidate if eligible
    if os.path.isfile(CHAMPION_EVOLVING_PATH):
        try:
            with open(CHAMPION_EVOLVING_PATH, "r", encoding="utf-8") as f:
                ev_data = json.load(f)
            moves_learned = int(ev_data.get("total_moves_learned", 0))
            if moves_learned > 0:
                ev_cfg = get_evolving_champion_config()
                contributors = ev_data.get("contributors", [])
                cand_source = ev_data.get("last_contributor") or (contributors[-1] if contributors else "Player")
                
                # Autonomous Evolutionary Exploration: Screen mutation cohort to select strongest challenger
                best_cand_cfg = ev_cfg
                best_cand_name = "Champion_Evolving"
                try:
                    from sandbox.evolve_champion import mutate_candidate, clamp_config  # Deferred import: circular dependency
                    cohort: List[Tuple[str, PlannerConfig]] = [
                        ("Base", copy.deepcopy(ev_cfg)),
                        ("Velocity", clamp_config(mutate_candidate(ev_cfg, "velocity", 0.12, 1)[0])),
                        ("Tactical", clamp_config(mutate_candidate(ev_cfg, "tactical", 0.12, 2)[0])),
                        ("Hybrid", clamp_config(mutate_candidate(ev_cfg, "hybrid", 0.12, 3)[0])),
                    ]
                    gm_cfg = DIFFICULTY_PROFILES.get("Grandmaster", DIFFICULTY_PROFILES["Tactical"])
                    gm_bot = SandboxBot("Grandmaster", action_delay=0.0, config=gm_cfg)
                    best_score = -1
                    screen_seed = random.randint(10000, 70000)
                    for c_label, c_cfg in cohort:
                        c_bot = SandboxBot(f"Cand_{c_label}", action_delay=0.0, config=c_cfg)
                        w1 = 1 if Arena.play_single_duel(c_bot, gm_bot, seed=screen_seed).get("winner") == c_bot.bot_id else 0
                        w2 = 1 if Arena.play_single_duel(gm_bot, c_bot, seed=screen_seed + 1).get("winner") == c_bot.bot_id else 0
                        score = w1 + w2
                        if score > best_score:
                            best_score = score
                            best_cand_cfg = c_cfg
                            best_cand_name = f"Champion_Evolving ({c_label})"
                except Exception as m_err:
                    print(f"[ChampionTournament] Mutation cohort screening warning: {m_err}")

                start_msg = f"Evolving candidate '{best_cand_name}' ({moves_learned} moves from {contributors}) challenging Champion across {num_seeds} seeds..."
                print(f"[ChampionTournament] {start_msg}")
                log_tournament(start_msg)
                res = evaluate_challenger("Champion_Evolving", best_cand_cfg, "bot", num_seeds=num_seeds, source_player=cand_source)
                results_summary.append(res)
                # Reset candidate move counter after the title series completes
                reset_evolving_candidate()
        except Exception as exc:
            err_msg = f"Evolving candidate challenge error: {exc}"
            print(f"[ChampionTournament] {err_msg}")
            log_tournament(err_msg)

    return {
        "results": results_summary,
        "scoreboard": get_public_scoreboard(),
    }
