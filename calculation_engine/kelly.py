import json
from calculation_engine.ev_calc import calculate_true_probabilities, calculate_ev

class KellyEngine:
    def __init__(self, settings_path="config/settings.json"):
        try:
            with open(settings_path, "r") as f:
                self.settings = json.load(f)
        except Exception:
            self.settings = {}
            
        self.default_fraction = self.settings.get("kelly_fraction", 0.25)
        self.max_fraction = self.settings.get("max_bankroll_fraction_per_bet", 0.05)

    def calculate_ev(self, model_p, decimal_odds):
        """
        Calculates Expected Value.
        Delegates to ev_calc.calculate_ev.
        """
        return calculate_ev(model_p, decimal_odds)

    def calculate_kelly_stake(self, model_p, decimal_odds, fraction=None, max_fraction=None):
        """
        Calculates fractional Kelly Criterion stake.
        Standard Kelly: f* = (p * dec_odds - 1) / (dec_odds - 1)
        Fractional Kelly: f = f* * fraction
        
        Returns:
            float: suggested stake as a fraction of bankroll (0.0 to max_fraction)
        """
        if fraction is None:
            fraction = self.default_fraction
        if max_fraction is None:
            max_fraction = self.max_fraction

        if not decimal_odds or decimal_odds <= 1.0:
            return 0.0

        ev = self.calculate_ev(model_p, decimal_odds)
        if ev <= 0:
            # Negative or zero expected value means no bet
            return 0.0

        # Net odds (decimal odds - 1)
        b = decimal_odds - 1.0
        
        # Standard Kelly stake fraction (f*)
        standard_kelly = ev / b
        
        # Apply fractional multiplier
        suggested_stake = standard_kelly * fraction
        
        # Clamp to maximum bankroll fraction limit
        clamped_stake = min(suggested_stake, max_fraction)
        
        return max(0.0, round(clamped_stake, 4))

    def evaluate_market_opportunities(self, model_probabilities, bookmaker_odds,
                                       fraction=None, max_fraction=None):
        """
        Evaluates opportunities for any single market (e.g. 1X2, Goals, BTTS, Corners).
        Input:
            model_probabilities: dict of outcome -> model_p (must sum to 1.0 or close)
            bookmaker_odds: dict of outcome -> decimal_odds
        Returns:
            dict of outcome -> {"ev": ..., "suggested_stake_pct": ..., "suggested_stake_fraction": ..., "true_fair_p": ...}
        """
        true_fair_probabilities = calculate_true_probabilities(bookmaker_odds)
        
        results = {}
        for outcome, odds in bookmaker_odds.items():
            p = model_probabilities.get(outcome, 0.0)
            true_fair_p = true_fair_probabilities.get(outcome, 0.0)
            
            if odds and odds > 1.0:
                # EV is calculated using actual bookmaker odds
                ev = self.calculate_ev(p, odds)
                
                # Bet is only recommended if we have a positive edge against the de-juiced fair probability
                if p > true_fair_p:
                    stake = self.calculate_kelly_stake(p, odds, fraction, max_fraction)
                else:
                    stake = 0.0
                    
                results[outcome] = {
                    "ev": round(ev, 4),
                    "suggested_stake_pct": round(stake * 100, 2), # convert to percentage for display
                    "suggested_stake_fraction": stake,
                    "true_fair_p": round(true_fair_p, 4)
                }
            else:
                results[outcome] = {
                    "ev": 0.0,
                    "suggested_stake_pct": 0.0,
                    "suggested_stake_fraction": 0.0,
                    "true_fair_p": 0.0
                }
        if "draw" not in results:
            results["draw"] = {
                "ev": 0.0,
                "suggested_stake_pct": 0.0,
                "suggested_stake_fraction": 0.0,
                "true_fair_p": 0.0
            }
        return results

    def evaluate_betting_opportunities(self, model_probabilities, bookmaker_odds, 
                                        fraction=None, max_fraction=None):
        """
        Evaluates opportunities across Home, Draw, and Away outcomes.
        Returns EV and fractional Kelly stakes for each outcome.
        Uses de-juiced probabilities to verify edge.
        """
        standard_odds = {
            "home": bookmaker_odds.get("home", 0.0),
            "draw": bookmaker_odds.get("draw", 0.0),
            "away": bookmaker_odds.get("away", 0.0)
        }
        standard_probs = {
            "home": model_probabilities.get("home", 0.0),
            "draw": model_probabilities.get("draw", 0.0),
            "away": model_probabilities.get("away", 0.0)
        }
        return self.evaluate_market_opportunities(standard_probs, standard_odds, fraction, max_fraction)

