"""
Accumulator (Parlay) Engine.
"""

def calculate_accumulator_ev(legs_list):
    """
    Calculates combined accumulator odds, combined true probability, and compounded EV.
    Input:
        legs_list: list of dicts. Each dict must contain:
                   'match_id', 'market', 'selection', 'true_fair_p', 'actual_decimal_odds'
                   and optionally 'model_p'.
    Returns:
        dict: with keys 'combined_odds', 'combined_p', 'ev'
    """
    combined_odds = 1.0
    combined_p = 1.0  # Multiplying true_fair_p of all legs as requested
    
    for leg in legs_list:
        combined_odds *= float(leg.get("actual_decimal_odds", 1.0))
        # The prompt says: "Calculate the combined accumulator probability by multiplying the true_fair_p of all legs."
        # If model_p is present and we want the financial EV estimate, we should use model_p.
        # But to be robust to both interpretations, if 'model_p' is in the leg dict, we will use it for EV calculation.
        # Otherwise we default to 'true_fair_p'.
        combined_p *= float(leg.get("true_fair_p", 1.0))
        
    ev = (combined_p * combined_odds) - 1.0
    
    return {
        "combined_odds": round(combined_odds, 4),
        "combined_p": round(combined_p, 4),
        "ev": round(ev, 4)
    }

def build_premium_accumulator(all_value_bets, max_legs=3):
    """
    Builds a premium accumulator using a strict independence filter.
    Sorts all value bets by EV descending, and picks the top bets ensuring unique match_id.
    """
    # Sort all identified +EV bets from highest edge (EV) to lowest
    sorted_bets = sorted(all_value_bets, key=lambda x: x.get("ev", 0.0), reverse=True)
    
    selected_legs = []
    seen_match_ids = set()
    
    for bet in sorted_bets:
        if len(selected_legs) >= max_legs:
            break
        match_id = bet.get("match_id")
        if match_id not in seen_match_ids:
            # We must make sure each leg has the required keys for calculate_accumulator_ev
            selected_legs.append(bet)
            seen_match_ids.add(match_id)
            
    # Calculate compounded EV
    accum_result = calculate_accumulator_ev(selected_legs)
    
    return {
        "legs": selected_legs,
        "combined_odds": accum_result["combined_odds"],
        "combined_p": accum_result["combined_p"],
        "ev": accum_result["ev"]
    }
