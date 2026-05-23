import os
import sys
import uuid
import re
import argparse
from datetime import datetime
from feedback_loop.logger import init_db, log_prediction, get_pending_predictions, get_all_settled_predictions
from data_pipeline.entity_resolution import EntityResolver
from calculation_engine.probability import ProbabilityEngine
from calculation_engine.kelly import KellyEngine
from feedback_loop.evaluator import ModelEvaluator
from feedback_loop.json_logger import JsonDocumentLogger

def print_banner():
    print("=" * 70)
    print("      Tactical Manager Sports Betting System (EV & Kelly Criterion)      ")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Tactical Manager Sports Betting System CLI")
    subparsers = parser.add_subparsers(dest="command", help="System command to run")

    # init database
    subparsers.add_parser("init", help="Initialize the SQLite database schema")

    # predict command
    pred_parser = subparsers.add_parser("predict", help="Generate probabilities and calculate Kelly bets")
    pred_parser.add_argument("--home", required=True, help="Home team name")
    pred_parser.add_argument("--away", required=True, help="Away team name")
    pred_parser.add_argument("--home-mgr", required=True, help="Home manager name")
    pred_parser.add_argument("--away-mgr", required=True, help="Away manager name")
    pred_parser.add_argument("--home-form", required=True, help="Home predicted formation (e.g., 4-3-3)")
    pred_parser.add_argument("--away-form", required=True, help="Away predicted formation (e.g., 4-2-3-1)")
    pred_parser.add_argument("--odds-home", type=float, help="Decimal odds for Home Win")
    pred_parser.add_argument("--odds-draw", type=float, help="Decimal odds for Draw")
    pred_parser.add_argument("--odds-away", type=float, help="Decimal odds for Away Win")
    pred_parser.add_argument("--place-bet", choices=["home", "draw", "away", "none"], default="none", help="Register a bet on this outcome")
    pred_parser.add_argument("--bankroll", type=float, default=1000.0, help="Current bankroll for Kelly calculation")
    
    # JSON Logger extensions
    pred_parser.add_argument("--match-id", help="Override auto-generated match ID (e.g., epl_20260524_cry_ars)")
    pred_parser.add_argument("--competition", default="Premier League", help="Competition name")
    pred_parser.add_argument("--date", help="Match kickoff ISO datetime (default current UTC time)")
    pred_parser.add_argument("--bookmaker", default="Pinnacle", help="Bookmaker name")
    pred_parser.add_argument("--manager-h2h-win-rate-home", type=float, default=0.0, help="Manager head-to-head win rate for home manager")
    pred_parser.add_argument("--tactical-flexibility-index-home", type=float, default=0.5, help="Tactical flexibility index for home manager")
    pred_parser.add_argument("--tactical-flexibility-index-away", type=float, default=0.5, help="Tactical flexibility index for away manager")
    pred_parser.add_argument("--reasoning", help="Custom text reasoning override")

    # settle command
    settle_parser = subparsers.add_parser("settle", help="Settle an existing pending bet with actual score")
    settle_parser.add_argument("--id", required=True, help="Prediction/Match ID to settle")
    settle_parser.add_argument("--home-score", type=int, required=True, help="Home team final goals")
    settle_parser.add_argument("--away-score", type=int, required=True, help="Away team final goals")
    settle_parser.add_argument("--home-form", required=True, help="Home team actual formation")
    settle_parser.add_argument("--away-form", required=True, help="Away team actual formation")
    settle_parser.add_argument("--notes", default="", help="Optional feedback/context notes")

    # audit command
    subparsers.add_parser("audit", help="Audit model performance: ROI, Brier Score, and calibration metrics")

    # calibrate command
    cal_parser = subparsers.add_parser("calibrate", help="Calibrate model weights")
    cal_parser.add_argument("--base", type=float, required=True, help="Weight of baseline team odds (0.0 to 1.0)")
    cal_parser.add_argument("--mgr", type=float, required=True, help="Weight of manager PPG form (0.0 to 1.0)")
    cal_parser.add_argument("--tactics", type=float, required=True, help="Weight of tactical formation matchups (0.0 to 1.0)")

    # learn command
    learn_parser = subparsers.add_parser("learn", help="Ingest domestic match results to adjust ELO ratings and xG weights")
    learn_parser.add_argument("--results", nargs="+", required=True, help="Paths to JSON result files")
    learn_parser.add_argument("--lr", type=float, default=0.05, help="Learning rate for xG weights adjustment")
    learn_parser.add_argument("--k", type=float, default=32.0, help="ELO rating K-factor")

    # demo command
    subparsers.add_parser("demo", help="Run an end-to-end tactical matching and betting simulation")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    db_path = "data/tactics_betting.db"
    settings_path = "config/settings.json"
    aliases_path = "config/aliases.json"

    # Auto-initialize database on any command if it does not exist
    if not os.path.exists(db_path) and args.command != "init":
        print("[INFO] Database not found. Initializing database...")
        init_db(db_path)

    if args.command == "init":
        init_db(db_path)
        print(f"[SUCCESS] Database successfully initialized at {db_path}")

    elif args.command == "predict":
        # Resolve entities
        resolver = EntityResolver(aliases_path)
        home_team = resolver.resolve_team(args.home)
        away_team = resolver.resolve_team(args.away)
        home_mgr = resolver.resolve_manager(args.home_mgr)
        away_mgr = resolver.resolve_manager(args.away_mgr)

        print_banner()
        print(f"Resolving Entities:")
        print(f"  Home Team: '{args.home}' -> '{home_team}'")
        print(f"  Away Team: '{args.away}' -> '{away_team}'")
        print(f"  Home Manager: '{args.home_mgr}' -> '{home_mgr}'")
        print(f"  Away Manager: '{args.away_mgr}' -> '{away_mgr}'")
        print("-" * 70)

        # Generate Match ID if not provided
        if not args.match_id:
            comp_clean = "".join([c for c in args.competition if c.isalnum()]).lower()
            if comp_clean == "premierleague":
                comp_pref = "epl"
            elif comp_clean == "championsleague":
                comp_pref = "ucl"
            else:
                comp_pref = comp_clean[:3]
                
            match_date_str = args.date or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            date_clean = re.sub(r'[^0-9]', '', match_date_str)[:8]
            if not date_clean:
                date_clean = datetime.utcnow().strftime("%Y%m%d")
                
            home_pref = "".join([c for c in home_team if c.isalnum()]).lower()[:3]
            away_pref = "".join([c for c in away_team if c.isalnum()]).lower()[:3]
            match_id = f"{comp_pref}_{date_clean}_{home_pref}_{away_pref}"
        else:
            match_id = args.match_id

        # Run Probability Engine
        prob_engine = ProbabilityEngine(settings_path)
        probs_result = prob_engine.calculate_match_probabilities(
            home_team, away_team, home_mgr, away_mgr, args.home_form, args.away_form,
            bookmaker_odds={"home": args.odds_home, "draw": args.odds_draw, "away": args.odds_away} if args.odds_home else None
        )

        probs = probs_result["probabilities"]
        print(f"Model Probabilities:")
        print(f"  Home Win: {probs['home']*100:.2f}% | Draw: {probs['draw']*100:.2f}% | Away Win: {probs['away']*100:.2f}%")
        print(f"Tactical Modifiers:")
        print(f"  Home Manager: {probs_result['manager_stats']['home']['name']} (PPG: {probs_result['manager_stats']['home']['points_per_game']})")
        print(f"  Away Manager: {probs_result['manager_stats']['away']['name']} (PPG: {probs_result['manager_stats']['away']['points_per_game']})")
        print(f"  Tactical Advantage Multiplier -> Home: {probs_result['tactical_multipliers']['home']} | Away: {probs_result['tactical_multipliers']['away']}")
        print("-" * 70)

        # Kelly Engine Calculations
        kelly_engine = KellyEngine(settings_path)
        book_odds = {"home": args.odds_home, "draw": args.odds_draw, "away": args.odds_away} if args.odds_home else {}
        
        # Log prediction data structure
        pred_data = {
            "id": match_id,
            "match_date": args.date or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "home_team": home_team,
            "away_team": away_team,
            "home_manager": home_mgr,
            "away_manager": away_mgr,
            "home_predicted_formation": args.home_form,
            "away_predicted_formation": args.away_form,
            "model_p_home": probs["home"],
            "model_p_draw": probs["draw"],
            "model_p_away": probs["away"],
            "status": "PENDING"
        }

        opportunities = {}
        recommended_bet = "none"
        edge_identified = False
        kelly_fraction = 0.0

        if book_odds:
            opportunities = kelly_engine.evaluate_betting_opportunities(probs, book_odds)
            print("Betting Calculations (EV & Kelly Stakes):")
            for outcome, details in opportunities.items():
                print(f"  {outcome.capitalize()}: EV = {details['ev']*100:+.2f}% | Kelly Stake = {details['suggested_stake_pct']}% (${args.bankroll * details['suggested_stake_fraction']:.2f})")
            
            # Map implied probabilities from odds
            margin_sum = sum(1.0 / book_odds[k] for k in ["home", "draw", "away"])
            pred_data.update({
                "odds_home": args.odds_home,
                "odds_draw": args.odds_draw,
                "odds_away": args.odds_away,
                "implied_p_home": round((1.0 / args.odds_home) / margin_sum, 4),
                "implied_p_draw": round((1.0 / args.odds_draw) / margin_sum, 4),
                "implied_p_away": round((1.0 / args.odds_away) / margin_sum, 4),
                "calculated_ev_home": opportunities["home"]["ev"],
                "calculated_ev_draw": opportunities["draw"]["ev"],
                "calculated_ev_away": opportunities["away"]["ev"],
                "kelly_stake_home": opportunities["home"]["suggested_stake_fraction"],
                "kelly_stake_draw": opportunities["draw"]["suggested_stake_fraction"],
                "kelly_stake_away": opportunities["away"]["suggested_stake_fraction"]
            })

            # Auto-recommend bet based on highest positive EV outcome
            positive_evs = {k: o["ev"] for k, o in opportunities.items() if o["ev"] > 0}
            if positive_evs:
                best_ev_outcome = max(positive_evs, key=positive_evs.get)
                # Map to schema-compliant bets: 'home_win', 'draw', 'away_win'
                bet_map = {"home": "home_win", "draw": "draw", "away": "away_win"}
                recommended_bet = bet_map[best_ev_outcome]
                edge_identified = True
                kelly_fraction = opportunities[best_ev_outcome]["suggested_stake_fraction"]

            # Check if user explicitly placed a bet
            if args.place_bet != "none":
                placed_stake_frac = opportunities[args.place_bet]["suggested_stake_fraction"]
                pred_data.update({
                    "placed_bet_type": args.place_bet,
                    "placed_bet_odds": book_odds[args.place_bet],
                    "placed_bet_stake": args.bankroll * placed_stake_frac
                })
                # If explicit bet is placed, recommend it
                bet_map = {"home": "home_win", "draw": "draw", "away": "away_win"}
                recommended_bet = bet_map[args.place_bet]
                edge_identified = True
                kelly_fraction = placed_stake_frac
                print("-" * 70)
                print(f"[REGISTERED BET] Placed ${args.bankroll * placed_stake_frac:.2f} on {args.place_bet.capitalize()} at odds {book_odds[args.place_bet]}")
        else:
            print("No bookmaker odds provided. Betting calculations bypassed.")

        # Log to SQLite
        log_prediction(pred_data, db_path)
        print("-" * 70)
        print(f"[SUCCESS] Prediction logged to database with ID: {match_id}")

        # Construct reasoning if not provided
        if args.reasoning:
            reasoning_str = args.reasoning
        elif recommended_bet != "none":
            outcome_key = recommended_bet.split("_")[0]
            prob_pct = probs[outcome_key] * 100
            implied_pct = (1.0 / book_odds[outcome_key]) * 100
            reasoning_str = f"Model probability ({prob_pct:.1f}%) exceeds implied odds ({implied_pct:.1f}%). Manager performance and tactical matchups show value on the {outcome_key}."
        else:
            reasoning_str = "No positive expected value identified across the market outcomes under current tactical settings."

        # Log to JSON document
        json_logger = JsonDocumentLogger()
        meta_data = {
            "date": args.date or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "competition": args.competition,
            "home_team": home_team,
            "away_team": away_team,
            "home_manager": home_mgr,
            "away_manager": away_mgr
        }
        context_data = {
            "predicted_home_formation": args.home_form,
            "predicted_away_formation": args.away_form,
            "manager_h2h_win_rate_home": args.manager_h2h_win_rate_home,
            "tactical_flexibility_index_home": args.tactical_flexibility_index_home,
            "tactical_flexibility_index_away": args.tactical_flexibility_index_away
        }
        market_data = {
            "bookmaker": args.bookmaker,
            "odds_decimal": {
                "home_win": args.odds_home or 0.0,
                "draw": args.odds_draw or 0.0,
                "away_win": args.odds_away or 0.0
            }
        }
        inference_data = {
            "model_probabilities": {
                "home_win": probs["home"],
                "draw": probs["draw"],
                "away_win": probs["away"]
            },
            "expected_value": {
                "home_win": opportunities.get("home", {}).get("ev", 0.0) if book_odds else 0.0,
                "draw": opportunities.get("draw", {}).get("ev", 0.0) if book_odds else 0.0,
                "away_win": opportunities.get("away", {}).get("ev", 0.0) if book_odds else 0.0
            },
            "action": {
                "recommended_bet": recommended_bet,
                "edge_identified": edge_identified,
                "kelly_fraction_pct": round(kelly_fraction * 100, 2),
                "reasoning": reasoning_str
            }
        }
        
        json_logger.log_pre_match(match_id, meta_data, context_data, market_data, inference_data)

    elif args.command == "settle":
        evaluator = ModelEvaluator(db_path, settings_path)
        try:
            outcome = evaluator.settle_match(
                args.id, args.home_score, args.away_score,
                args.home_form, args.away_form, args.notes
            )
            print_banner()
            print(f"[SUCCESS] Match settled successfully!")
            print(f"  Result: Winner = {outcome['winner']} (Home {args.home_score} - {args.away_score} Away)")
            print(f"  Actual Formations: Home = {args.home_form} | Away = {args.away_form}")
            print(f"  Financial Result: Profit/Loss = ${outcome['net_profit_loss']:+.2f}")
            
            # Settle in JSON log
            json_logger = JsonDocumentLogger()
            json_logger.settle_match(
                args.id, args.home_score, args.away_score,
                actual_home_formation=args.home_form,
                actual_away_formation=args.away_form,
                notes=args.notes
            )
        except Exception as e:
            print(f"[ERROR] Failed to settle match: {e}")

    elif args.command == "audit":
        evaluator = ModelEvaluator(db_path, settings_path)
        metrics = evaluator.calculate_metrics()
        print_banner()
        print("Performance Audit Metrics:")
        print(f"  Total Predictions Settled: {metrics['total_predictions']}")
        print(f"  Total Bets Placed:         {metrics['total_bets_placed']}")
        print(f"  Total Investment:          ${metrics['total_stake']:.2f}")
        print(f"  Net Profit/Loss:           ${metrics['total_profit_loss']:+.2f}")
        print(f"  System ROI:                {metrics['roi_pct']}%")
        print(f"  Win Rate:                  {metrics['win_rate_pct']}%")
        print(f"  Brier Calibration Score:   {metrics['brier_score'] if metrics['brier_score'] is not None else 'N/A'}")
        print("-" * 70)
        print("Brier Score Reference: 0.0 is perfect calibration, < 0.22 is strong for football prediction.")
        
        # Display list of pending predictions
        pending = get_pending_predictions(db_path)
        if pending:
            print("\nPending Predictions awaiting settlement:")
            for p in pending:
                print(f"  ID: {p['id']} | {p['match_date']} | {p['home_team']} vs {p['away_team']}")

    elif args.command == "calibrate":
        evaluator = ModelEvaluator(db_path, settings_path)
        new_weights = evaluator.calibrate_weights(args.base, args.mgr, args.tactics)
        print_banner()
        print(f"[SUCCESS] Model weights recalibrated and saved:")
        print(f"  Baseline Weight:        {new_weights['baseline_weight']}")
        print(f"  Manager Form Weight:    {new_weights['manager_form_weight']}")
        print(f"  Tactical Matchup Weight: {new_weights['tactical_matchup_weight']}")

    elif args.command == "learn":
        evaluator = ModelEvaluator(db_path, settings_path)
        evaluator.evaluate_and_adjust_from_results(args.results, predictions_dir="predictions", learning_rate=args.lr, elo_k=args.k)

    elif args.command == "demo":
        print_banner()
        print("Running end-to-end tactical sports betting simulation with JSON logging...")
        print("-" * 70)
        
        # 1. Initialize DB
        print("Step 1: Re-initializing Database...")
        init_db(db_path)
        
        # 2. Add an alias/mapping & show Entity resolution
        resolver = EntityResolver(aliases_path)
        print("\nStep 2: Testing Entity Resolution:")
        resolved_mgr = resolver.resolve_manager("P. Guardiola")
        print(f"  Name variation: 'P. Guardiola' resolved to canonical name: '{resolved_mgr}'")
        
        # 3. Perform a prediction with odds
        print("\nStep 3: Calculating Match Probabilities, EV and Logging JSON (Pep vs Arteta):")
        prob_engine = ProbabilityEngine(settings_path)
        odds = {"home": 2.10, "draw": 3.40, "away": 3.30}
        probs_result = prob_engine.calculate_match_probabilities(
            "Manchester City", "Arsenal", "Pep Guardiola", "Mikel Arteta", "4-3-3", "4-2-3-1",
            bookmaker_odds=odds
        )
        
        probs = probs_result["probabilities"]
        print(f"  Manchester City vs Arsenal")
        print(f"  Calculated Probabilities -> Home Win: {probs['home']*100:.2f}%, Draw: {probs['draw']*100:.2f}%, Away: {probs['away']*100:.2f}%")
        
        # 4. Calculate Kelly Bet & EV
        kelly_engine = KellyEngine(settings_path)
        opts = kelly_engine.evaluate_betting_opportunities(probs, odds)
        
        best_outcome = max(opts.keys(), key=lambda k: opts[k]["ev"])
        best_ev = opts[best_outcome]["ev"]
        
        placed_bet_type = "none"
        placed_bet_stake = 0.0
        placed_bet_odds = 0.0
        recommended_bet = "none"
        edge_identified = False
        kelly_fraction = 0.0
        
        if best_ev > 0:
            bet_map = {"home": "home_win", "draw": "draw", "away": "away_win"}
            recommended_bet = bet_map[best_outcome]
            edge_identified = True
            kelly_fraction = opts[best_outcome]["suggested_stake_fraction"]
            placed_bet_type = best_outcome
            placed_bet_odds = odds[best_outcome]
            placed_bet_stake = 1000.0 * kelly_fraction
            print(f"  ==> Placing Bet: ${placed_bet_stake:.2f} on {placed_bet_type.capitalize()} at odds {placed_bet_odds}")

        # Log prediction to SQLite and JSON
        match_id = "epl_20260521_mci_ars"
        margin_sum = sum(1.0 / odds[k] for k in ["home", "draw", "away"])
        pred_data = {
            "id": match_id,
            "match_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "home_team": "Manchester City",
            "away_team": "Arsenal",
            "home_manager": "Josep Guardiola",
            "away_manager": "Mikel Arteta",
            "home_predicted_formation": "4-3-3",
            "away_predicted_formation": "4-2-3-1",
            "model_p_home": probs["home"],
            "model_p_draw": probs["draw"],
            "model_p_away": probs["away"],
            "odds_home": odds["home"],
            "odds_draw": odds["draw"],
            "odds_away": odds["away"],
            "implied_p_home": round((1.0 / odds["home"]) / margin_sum, 4),
            "implied_p_draw": round((1.0 / odds["draw"]) / margin_sum, 4),
            "implied_p_away": round((1.0 / odds["away"]) / margin_sum, 4),
            "calculated_ev_home": opts["home"]["ev"],
            "calculated_ev_draw": opts["draw"]["ev"],
            "calculated_ev_away": opts["away"]["ev"],
            "kelly_stake_home": opts["home"]["suggested_stake_fraction"],
            "kelly_stake_draw": opts["draw"]["suggested_stake_fraction"],
            "kelly_stake_away": opts["away"]["suggested_stake_fraction"],
            "placed_bet_type": placed_bet_type,
            "placed_bet_odds": placed_bet_odds,
            "placed_bet_stake": placed_bet_stake,
            "status": "PENDING"
        }
        log_prediction(pred_data, db_path)
        
        # Log JSON
        json_logger = JsonDocumentLogger()
        meta_data = {
            "date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "competition": "Premier League",
            "home_team": "Manchester City",
            "away_team": "Arsenal",
            "home_manager": "Josep Guardiola",
            "away_manager": "Mikel Arteta"
        }
        context_data = {
            "predicted_home_formation": "4-3-3",
            "predicted_away_formation": "4-2-3-1",
            "manager_h2h_win_rate_home": 0.55,
            "tactical_flexibility_index_home": 0.8,
            "tactical_flexibility_index_away": 0.7
        }
        market_data = {
            "bookmaker": "Pinnacle",
            "odds_decimal": {
                "home_win": odds["home"],
                "draw": odds["draw"],
                "away_win": odds["away"]
            }
        }
        inference_data = {
            "model_probabilities": {
                "home_win": probs["home"],
                "draw": probs["draw"],
                "away_win": probs["away"]
            },
            "expected_value": {
                "home_win": opts["home"]["ev"],
                "draw": opts["draw"]["ev"],
                "away_win": opts["away"]["ev"]
            },
            "action": {
                "recommended_bet": recommended_bet,
                "edge_identified": edge_identified,
                "kelly_fraction_pct": round(kelly_fraction * 100, 2),
                "reasoning": f"Model probability of City ({probs['home']*100:.1f}%) exceeds implied odds."
            }
        }
        json_logger.log_pre_match(match_id, meta_data, context_data, market_data, inference_data)
        
        # 5. Settle match
        print("\nStep 5: Settling Matchday Outcomes (City 2 - 1 Arsenal):")
        evaluator = ModelEvaluator(db_path, settings_path)
        settle_res = evaluator.settle_match(
            match_id, home_score=2, away_score=1,
            home_actual_formation="4-3-3", away_actual_formation="4-2-3-1",
            human_notes="Sticking to tactical plan worked."
        )
        json_logger.settle_match(
            match_id, score_home=2, score_away=1,
            actual_home_formation="4-3-3", actual_away_formation="4-2-3-1",
            notes="Guardiola controls middle."
        )
        print(f"  Match Settled. Winner: {settle_res['winner']} | Profit/Loss: ${settle_res['net_profit_loss']:+.2f}")
        
        # 6. Audit statistics
        print("\nStep 6: Auditing Performance Statistics:")
        metrics = evaluator.calculate_metrics()
        print(f"  Total Predictions: {metrics['total_predictions']}")
        print(f"  Brier Score:       {metrics['brier_score']}")
        print("-" * 70)
        print("Simulation Completed Successfully!")

if __name__ == "__main__":
    main()
