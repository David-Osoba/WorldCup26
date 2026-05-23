import os
import json
from feedback_loop.logger import get_all_settled_predictions, settle_prediction, get_pending_predictions

class ModelEvaluator:
    def __init__(self, db_path="data/tactics_betting.db", settings_path="config/settings.json"):
        self.db_path = db_path
        self.settings_path = settings_path
        self.load_settings()

    def load_settings(self):
        if os.path.exists(self.settings_path):
            with open(self.settings_path, "r", encoding="utf-8") as f:
                self.settings = json.load(f)
        else:
            self.settings = {}

    def save_settings(self):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=2)

    def calculate_metrics(self):
        """
        Calculates performance metrics from all settled bets in the database:
        - ROI (Return on Investment)
        - Multi-class Brier Score (Forecast calibration)
        - Win rate & Profit/Loss totals
        """
        predictions = get_all_settled_predictions(self.db_path)
        if not predictions:
            return {
                "total_predictions": 0,
                "total_bets_placed": 0,
                "total_stake": 0.0,
                "total_profit_loss": 0.0,
                "roi_pct": 0.0,
                "brier_score": None,
                "win_rate_pct": 0.0
            }

        total_stake = 0.0
        total_pnl = 0.0
        win_count = 0
        bets_placed_count = 0
        brier_sum = 0.0
        brier_count = 0

        for pred in predictions:
            # 1. Brier Score calculation (calibration audit)
            # Actual outcome representation
            winner = pred["winner"] # 'HOME', 'DRAW', or 'AWAY'
            o_h = 1.0 if winner == "HOME" else 0.0
            o_d = 1.0 if winner == "DRAW" else 0.0
            o_a = 1.0 if winner == "AWAY" else 0.0

            p_h = pred.get("model_p_home") or 0.0
            p_d = pred.get("model_p_draw") or 0.0
            p_a = pred.get("model_p_away") or 0.0

            # Multi-class Brier Score: sum of squared differences divided by number of categories (3)
            # BS = 1/3 * ((p_h - o_h)^2 + (p_d - o_d)^2 + (p_a - o_a)^2)
            # This bounds the score between 0.0 (perfect prediction) and 0.667 (worst possible prediction)
            match_brier = ( (p_h - o_h)**2 + (p_d - o_d)**2 + (p_a - o_a)**2 ) / 3.0
            brier_sum += match_brier
            brier_count += 1

            # 2. Betting performance calculation
            bet_type = pred["placed_bet_type"]
            if bet_type and bet_type != "none":
                bets_placed_count += 1
                stake = pred["placed_bet_stake"] or 0.0
                pnl = pred["net_profit_loss"] or 0.0
                
                total_stake += stake
                total_pnl += pnl
                if pnl > 0:
                    win_count += 1

        avg_brier = brier_sum / brier_count if brier_count > 0 else 0.0
        roi = (total_pnl / total_stake * 100) if total_stake > 0 else 0.0
        win_rate = (win_count / bets_placed_count * 100) if bets_placed_count > 0 else 0.0

        return {
            "total_predictions": len(predictions),
            "total_bets_placed": bets_placed_count,
            "total_stake": round(total_stake, 2),
            "total_profit_loss": round(total_pnl, 2),
            "roi_pct": round(roi, 2),
            "brier_score": round(avg_brier, 4),
            "win_rate_pct": round(win_rate, 2)
        }

    def settle_match(self, prediction_id, home_score, away_score, 
                    home_actual_formation, away_actual_formation, human_notes=""):
        """
        Settles a pending prediction with actual match outcomes.
        Calculates profit/loss from the placed bet.
        """
        # Fetch the pending prediction
        predictions = get_pending_predictions(self.db_path)
        pred = next((p for p in predictions if p["id"] == prediction_id), None)
        
        if not pred:
            raise ValueError(f"Pending prediction with ID {prediction_id} not found.")

        # Determine winner
        if home_score > away_score:
            winner = "HOME"
        elif home_score < away_score:
            winner = "AWAY"
        else:
            winner = "DRAW"

        # Calculate Net Profit/Loss
        bet_type = pred["placed_bet_type"]
        stake = pred["placed_bet_stake"] or 0.0
        odds = pred["placed_bet_odds"] or 0.0
        
        net_profit_loss = 0.0
        if bet_type and bet_type != "none":
            # Map winner string to bet type key
            result_map = {"HOME": "home", "DRAW": "draw", "AWAY": "away"}
            actual_winning_outcome = result_map[winner]
            
            if bet_type == actual_winning_outcome:
                # Win
                net_profit_loss = (stake * odds) - stake
            else:
                # Loss
                net_profit_loss = -stake

        outcome_data = {
            "home_score": home_score,
            "away_score": away_score,
            "winner": winner,
            "home_actual_formation": home_actual_formation,
            "away_actual_formation": away_actual_formation,
            "net_profit_loss": round(net_profit_loss, 4),
            "human_notes": human_notes
        }

        settle_prediction(prediction_id, outcome_data, self.db_path)
        return outcome_data

    def calibrate_weights(self, baseline, manager_form, tactical_matchup):
        """
        Manually recalibrate prediction weight coefficients.
        Saves to settings.json.
        """
        total = baseline + manager_form + tactical_matchup
        if abs(total - 1.0) > 0.001:
            # Normalize to 1.0
            baseline = baseline / total
            manager_form = manager_form / total
            tactical_matchup = tactical_matchup / total
            
        self.settings["model_weights"] = {
            "baseline_weight": round(baseline, 3),
            "manager_form_weight": round(manager_form, 3),
            "tactical_matchup_weight": round(tactical_matchup, 3)
        }
        self.save_settings()
        return self.settings["model_weights"]

    def evaluate_and_adjust_from_results(self, results_filepaths, predictions_dir="predictions", learning_rate=0.05, elo_k=32.0):
        """
        Ingests JSON result files containing the actual final scores of domestic matches.
        Compares the actual results against the pre-match model probabilities logged in predictions/ folder.
        Adjusts the baseline ELO ratings and xG weights based on the delta between predicted and actual outcomes.
        """
        from data_pipeline.utils import normalize_string
        from data_pipeline.scrapers import FBrefWhoScoredScraper
        from feedback_loop.json_logger import JsonDocumentLogger

        self.load_settings()
        elo_ratings = self.settings.setdefault("elo_ratings", {})
        xg_weights = self.settings.setdefault("xg_weights", {
            "goals_over_under": 1.0,
            "btts": 1.0,
            "corners": 1.0
        })

        # Load all logged pre-match predictions
        prediction_docs = []
        if os.path.exists(predictions_dir):
            for filename in os.listdir(predictions_dir):
                if filename.endswith(".json") and "ticket" not in filename:
                    path = os.path.join(predictions_dir, filename)
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            doc = json.load(f)
                            if "meta" in doc and "agent_inference" in doc:
                                prediction_docs.append((path, doc))
                    except Exception as e:
                        print(f"[EVALUATOR] Failed to load prediction file {filename}: {e}")

        print(f"[EVALUATOR] Loaded {len(prediction_docs)} pre-match predictions from '{predictions_dir}'.")

        def find_prediction(home, away):
            norm_home = normalize_string(home)
            norm_away = normalize_string(away)
            for path, doc in prediction_docs:
                p_home = normalize_string(doc["meta"]["home_team"])
                p_away = normalize_string(doc["meta"]["away_team"])
                if p_home == norm_home and p_away == norm_away:
                    return path, doc
            return None, None

        fbref = FBrefWhoScoredScraper(self.settings_path)
        json_logger = JsonDocumentLogger(predictions_dir)

        total_processed = 0
        
        # Ingest and compare
        for results_file in results_filepaths:
            if not os.path.exists(results_file):
                print(f"[EVALUATOR] Results file '{results_file}' not found. Skipping.")
                continue

            print(f"[EVALUATOR] Ingesting results from '{results_file}'...")
            with open(results_file, "r", encoding="utf-8") as f:
                results_data = json.load(f)

            for match_key, res_val in results_data.items():
                if " vs " not in match_key:
                    continue
                home_team, away_team = match_key.split(" vs ")
                
                # Find matching prediction
                pred_path, pred_doc = find_prediction(home_team, away_team)
                if not pred_doc:
                    # Try reverse check just in case
                    pred_path, pred_doc = find_prediction(away_team, home_team)
                    if not pred_doc:
                        print(f"[EVALUATOR] No pre-match prediction logged for: {home_team} vs {away_team}. Skipping.")
                        continue
                
                match_id = pred_doc["match_id"]
                home_score = res_val["home_score"]
                away_score = res_val["away_score"]
                total_corners = res_val.get("total_corners", 9)
                h_actual_form = res_val.get("home_actual_formation", "4-4-2")
                a_actual_form = res_val.get("away_actual_formation", "4-4-2")
                
                # Determine outcome
                if home_score > away_score:
                    outcome_score = 1.0 # home win
                    winner_str = "HOME"
                elif home_score < away_score:
                    outcome_score = 0.0 # away win
                    winner_str = "AWAY"
                else:
                    outcome_score = 0.5 # draw
                    winner_str = "DRAW"

                # 1. Settle in DB and JSON logger if they are pending
                try:
                    self.settle_match(
                        prediction_id=match_id,
                        home_score=home_score,
                        away_score=away_score,
                        home_actual_formation=h_actual_form,
                        away_actual_formation=a_actual_form,
                        human_notes="Settled automatically via feedback loop evaluator."
                    )
                except Exception:
                    pass

                try:
                    json_logger.settle_match(
                        match_id=match_id,
                        score_home=home_score,
                        score_away=away_score,
                        actual_home_formation=h_actual_form,
                        actual_away_formation=a_actual_form,
                        notes="Settled automatically via feedback loop evaluator."
                    )
                except Exception:
                    pass

                # Get pre-match probabilities from doc
                probs = pred_doc["agent_inference"]["model_probabilities"]
                p_home = probs.get("home_win", probs.get("home", 0.33))
                
                # Get canonical names
                h_team_canon = pred_doc["meta"]["home_team"]
                a_team_canon = pred_doc["meta"]["away_team"]
                
                # 2. Adjust ELO ratings based on delta between predicted and actual outcome
                elo_ratings.setdefault(h_team_canon, 1500.0)
                elo_ratings.setdefault(a_team_canon, 1500.0)
                
                old_elo_h = elo_ratings[h_team_canon]
                old_elo_a = elo_ratings[a_team_canon]
                
                # Delta ELO calculation
                delta_elo = elo_k * (outcome_score - p_home)
                elo_ratings[h_team_canon] = round(old_elo_h + delta_elo, 2)
                elo_ratings[a_team_canon] = round(old_elo_a - delta_elo, 2)
                
                # 3. Adjust xG weights based on delta between predicted and actual outcomes
                home_stats = fbref.get_mock_advanced_team_stats(h_team_canon)
                away_stats = fbref.get_mock_advanced_team_stats(a_team_canon)
                
                # Goals Over/Under 2.5 Market
                p_over = probs.get("goals_over_2.5", probs.get("over", 0.5))
                y_over = 1.0 if (home_score + away_score) > 2.5 else 0.0
                shots_sum = home_stats.get("total_shots_attempted_rolling_avg", 12.0) + away_stats.get("total_shots_attempted_rolling_avg", 12.0)
                shots_factor = (shots_sum / 27.0) - 1.0
                
                # BTTS Yes/No Market
                p_btts = probs.get("btts_yes", probs.get("yes", 0.5))
                y_btts = 1.0 if (home_score > 0 and away_score > 0) else 0.0
                crosses_sum = home_stats.get("crosses_in_penalty_area_rolling_avg", 10.0) + away_stats.get("crosses_in_penalty_area_rolling_avg", 10.0)
                crosses_factor = (crosses_sum / 23.0) - 1.0
                
                # Corners Market (9.5 line)
                p_corners = probs.get("corners_over_9.5", probs.get("corners_over", 0.5))
                y_corners = 1.0 if total_corners > 9.5 else 0.0
                ppda_avg = (home_stats.get("ppda_rolling_avg", 11.5) + away_stats.get("ppda_rolling_avg", 11.5)) / 2.0
                ppda_factor = (11.0 / ppda_avg - 1.0) if ppda_avg > 0 else 0.0
                
                # Apply learning update rules
                xg_weights["goals_over_under"] += learning_rate * (y_over - p_over) * shots_factor
                xg_weights["btts"] += learning_rate * (y_btts - p_btts) * crosses_factor
                xg_weights["corners"] += learning_rate * (y_corners - p_corners) * ppda_factor
                
                # Clamp weights to prevent divergence/instability (range [0.1, 3.0])
                xg_weights["goals_over_under"] = round(max(0.1, min(3.0, xg_weights["goals_over_under"])), 4)
                xg_weights["btts"] = round(max(0.1, min(3.0, xg_weights["btts"])), 4)
                xg_weights["corners"] = round(max(0.1, min(3.0, xg_weights["corners"])), 4)
                
                total_processed += 1
                
                print(f"[LEARNING] Match {match_id}: {h_team_canon} vs {a_team_canon}")
                print(f"  Outcome: {home_score}-{away_score} | Corners: {total_corners}")
                print(f"  ELO Update: {h_team_canon} ({old_elo_h} -> {elo_ratings[h_team_canon]}), {a_team_canon} ({old_elo_a} -> {elo_ratings[a_team_canon]})")
                print(f"  Goals O/U 2.5: Pred={p_over:.4f}, Actual={y_over} | Shots Factor={shots_factor:+.4f}")
                print(f"  BTTS Yes/No:   Pred={p_btts:.4f}, Actual={y_btts} | Crosses Factor={crosses_factor:+.4f}")
                print(f"  Corners O/U:   Pred={p_corners:.4f}, Actual={y_corners} | PPDA Factor={ppda_factor:+.4f}")
                print("-" * 50)
                
        self.save_settings()
        print(f"[SUCCESS] Feedback Loop complete. Processed {total_processed} matches.")
        print(f"  New xG weights: {xg_weights}")
        return total_processed

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Ingest domestic results and calibrate ELO/xG weights.")
    parser.add_argument("--results", nargs="+", required=True, help="List of result JSON files to process.")
    parser.add_argument("--predictions", default="predictions", help="Predictions folder.")
    parser.add_argument("--lr", type=float, default=0.05, help="Learning rate for xG weights.")
    parser.add_argument("--k", type=float, default=32.0, help="ELO K-factor.")
    args = parser.parse_args()
    
    evaluator = ModelEvaluator()
    evaluator.evaluate_and_adjust_from_results(args.results, predictions_dir=args.predictions, learning_rate=args.lr, elo_k=args.k)
