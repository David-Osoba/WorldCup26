import json
from data_pipeline.scrapers import ManagerScraper, FBrefWhoScoredScraper
from data_pipeline.processor import get_matchup_tactical_advantage, standardize_formation
from calculation_engine.ev_calc import calculate_true_probabilities

class ProbabilityEngine:
    def __init__(self, settings_path="config/settings.json"):
        with open(settings_path, "r") as f:
            self.settings = json.load(f)
        self.manager_scraper = ManagerScraper(settings_path)
        self.fbref_scraper = FBrefWhoScoredScraper(settings_path)

    def calculate_match_probabilities(self, home_team, away_team, 
                                     home_manager, away_manager,
                                     home_predicted_formation, away_predicted_formation,
                                     bookmaker_odds=None,
                                     is_neutral_venue=False,
                                     is_host_nation=None,
                                     motivation_modifier=None):
        """
        Calculates Win, Draw, and Loss probabilities using baseline ratings,
        manager stats, and formation matchups.
        
        bookmaker_odds: dict like {"home": 2.10, "draw": 3.40, "away": 3.20} (optional)
        is_neutral_venue: bool, default False. If True, removes standard home-field advantage (HFA).
        is_host_nation: str/bool/None (optional). Applies a slight 1.07x multiplier if one of the teams is the tournament host.
        motivation_modifier: dict like {"home": 1.12, "away": 1.0} (optional)
        """
        # 1. Compute baseline probabilities
        if bookmaker_odds:
            # Extract implied probabilities and strip margin safely
            implied_h = 1.0 / bookmaker_odds.get("home", 0.0) if bookmaker_odds.get("home", 0.0) and bookmaker_odds.get("home", 0.0) > 0 else 0.0
            implied_d = 1.0 / bookmaker_odds.get("draw", 0.0) if bookmaker_odds.get("draw", 0.0) and bookmaker_odds.get("draw", 0.0) > 0 else 0.0
            implied_a = 1.0 / bookmaker_odds.get("away", 0.0) if bookmaker_odds.get("away", 0.0) and bookmaker_odds.get("away", 0.0) > 0 else 0.0
            margin_sum = implied_h + implied_d + implied_a
            
            p_base_h = implied_h / margin_sum if margin_sum > 0 else 0.0
            p_base_d = implied_d / margin_sum if margin_sum > 0 else 0.0
            p_base_a = implied_a / margin_sum if margin_sum > 0 else 0.0
        else:
            if is_neutral_venue:
                # Symmetric baseline probabilities for neutral venues
                p_base_h = 0.36
                p_base_d = 0.28
                p_base_a = 0.36
            else:
                # Default fallback probabilities (home field advantage factored in)
                p_base_h = 0.42
                p_base_d = 0.28
                p_base_a = 0.30

        # Apply motivation modifiers to baseline ratings before blending/normalization
        if motivation_modifier:
            from calculation_engine.ev_calc import apply_motivation_modifiers
            baselines = {"home": p_base_h, "away": p_base_a}
            modified = apply_motivation_modifiers(baselines, motivation_modifier)
            p_base_h = modified["home"]
            p_base_a = modified["away"]

        # 2. Get Manager data
        home_m_data = self.manager_scraper.scrape_manager_profile(home_manager)
        away_m_data = self.manager_scraper.scrape_manager_profile(away_manager)
        
        ppg_h = home_m_data.get("points_per_game", 1.5)
        ppg_a = away_m_data.get("points_per_game", 1.5)
        
        # Calculate manager adjustment factors
        # Compare manager's historical PPG relative to a baseline of 1.5 (average)
        # e.g., Pep (2.3 PPG) gives a +8% relative modifier, Maresca (1.75 PPG) gives +2.5%
        m_factor_h = 1.0 + (ppg_h - 1.5) * 0.10
        m_factor_a = 1.0 + (ppg_a - 1.5) * 0.10

        # 3. Get Formation Matchup Advantage
        t_mod_h, t_mod_a = get_matchup_tactical_advantage(home_predicted_formation, away_predicted_formation)

        # 4. Integrate factors using weights configured in settings
        weights = self.settings.get("model_weights", {
            "baseline_weight": 0.50,
            "manager_form_weight": 0.20,
            "tactical_matchup_weight": 0.30
        })
        
        w_base = weights["baseline_weight"]
        w_m = weights["manager_form_weight"]
        w_t = weights["tactical_matchup_weight"]

        # Blending multipliers:
        # We blend the baseline probability with manager strength and tactical modifiers
        adj_score_h = p_base_h * (w_base + w_m * m_factor_h + w_t * t_mod_h)
        adj_score_a = p_base_a * (w_base + w_m * m_factor_a + w_t * t_mod_a)
        adj_score_d = p_base_d * (w_base + w_m * 1.0 + w_t * 1.0) # Draw remains anchored to baseline

        # Apply host nation multiplier if one of the teams is the tournament host
        if is_host_nation:
            if is_host_nation == "home" or is_host_nation == home_team:
                adj_score_h *= 1.07
            elif is_host_nation == "away" or is_host_nation == away_team:
                adj_score_a *= 1.07

        # Normalize to ensure probabilities sum to 1.0
        score_sum = adj_score_h + adj_score_d + adj_score_a
        p_final_h = adj_score_h / score_sum
        p_final_d = adj_score_d / score_sum
        p_final_a = adj_score_a / score_sum

        return {
            "probabilities": {
                "home": round(p_final_h, 4),
                "draw": round(p_final_d, 4),
                "away": round(p_final_a, 4)
            },
            "manager_stats": {
                "home": home_m_data,
                "away": away_m_data
            },
            "tactical_multipliers": {
                "home": round(t_mod_h, 2),
                "away": round(t_mod_a, 2)
            }
        }

    def calculate_advancement_probability(self, home_prob_90, draw_prob_90, away_prob_90, home_et_pso_weight=0.5, away_et_pso_weight=0.5):
        """
        Calculates the synthetic advancement (qualification) probabilities for Home and Away teams.
        Q_H = P(Win_90, H) + P(Draw_90) * P(Win_ET_PSO, H)
        Q_A = P(Win_90, A) + P(Draw_90) * P(Win_ET_PSO, A)
        """
        total_weight = home_et_pso_weight + away_et_pso_weight
        if total_weight > 0:
            p_et_pso_h = home_et_pso_weight / total_weight
            p_et_pso_a = away_et_pso_weight / total_weight
        else:
            p_et_pso_h = 0.5
            p_et_pso_a = 0.5
            
        q_h = home_prob_90 + draw_prob_90 * p_et_pso_h
        q_a = away_prob_90 + draw_prob_90 * p_et_pso_a
        
        return {
            "home": round(q_h, 4),
            "away": round(q_a, 4)
        }

    def calculate_goals_ou_probability(self, home_team, away_team, bookmaker_odds=None):
        """
        Calculates Goals Over/Under 2.5 probabilities.
        Scales with the sum of both teams' Total Shots Attempted.
        """
        home_stats = self.fbref_scraper.scrape_team_advanced_stats(home_team)
        away_stats = self.fbref_scraper.scrape_team_advanced_stats(away_team)
        
        home_shots = home_stats.get("total_shots_attempted_rolling_avg", 12.0)
        away_shots = away_stats.get("total_shots_attempted_rolling_avg", 12.0)
        shots_sum = home_shots + away_shots
        
        p_base_over = 0.50
        p_base_under = 0.50
        if bookmaker_odds and "over" in bookmaker_odds and "under" in bookmaker_odds:
            de_juiced = calculate_true_probabilities(bookmaker_odds)
            p_base_over = de_juiced.get("over", 0.50)
            p_base_under = de_juiced.get("under", 0.50)
            
        shots_factor = shots_sum / 27.0
        
        adj_over = p_base_over * shots_factor
        adj_under = p_base_under / shots_factor
        
        total = adj_over + adj_under
        p_final_over = adj_over / total if total > 0 else 0.50
        p_final_under = adj_under / total if total > 0 else 0.50
        
        return {
            "over": round(p_final_over, 4),
            "under": round(p_final_under, 4),
            "stats": {
                "home_shots": home_shots,
                "away_shots": away_shots,
                "shots_sum": shots_sum
            }
        }

    def calculate_btts_probability(self, home_team, away_team, bookmaker_odds=None):
        """
        Calculates Both Teams to Score (BTTS) Yes/No probabilities.
        Scales with the sum of both teams' Crosses into the Penalty Area.
        """
        home_stats = self.fbref_scraper.scrape_team_advanced_stats(home_team)
        away_stats = self.fbref_scraper.scrape_team_advanced_stats(away_team)
        
        home_crosses = home_stats.get("crosses_in_penalty_area_rolling_avg", 10.0)
        away_crosses = away_stats.get("crosses_in_penalty_area_rolling_avg", 10.0)
        crosses_sum = home_crosses + away_crosses
        
        p_base_yes = 0.52
        p_base_no = 0.48
        if bookmaker_odds and "yes" in bookmaker_odds and "no" in bookmaker_odds:
            de_juiced = calculate_true_probabilities(bookmaker_odds)
            p_base_yes = de_juiced.get("yes", 0.52)
            p_base_no = de_juiced.get("no", 0.48)
            
        crosses_factor = crosses_sum / 23.0
        
        adj_yes = p_base_yes * crosses_factor
        adj_no = p_base_no / crosses_factor
        
        total = adj_yes + adj_no
        p_final_yes = adj_yes / total if total > 0 else 0.50
        p_final_no = adj_no / total if total > 0 else 0.50
        
        return {
            "yes": round(p_final_yes, 4),
            "no": round(p_final_no, 4),
            "stats": {
                "home_crosses": home_crosses,
                "away_crosses": away_crosses,
                "crosses_sum": crosses_sum
            }
        }

    def calculate_corners_probability(self, home_team, away_team, bookmaker_odds=None):
        """
        Calculates Corners Over/Under probabilities for a given line.
        Scales inversely with average PPDA.
        """
        home_stats = self.fbref_scraper.scrape_team_advanced_stats(home_team)
        away_stats = self.fbref_scraper.scrape_team_advanced_stats(away_team)
        
        home_ppda = home_stats.get("ppda_rolling_avg", 11.5)
        away_ppda = away_stats.get("ppda_rolling_avg", 11.5)
        ppda_avg = (home_ppda + away_ppda) / 2.0
        
        p_base_over = 0.50
        p_base_under = 0.50
        if bookmaker_odds and "over" in bookmaker_odds and "under" in bookmaker_odds:
            de_juiced = calculate_true_probabilities(bookmaker_odds)
            p_base_over = de_juiced.get("over", 0.50)
            p_base_under = de_juiced.get("under", 0.50)
            
        ppda_factor = 11.0 / ppda_avg if ppda_avg > 0 else 1.0
        
        adj_over = p_base_over * ppda_factor
        adj_under = p_base_under / ppda_factor
        
        total = adj_over + adj_under
        p_final_over = adj_over / total if total > 0 else 0.50
        p_final_under = adj_under / total if total > 0 else 0.50
        
        return {
            "over": round(p_final_over, 4),
            "under": round(p_final_under, 4),
            "stats": {
                "home_ppda": home_ppda,
                "away_ppda": away_ppda,
                "ppda_avg": ppda_avg
            }
        }

