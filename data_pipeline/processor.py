import re

def standardize_formation(formation_str):
    """
    Standardize formation string (e.g., '4-2-3-1 deep' -> '4-2-3-1', '3-5-2 flat' -> '3-5-2').
    Returns a normalized string like '4-3-3', '4-4-2', '3-5-2', etc.
    """
    if not formation_str:
        return "4-4-2" # default
    
    # Strip whitespace and lowercase
    f = formation_str.strip().lower()
    
    # Regular expression to extract numbers separated by dashes (e.g., 4-3-3, 4-2-3-1, 3-5-2)
    match = re.search(r'\b\d-\d-\d(?:-\d)?\b', f)
    if match:
        return match.group(0)
        
    # Check if numbers are separated by spaces or other delimiters
    match_spaces = re.search(r'\b(\d)\s+(\d)\s+(\d)(?:\s+(\d))?\b', f)
    if match_spaces:
        parts = [g for g in match_spaces.groups() if g is not None]
        return "-".join(parts)
        
    # Fallback cleanup rules
    clean = re.sub(r'[^0-9\-]', '', f)
    if '-' in clean and len(clean) >= 5:
        return clean
        
    return "4-4-2" # standard default fallback

def get_matchup_tactical_advantage(formation_home, formation_away):
    """
    Computes a tactical matchup rating modifier based on formation dynamics.
    For example:
    - 3-5-2 wingbacks tend to match up well against 4-3-3 wide attackers.
    - 4-2-3-1 double pivots control midfield space against a standard 4-4-2 flat.
    Returns:
        (home_advantage, away_advantage) as float multipliers centered around 1.0.
    """
    f_home = standardize_formation(formation_home)
    f_away = standardize_formation(formation_away)
    
    # Basic lookup of tactical counters
    # Structure: (Home Formation, Away Formation) -> (Home Mod, Away Mod)
    matchup_matrix = {
        ("3-5-2", "4-3-3"): (1.05, 0.95),  # 3-5-2 wingbacks overload 4-3-3 wide zones
        ("4-3-3", "3-5-2"): (0.95, 1.05),
        
        ("4-2-3-1", "4-4-2"): (1.06, 0.94), # 4-2-3-1 controls the midfield triangle against flat 4-4-2
        ("4-4-2", "4-2-3-1"): (0.94, 1.06),
        
        ("4-3-3", "4-2-3-1"): (1.02, 0.98), # 4-3-3 flat can match 4-2-3-1 depth
        ("4-2-3-1", "4-3-3"): (0.98, 1.02),
        
        ("3-4-3", "4-4-2"): (1.04, 0.96),
        ("4-4-2", "3-4-3"): (0.96, 1.04),
    }
    
    return matchup_matrix.get((f_home, f_away), (1.0, 1.0))
