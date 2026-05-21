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
