"""
Expected Value and Probability Calibration Engine.
"""

def calculate_true_probabilities(odds_dict):
    """
    Strips the bookmaker's margin from a dictionary of odds using the Multiplicative Method.
    Input:
        odds_dict: dict like {"home": 2.10, "draw": 3.40, "away": 3.20} or {"over_2.5": 1.90, "under_2.5": 1.90}
    Returns:
        dict: true fair probabilities for each outcome summing to 1.0 (or empty dict if invalid)
    """
    implied_probs = {}
    for outcome, odds in odds_dict.items():
        if odds and odds > 1.0:
            implied_probs[outcome] = 1.0 / odds
        else:
            implied_probs[outcome] = 0.0
            
    total_margin = sum(implied_probs.values())
    
    true_probs = {}
    for outcome, implied_p in implied_probs.items():
        if total_margin > 0:
            true_probs[outcome] = implied_p / total_margin
        else:
            true_probs[outcome] = 0.0
            
    return true_probs

def calculate_ev(model_p, actual_decimal_odds):
    """
    Calculates expected value relative to actual bookmaker decimal odds.
    EV = (Model Probability * Actual Decimal Odds) - 1.0
    """
    if not actual_decimal_odds or actual_decimal_odds <= 1.0:
        return 0.0
    return (model_p * actual_decimal_odds) - 1.0

def apply_motivation_modifiers(baseline_metrics, motivation_modifier=None):
    """
    Applies motivation modifiers strictly to pre-distribution baseline metrics (like xG or linear strength),
    and never directly to final win percentages or raw logarithmic ELO scores.
    
    baseline_metrics: dict containing keys like 'home' and 'away' representing baseline ratings/xG.
    motivation_modifier: dict containing multipliers like {'home': 1.12, 'away': 1.0}
    """
    if not motivation_modifier:
        return baseline_metrics
        
    modified = baseline_metrics.copy()
    if "home" in modified and "home" in motivation_modifier:
        modified["home"] = modified["home"] * motivation_modifier["home"]
    if "away" in modified and "away" in motivation_modifier:
        modified["away"] = modified["away"] * motivation_modifier["away"]
        
    return modified


def evaluate_market_ev(model_probabilities, bookmaker_odds):
    """
    Evaluates Expected Value and true probabilities for a market.
    Safely handles two-way markets (no 'draw' key).
    """
    true_probs = calculate_true_probabilities(bookmaker_odds)
    results = {}
    for outcome, odds in bookmaker_odds.items():
        p = model_probabilities.get(outcome, 0.0)
        true_p = true_probs.get(outcome, 0.0)
        ev = calculate_ev(p, odds)
        results[outcome] = {
            "ev": round(ev, 4),
            "true_fair_p": round(true_p, 4)
        }
    if "draw" not in results:
        results["draw"] = {
            "ev": 0.0,
            "true_fair_p": 0.0
        }
    return results

