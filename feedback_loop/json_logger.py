import os
import json
from datetime import datetime

class JsonDocumentLogger:
    def __init__(self, log_dir="predictions"):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

    def _get_file_path(self, match_id):
        # Ensure safe filename
        safe_id = "".join([c for c in match_id if c.isalnum() or c in ("_", "-")]).lower()
        return os.path.join(self.log_dir, f"{safe_id}.json")

    def log_pre_match(self, match_id, meta_data, context_data, market_data, inference_data):
        """
        Creates and writes the pre-match inference JSON log.
        """
        log_structure = {
            "match_id": match_id,
            "meta": {
                "date": meta_data.get("date", datetime.utcnow().isoformat() + "Z"),
                "competition": meta_data.get("competition", "Premier League"),
                "home_team": meta_data.get("home_team", ""),
                "away_team": meta_data.get("away_team", ""),
                "home_manager": meta_data.get("home_manager", ""),
                "away_manager": meta_data.get("away_manager", "")
            },
            "pre_match_context": {
                "predicted_home_formation": context_data.get("predicted_home_formation", "4-4-2"),
                "predicted_away_formation": context_data.get("predicted_away_formation", "4-4-2"),
                "manager_h2h_win_rate_home": float(context_data.get("manager_h2h_win_rate_home", 0.0)),
                "tactical_flexibility_index_home": float(context_data.get("tactical_flexibility_index_home", 0.5)),
                "tactical_flexibility_index_away": float(context_data.get("tactical_flexibility_index_away", 0.5))
            },
            "market_state": {
                "bookmaker": market_data.get("bookmaker", "Pinnacle"),
                "odds_decimal": {
                    k: float(v) for k, v in market_data.get("odds_decimal", {}).items()
                }
            },
            "agent_inference": {
                "model_probabilities": {
                    k: float(v) for k, v in inference_data.get("model_probabilities", {}).items()
                },
                "expected_value": {
                    k: float(v) for k, v in inference_data.get("expected_value", {}).items()
                },
                "action": {
                    "recommended_bet": inference_data.get("action", {}).get("recommended_bet", "none"),
                    "edge_identified": bool(inference_data.get("action", {}).get("edge_identified", False)),
                    "kelly_fraction_pct": float(inference_data.get("action", {}).get("kelly_fraction_pct", 0.0)),
                    "reasoning": inference_data.get("action", {}).get("reasoning", "")
                }
            },
            "post_match_resolution": {
                "status": "pending",
                "actual_result": None,
                "actual_score": None,
                "profit_loss_units": None
            },
            "calibration_loop": {
                "human_override_notes": None,
                "agent_learning_adjustment": None
            }
        }
        
        file_path = self._get_file_path(match_id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(log_structure, f, indent=2)
            
        print(f"[JSON LOG] Pre-match inference log written to {file_path}")
        return log_structure

    def settle_match(self, match_id, score_home, score_away, actual_home_formation=None, actual_away_formation=None, notes=None):
        """
        Settles a logged match using final scores, and computes profit/loss units.
        """
        file_path = self._get_file_path(match_id)
        if not os.path.exists(file_path):
            print(f"[JSON LOG] Warning: pre-match JSON log not found for match_id: {match_id}")
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Determine winner
        if score_home > score_away:
            result = "home_win"
        elif score_home < score_away:
            result = "away_win"
        else:
            result = "draw"

        # Calculate profit/loss units
        rec_bet = data["agent_inference"]["action"]["recommended_bet"]
        kelly_frac_pct = data["agent_inference"]["action"]["kelly_fraction_pct"]
        
        pnl_units = 0.0
        if rec_bet and rec_bet != "none":
            odds = data["market_state"]["odds_decimal"].get(rec_bet, 0.0)
            stake_pct = kelly_frac_pct / 100.0  # Fraction of bankroll
            
            if rec_bet == result:
                # Won: profit = stake * (odds - 1)
                pnl_units = stake_pct * (odds - 1.0)
            else:
                # Lost: loss = -stake
                pnl_units = -stake_pct

        # Update JSON fields
        data["post_match_resolution"] = {
            "status": "settled",
            "actual_result": result,
            "actual_score": f"{score_home}-{score_away}",
            "profit_loss_units": round(pnl_units, 4)
        }
        
        if notes:
            data["calibration_loop"]["human_override_notes"] = notes
            
        # Add feedback parameters adjustments if any
        # E.g. did formations drift
        pred_home_form = data["pre_match_context"]["predicted_home_formation"]
        pred_away_form = data["pre_match_context"]["predicted_away_formation"]
        
        drifts = []
        if actual_home_formation and actual_home_formation != pred_home_form:
            drifts.append(f"Home formation drift: {pred_home_form} -> {actual_home_formation}")
        if actual_away_formation and actual_away_formation != pred_away_form:
            drifts.append(f"Away formation drift: {pred_away_form} -> {actual_away_formation}")
            
        if drifts:
            data["calibration_loop"]["agent_learning_adjustment"] = "; ".join(drifts)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        print(f"[JSON LOG] Match {match_id} resolved in JSON log.")
        return data
