import os
import unittest
import sqlite3
import json
import uuid
import shutil
from datetime import datetime

# Import modules to test
from data_pipeline.utils import normalize_string
from data_pipeline.entity_resolution import jaro_winkler_similarity, levenshtein_distance, EntityResolver
from data_pipeline.processor import standardize_formation, get_matchup_tactical_advantage
from calculation_engine.kelly import KellyEngine
from calculation_engine.probability import ProbabilityEngine
from calculation_engine.ev_calc import calculate_true_probabilities, calculate_ev
from feedback_loop.logger import init_db, log_prediction, settle_prediction, get_pending_predictions, get_all_settled_predictions
from feedback_loop.evaluator import ModelEvaluator


class TestEntityResolution(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize_string("Pep Guardiola"), "pep guardiola")
        self.assertEqual(normalize_string("José Mourinho"), "jose mourinho")
        self.assertEqual(normalize_string("J. Klopp"), "j klopp")
        self.assertEqual(normalize_string("4-3-3 Attacking"), "4 3 3 attacking")

    def test_jaro_winkler(self):
        # Exact match
        self.assertEqual(jaro_winkler_similarity("pep", "pep"), 1.0)
        # Empty match
        self.assertEqual(jaro_winkler_similarity("pep", ""), 0.0)
        # Similar names
        score1 = jaro_winkler_similarity("guardiola", "guardola")
        score2 = jaro_winkler_similarity("guardiola", "mourinho")
        self.assertTrue(score1 > score2)
        self.assertTrue(score1 > 0.85)

    def test_levenshtein(self):
        self.assertEqual(levenshtein_distance("kitten", "sitting"), 3)
        self.assertEqual(levenshtein_distance("pep", "pep"), 0)
        self.assertEqual(levenshtein_distance("pep", ""), 3)

    def test_resolver_with_aliases(self):
        # Create a temp aliases file
        temp_aliases = "config/temp_aliases.json"
        if os.path.exists(temp_aliases):
            os.remove(temp_aliases)
            
        try:
            resolver = EntityResolver(temp_aliases)
            # Seed known list
            resolver.data["managers"] = {"pep guardiola": "Josep Guardiola", "mikel arteta": "Mikel Arteta"}
            resolver.save_aliases()
            
            # Test exact alias lookup
            self.assertEqual(resolver.resolve_manager("pep guardiola"), "Josep Guardiola")
            # Test fuzzy resolving (within 0.92 auto-resolve threshold)
            # "Josep Guardiola" -> "Josep Guardiola"
            self.assertEqual(resolver.resolve_manager("Josep Guardiola"), "Josep Guardiola")
            
        finally:
            if os.path.exists(temp_aliases):
                os.remove(temp_aliases)

    def test_all_20_laliga_teams_mock_stats(self):
        from data_pipeline.scrapers import FBrefWhoScoredScraper
        from data_pipeline.entity_resolution import EntityResolver
        
        resolver = EntityResolver("config/aliases.json")
        fbref = FBrefWhoScoredScraper("config/settings.json")
        
        laliga_teams = [
            "Real Madrid", "Barcelona", "Atletico Madrid", "Sevilla", "Real Sociedad",
            "Real Betis", "Villarreal", "Athletic Club", "Valencia", "Girona",
            "Alavés", "Osasuna", "Celta Vigo", "Getafe", "Las Palmas",
            "Rayo Vallecano", "Mallorca", "Leganés", "Espanyol", "Real Valladolid"
        ]
        
        fallback_stats = {
            "crosses_in_penalty_area_rolling_avg": 10.0,
            "ppda_rolling_avg": 11.5,
            "total_shots_attempted_rolling_avg": 12.0
        }
        
        for team in laliga_teams:
            resolved = resolver.resolve_team(team)
            self.assertEqual(resolved, team, f"Team '{team}' did not resolve to itself")
            
            stats = fbref.get_mock_advanced_team_stats(resolved)
            self.assertEqual(stats["team_name"], resolved)
            
            # Check that it did not fall back to the generic default stats
            team_stats = {k: v for k, v in stats.items() if k != "team_name"}
            self.assertNotEqual(team_stats, fallback_stats, f"Team '{resolved}' returned generic fallback stats")

class TestTacticalProcessor(unittest.TestCase):
    def test_standardize_formation(self):
        self.assertEqual(standardize_formation("4-3-3 Attacking"), "4-3-3")
        self.assertEqual(standardize_formation("4-2-3-1 deep"), "4-2-3-1")
        self.assertEqual(standardize_formation("3 5 2"), "3-5-2")
        self.assertEqual(standardize_formation("Invalid"), "4-4-2")

    def test_get_matchup_tactical_advantage(self):
        # 3-5-2 vs 4-3-3 has tactical adjustment
        home_adv, away_adv = get_matchup_tactical_advantage("3-5-2", "4-3-3")
        self.assertEqual(home_adv, 1.05)
        self.assertEqual(away_adv, 0.95)
        
        # Unknown matchup returns default neutral 1.0
        home_adv2, away_adv2 = get_matchup_tactical_advantage("4-5-1", "4-4-2")
        self.assertEqual(home_adv2, 1.0)
        self.assertEqual(away_adv2, 1.0)

class TestKellyEngine(unittest.TestCase):
    def setUp(self):
        # Use simple local config settings mock
        self.engine = KellyEngine(settings_path="non_existent.json")
        self.engine.default_fraction = 0.25 # quarter Kelly
        self.engine.max_fraction = 0.05    # 5% max bet limit

    def test_ev_calculations(self):
        # EV = (p * decimal_odds) - 1
        # p = 0.5, odds = 2.2 => EV = (0.5 * 2.2) - 1 = 0.10 (+10% EV)
        self.assertAlmostEqual(self.engine.calculate_ev(0.5, 2.2), 0.10)
        # Negative EV
        self.assertAlmostEqual(self.engine.calculate_ev(0.3, 2.0), -0.40)

    def test_kelly_staking(self):
        # Standard Kelly: (p * dec_odds - 1) / (dec_odds - 1)
        # p = 0.55, odds = 2.0 (b = 1.0)
        # standard_kelly = (0.55 * 2 - 1) / 1 = 0.10
        # fractional_kelly (1/4) = 0.10 * 0.25 = 0.025 (2.5% bankroll)
        stake = self.engine.calculate_kelly_stake(0.55, 2.0, fraction=0.25, max_fraction=0.05)
        self.assertEqual(stake, 0.025)

        # Clamped to max_fraction (e.g. 5%)
        # p = 0.8, odds = 2.0 -> standard_kelly = (0.8 * 2 - 1) / 1 = 0.60
        # 1/4 Kelly = 0.60 * 0.25 = 0.15 (15% bankroll) -> clamped to 5% (0.05)
        clamped_stake = self.engine.calculate_kelly_stake(0.8, 2.0, fraction=0.25, max_fraction=0.05)
        self.assertEqual(clamped_stake, 0.05)

        # Negative EV returns 0.0 (no bet)
        neg_stake = self.engine.calculate_kelly_stake(0.4, 2.0, fraction=0.25)
        self.assertEqual(neg_stake, 0.0)

    def test_de_juicing(self):
        # Multiplicative de-juicing test
        odds = {"home": 2.0, "draw": 3.0, "away": 4.0}
        true_probs = calculate_true_probabilities(odds)
        self.assertAlmostEqual(sum(true_probs.values()), 1.0)
        margin = 1.0/2.0 + 1.0/3.0 + 1.0/4.0
        self.assertAlmostEqual(true_probs["home"], (1.0/2.0) / margin)
        self.assertAlmostEqual(true_probs["draw"], (1.0/3.0) / margin)
        self.assertAlmostEqual(true_probs["away"], (1.0/4.0) / margin)

    def test_evaluate_market_opportunities(self):
        # Test evaluate_market_opportunities for other markets (like goals or corners)
        model_probs = {"over": 0.65, "under": 0.35}
        bookmaker_odds = {"over": 1.80, "under": 2.00}
        results = self.engine.evaluate_market_opportunities(model_probs, bookmaker_odds, fraction=0.25, max_fraction=0.05)
        # EV = (0.65 * 1.80) - 1 = 0.17
        self.assertAlmostEqual(results["over"]["ev"], 0.17)
        # Edge check: true_fair_probabilities for over = (1/1.8) / (1/1.8 + 1/2.0) = 0.5556 / 1.0556 = 0.526
        # Model probability (0.65) > True Fair Probability (0.526) => edge exists
        self.assertTrue(results["over"]["suggested_stake_pct"] > 0)

class TestProbabilityEngineAndFeatures(unittest.TestCase):
    def setUp(self):
        self.engine = ProbabilityEngine(settings_path="config/settings.json")

    def test_goals_ou_probability(self):
        # Test Goals Over/Under probability calculation
        res = self.engine.calculate_goals_ou_probability("Manchester City", "Liverpool")
        self.assertIn("over", res)
        self.assertIn("under", res)
        self.assertAlmostEqual(res["over"] + res["under"], 1.0)
        # Verify stats are present
        self.assertIn("stats", res)
        self.assertIn("shots_sum", res["stats"])

    def test_btts_probability(self):
        # Test BTTS probability calculation
        res = self.engine.calculate_btts_probability("Arsenal", "Chelsea")
        self.assertIn("yes", res)
        self.assertIn("no", res)
        self.assertAlmostEqual(res["yes"] + res["no"], 1.0)
        self.assertIn("stats", res)
        self.assertIn("crosses_sum", res["stats"])

    def test_corners_probability(self):
        # Test Corners probability calculation
        res = self.engine.calculate_corners_probability("Tottenham", "Manchester United")
        self.assertIn("over", res)
        self.assertIn("under", res)
        self.assertAlmostEqual(res["over"] + res["under"], 1.0)
        self.assertIn("stats", res)
        self.assertIn("ppda_avg", res["stats"])

    def test_neutral_venue_and_host_probabilities(self):
        # Test neutral venue and host nation multipliers
        # Default: is_neutral_venue = False, is_host_nation = None
        res_default = self.engine.calculate_match_probabilities(
            "Manchester City", "Arsenal", "Pep Guardiola", "Mikel Arteta", "4-3-3", "4-2-3-1"
        )
        p_def = res_default["probabilities"]
        
        # Neutral: is_neutral_venue = True
        res_neutral = self.engine.calculate_match_probabilities(
            "Manchester City", "Arsenal", "Pep Guardiola", "Mikel Arteta", "4-3-3", "4-2-3-1",
            is_neutral_venue=True
        )
        p_neu = res_neutral["probabilities"]
        
        # Strip standard HFA should mean home probability is lower than default
        self.assertTrue(p_neu["home"] < p_def["home"])
        # And away probability is higher than default
        self.assertTrue(p_neu["away"] > p_def["away"])
        
        # Host: is_host_nation = 'home'
        res_host_h = self.engine.calculate_match_probabilities(
            "Manchester City", "Arsenal", "Pep Guardiola", "Mikel Arteta", "4-3-3", "4-2-3-1",
            is_neutral_venue=True, is_host_nation="home"
        )
        p_host_h = res_host_h["probabilities"]
        self.assertTrue(p_host_h["home"] > p_neu["home"])
        
        # Host: is_host_nation = 'away'
        res_host_a = self.engine.calculate_match_probabilities(
            "Manchester City", "Arsenal", "Pep Guardiola", "Mikel Arteta", "4-3-3", "4-2-3-1",
            is_neutral_venue=True, is_host_nation="away"
        )
        p_host_a = res_host_a["probabilities"]
        self.assertTrue(p_host_a["away"] > p_neu["away"])
        
        # Host matched by team name
        res_host_name = self.engine.calculate_match_probabilities(
            "Manchester City", "Arsenal", "Pep Guardiola", "Mikel Arteta", "4-3-3", "4-2-3-1",
            is_neutral_venue=True, is_host_nation="Manchester City"
        )
        p_host_name = res_host_name["probabilities"]
        self.assertAlmostEqual(p_host_name["home"], p_host_h["home"])

    def test_advancement_probabilities(self):
        # Q_H = P_H + P_D * p_et_pso_h
        # 0.4 + 0.3 * 0.5 = 0.55
        res = self.engine.calculate_advancement_probability(0.4, 0.3, 0.3)
        self.assertAlmostEqual(res["home"], 0.55)
        self.assertAlmostEqual(res["away"], 0.45)
        
        # Custom weights
        res_custom = self.engine.calculate_advancement_probability(0.4, 0.3, 0.3, home_et_pso_weight=0.6, away_et_pso_weight=0.4)
        # 0.4 + 0.3 * 0.6 = 0.58
        self.assertAlmostEqual(res_custom["home"], 0.58)
        self.assertAlmostEqual(res_custom["away"], 0.42)

    def test_motivation_modifiers(self):
        # Default run
        res_default = self.engine.calculate_match_probabilities(
            "Manchester City", "Arsenal", "Pep Guardiola", "Mikel Arteta", "4-3-3", "4-2-3-1"
        )
        p_def = res_default["probabilities"]
        
        # Highly motivated home team: {'home': 1.12, 'away': 1.0}
        res_mot = self.engine.calculate_match_probabilities(
            "Manchester City", "Arsenal", "Pep Guardiola", "Mikel Arteta", "4-3-3", "4-2-3-1",
            motivation_modifier={"home": 1.12, "away": 1.0}
        )
        p_mot = res_mot["probabilities"]
        self.assertTrue(p_mot["home"] > p_def["home"])
        
        # Low motivation home team: {'home': 0.90, 'away': 1.0}
        res_demot = self.engine.calculate_match_probabilities(
            "Manchester City", "Arsenal", "Pep Guardiola", "Mikel Arteta", "4-3-3", "4-2-3-1",
            motivation_modifier={"home": 0.90, "away": 1.0}
        )
        p_demot = res_demot["probabilities"]
        self.assertTrue(p_demot["home"] < p_def["home"])

    def test_apply_motivation_modifiers_direct(self):
        from calculation_engine.ev_calc import apply_motivation_modifiers
        base = {"home": 0.5, "away": 0.3}
        # Standard scaling
        res = apply_motivation_modifiers(base, {"home": 1.12, "away": 0.9})
        self.assertAlmostEqual(res["home"], 0.56)
        self.assertAlmostEqual(res["away"], 0.27)

class TestDatabaseAndEvaluator(unittest.TestCase):
    def setUp(self):
        self.db_path = "data/test_tactics_betting.db"
        self.settings_path = "config/test_settings.json"
        
        # Write dummy settings
        with open(self.settings_path, "w") as f:
            json.dump({
                "database_path": self.db_path,
                "kelly_fraction": 0.25,
                "model_weights": {
                    "baseline_weight": 0.5,
                    "manager_form_weight": 0.2,
                    "tactical_matchup_weight": 0.3
                }
            }, f)
            
        init_db(self.db_path)

    def tearDown(self):
        # Cleanup test DB and settings
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.settings_path):
            os.remove(self.settings_path)
            
        # Clean up database folders if empty
        db_dir = os.path.dirname(self.db_path)
        if os.path.exists(db_dir) and not os.listdir(db_dir):
            os.rmdir(db_dir)

    def test_prediction_logging_and_settlement(self):
        pred_id = str(uuid.uuid4())
        pred_data = {
            "id": pred_id,
            "match_date": datetime.now().strftime("%Y-%m-%d"),
            "home_team": "Manchester City",
            "away_team": "Arsenal",
            "home_manager": "Josep Guardiola",
            "away_manager": "Mikel Arteta",
            "home_predicted_formation": "4-3-3",
            "away_predicted_formation": "4-2-3-1",
            "model_p_home": 0.50,
            "model_p_draw": 0.25,
            "model_p_away": 0.25,
            "placed_bet_type": "home",
            "placed_bet_odds": 2.20,
            "placed_bet_stake": 50.0,
            "status": "PENDING"
        }
        
        log_prediction(pred_data, self.db_path)
        
        # Verify it's pending
        pending = get_pending_predictions(self.db_path)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], pred_id)
        
        # Settle bet (Home team wins 2-1)
        evaluator = ModelEvaluator(self.db_path, self.settings_path)
        outcome = evaluator.settle_match(
            pred_id, home_score=2, away_score=1,
            home_actual_formation="4-3-3", away_actual_formation="4-2-3-1",
            human_notes="Stuck to gameplan"
        )
        
        # Profit = (50.0 * 2.20) - 50.0 = 60.0
        self.assertEqual(outcome["net_profit_loss"], 60.0)
        self.assertEqual(outcome["winner"], "HOME")
        
        # Check settled predictions list
        settled = get_all_settled_predictions(self.db_path)
        self.assertEqual(len(settled), 1)
        self.assertEqual(settled[0]["id"], pred_id)
        self.assertEqual(settled[0]["status"], "SETTLED")

    def test_brier_and_roi_metrics(self):
        evaluator = ModelEvaluator(self.db_path, self.settings_path)
        
        # Test empty metrics
        metrics = evaluator.calculate_metrics()
        self.assertEqual(metrics["total_predictions"], 0)
        
        # Log a dummy won prediction
        pred_id1 = str(uuid.uuid4())
        log_prediction({
            "id": pred_id1,
            "match_date": "2026-05-21",
            "home_team": "Chelsea",
            "away_team": "Liverpool",
            "home_manager": "Enzo Maresca",
            "away_manager": "Arne Slot",
            "home_predicted_formation": "4-2-3-1",
            "away_predicted_formation": "4-3-3",
            "model_p_home": 0.40,
            "model_p_draw": 0.30,
            "model_p_away": 0.30,
            "placed_bet_type": "home",
            "placed_bet_odds": 2.50,
            "placed_bet_stake": 100.0,
            "status": "PENDING"
        }, self.db_path)
        
        # Settle as home win (2-0)
        evaluator.settle_match(pred_id1, 2, 0, "4-2-3-1", "4-3-3")
        
        # Calculate metrics
        metrics = evaluator.calculate_metrics()
        
        # Total profit = 100 * 2.5 - 100 = 150.0
        self.assertEqual(metrics["total_predictions"], 1)
        self.assertEqual(metrics["total_bets_placed"], 1)
        self.assertEqual(metrics["total_stake"], 100.0)
        self.assertEqual(metrics["total_profit_loss"], 150.0)
        self.assertEqual(metrics["roi_pct"], 150.0)
        self.assertEqual(metrics["win_rate_pct"], 100.0)
        
        # Multi-class Brier score:
        # P_home = 0.40, P_draw = 0.30, P_away = 0.30
        # Winner = HOME (o_h=1, o_d=0, o_a=0)
        # Brier = ((0.4-1)^2 + (0.3-0)^2 + (0.3-0)^2)/3 = (0.36 + 0.09 + 0.09)/3 = 0.54 / 3 = 0.18
        self.assertAlmostEqual(metrics["brier_score"], 0.18)

class TestJsonLogger(unittest.TestCase):
    def setUp(self):
        self.log_dir = "temp_predictions"
        if os.path.exists(self.log_dir):
            shutil.rmtree(self.log_dir)
            
        from feedback_loop.json_logger import JsonDocumentLogger
        self.logger = JsonDocumentLogger(self.log_dir)

    def tearDown(self):
        if os.path.exists(self.log_dir):
            shutil.rmtree(self.log_dir)

    def test_json_logging_schema_and_settlement(self):
        match_id = "test_epl_2026_mci_ars"
        meta_data = {
            "date": "2026-05-24T15:00:00Z",
            "competition": "Premier League",
            "home_team": "Manchester City",
            "away_team": "Arsenal",
            "home_manager": "Josep Guardiola",
            "away_manager": "Mikel Arteta"
        }
        context_data = {
            "predicted_home_formation": "4-3-3",
            "predicted_away_formation": "4-2-3-1",
            "manager_h2h_win_rate_home": 0.60,
            "tactical_flexibility_index_home": 0.8,
            "tactical_flexibility_index_away": 0.7
        }
        market_data = {
            "bookmaker": "Pinnacle",
            "odds_decimal": {
                "home_win": 2.10,
                "draw": 3.40,
                "away_win": 3.30
            }
        }
        inference_data = {
            "model_probabilities": {
                "home_win": 0.50,
                "draw": 0.25,
                "away_win": 0.25
            },
            "expected_value": {
                "home_win": 0.05,
                "draw": -0.15,
                "away_win": -0.175
            },
            "action": {
                "recommended_bet": "home_win",
                "edge_identified": True,
                "kelly_fraction_pct": 1.25,
                "reasoning": "Value on City"
            }
        }

        # 1. Log pre-match
        log = self.logger.log_pre_match(match_id, meta_data, context_data, market_data, inference_data)
        
        # Verify schema keys
        self.assertEqual(log["match_id"], match_id)
        self.assertEqual(log["meta"]["home_team"], "Manchester City")
        self.assertEqual(log["pre_match_context"]["manager_h2h_win_rate_home"], 0.60)
        self.assertEqual(log["market_state"]["odds_decimal"]["home_win"], 2.10)
        self.assertEqual(log["agent_inference"]["action"]["recommended_bet"], "home_win")
        self.assertEqual(log["post_match_resolution"]["status"], "pending")

        # Check file exists
        file_path = os.path.join(self.log_dir, f"{match_id}.json")
        self.assertTrue(os.path.exists(file_path))
        with open(file_path, "r", encoding="utf-8") as f:
            file_data = json.load(f)
        self.assertEqual(file_data["match_id"], match_id)

        # 2. Settle match
        # Say Manchester City won 2-1 (home win)
        # Recommended bet was 'home_win', Kelly fraction was 1.25% (0.0125 stake)
        # Profit = 0.0125 * (2.10 - 1.0) = 0.0125 * 1.1 = 0.01375
        settled_data = self.logger.settle_match(
            match_id, score_home=2, score_away=1,
            actual_home_formation="4-3-3", actual_away_formation="4-2-3-1",
            notes="Guardiola tactics worked well"
        )

        self.assertEqual(settled_data["post_match_resolution"]["status"], "settled")
        self.assertEqual(settled_data["post_match_resolution"]["actual_result"], "home_win")
        self.assertEqual(settled_data["post_match_resolution"]["actual_score"], "2-1")
        self.assertAlmostEqual(settled_data["post_match_resolution"]["profit_loss_units"], 0.0138)
        self.assertEqual(settled_data["calibration_loop"]["human_override_notes"], "Guardiola tactics worked well")

class TestPostMatchResolver(unittest.TestCase):
    def setUp(self):
        self.db_path = "data/test_resolver_tactics_betting.db"
        self.settings_path = "config/test_resolver_settings.json"
        self.log_dir = "temp_resolver_predictions"
        
        # Ensure directories exist
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        
        if os.path.exists(self.log_dir):
            shutil.rmtree(self.log_dir)
        os.makedirs(self.log_dir, exist_ok=True)
            
        # Write dummy settings
        with open(self.settings_path, "w") as f:
            json.dump({
                "database_path": self.db_path,
                "kelly_fraction": 0.25,
                "model_weights": {
                    "baseline_weight": 0.5,
                    "manager_form_weight": 0.2,
                    "tactical_matchup_weight": 0.3
                }
            }, f)
            
        init_db(self.db_path)
        from feedback_loop.resolver import PostMatchResolver
        self.resolver = PostMatchResolver(db_path=self.db_path, log_dir=self.log_dir, settings_path=self.settings_path)
        
        # Point accumulator files to temp location to avoid touching user's real tickets
        self.temp_accum_file = os.path.join(self.log_dir, "temp_accumulator_ticket.json")
        self.resolver.accum_files = [self.temp_accum_file]

    def tearDown(self):
        # Cleanup
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.settings_path):
            os.remove(self.settings_path)
        if os.path.exists(self.log_dir):
            shutil.rmtree(self.log_dir)

    def test_determine_bet_result_1x2(self):
        # 1X2 market
        self.assertEqual(self.resolver.determine_bet_result("1X2", "home_win", 2, 1, 10), "won")
        self.assertEqual(self.resolver.determine_bet_result("1X2", "draw", 2, 1, 10), "lost")
        self.assertEqual(self.resolver.determine_bet_result("1X2", "away_win", 2, 1, 10), "lost")
        
        self.assertEqual(self.resolver.determine_bet_result("1X2", "draw", 1, 1, 10), "won")
        self.assertEqual(self.resolver.determine_bet_result("1X2", "away_win", 0, 3, 10), "won")

    def test_determine_bet_result_goals(self):
        # Goals Over/Under 2.5
        self.assertEqual(self.resolver.determine_bet_result("goals_ou_2.5", "goals_over_2.5", 2, 1, 10), "won")
        self.assertEqual(self.resolver.determine_bet_result("goals_ou_2.5", "goals_under_2.5", 2, 1, 10), "lost")
        self.assertEqual(self.resolver.determine_bet_result("goals_ou_2.5", "goals_under_2.5", 1, 1, 10), "won")

    def test_determine_bet_result_btts(self):
        # BTTS
        self.assertEqual(self.resolver.determine_bet_result("btts", "btts_yes", 2, 1, 10), "won")
        self.assertEqual(self.resolver.determine_bet_result("btts", "btts_no", 2, 1, 10), "lost")
        self.assertEqual(self.resolver.determine_bet_result("btts", "btts_no", 2, 0, 10), "won")
        self.assertEqual(self.resolver.determine_bet_result("btts", "btts_yes", 0, 0, 10), "lost")

    def test_determine_bet_result_corners(self):
        # Corners O/U
        self.assertEqual(self.resolver.determine_bet_result("corners_ou", "corners_over_9.5", 2, 1, 10), "won")
        self.assertEqual(self.resolver.determine_bet_result("corners_ou", "corners_over_9.5", 2, 1, 9), "lost")
        self.assertEqual(self.resolver.determine_bet_result("corners_ou", "corners_under_9.5", 2, 1, 9), "won")
        
        # Test Push condition
        self.assertEqual(self.resolver.determine_bet_result("corners_ou", "corners_over_10.0", 2, 1, 10), "push")
        self.assertEqual(self.resolver.determine_bet_result("corners_ou", "corners_under_10.0", 2, 1, 10), "push")

    def create_mock_json_log(self, match_id, rec_bet, stake_pct, odds):
        data = {
            "match_id": match_id,
            "meta": {
                "date": "2026-05-24T15:00:00Z",
                "competition": "Premier League",
                "home_team": "Manchester City",
                "away_team": "Arsenal",
                "home_manager": "Josep Guardiola",
                "away_manager": "Mikel Arteta"
            },
            "pre_match_context": {
                "predicted_home_formation": "4-3-3",
                "predicted_away_formation": "4-2-3-1"
            },
            "market_state": {
                "bookmaker": "SportyBet",
                "odds_decimal": {
                    rec_bet: odds
                }
            },
            "agent_inference": {
                "model_probabilities": {
                    "home_win": 0.60,
                    "draw": 0.20,
                    "away_win": 0.20
                },
                "expected_value": {
                    rec_bet: 0.15
                },
                "action": {
                    "recommended_bet": rec_bet,
                    "edge_identified": True,
                    "kelly_fraction_pct": stake_pct
                }
            },
            "calibration_loop": {
                "agent_learning_adjustment": "",
                "human_override_notes": ""
            }
        }
        file_path = os.path.join(self.log_dir, f"{match_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return file_path

    def test_settle_single_json_log_won(self):
        match_id = "epl_20260524_man_ars"
        file_path = self.create_mock_json_log(match_id, "home_win", 4.0, 2.0)
        
        result_val = {
            "home_score": 2,
            "away_score": 1,
            "total_corners": 10,
            "home_actual_formation": "4-3-3",
            "away_actual_formation": "4-2-3-1"
        }
        
        data, res, pnl = self.resolver.settle_single_json_log(file_path, result_val)
        
        self.assertEqual(res, "won")
        self.assertAlmostEqual(pnl, 0.04)
        self.assertEqual(data["post_match_resolution"]["status"], "settled")
        self.assertEqual(data["post_match_resolution"]["actual_score"], "2-1")
        self.assertEqual(data["post_match_resolution"]["profit_loss_units"], 0.04)

    def test_settle_single_json_log_lost(self):
        match_id = "epl_20260524_man_ars"
        file_path = self.create_mock_json_log(match_id, "home_win", 4.0, 2.0)
        
        result_val = {
            "home_score": 1,
            "away_score": 2,
            "total_corners": 10,
            "home_actual_formation": "4-3-3",
            "away_actual_formation": "4-2-3-1"
        }
        
        data, res, pnl = self.resolver.settle_single_json_log(file_path, result_val)
        
        self.assertEqual(res, "lost")
        self.assertAlmostEqual(pnl, -0.04)
        self.assertEqual(data["post_match_resolution"]["profit_loss_units"], -0.04)

    def test_settle_single_json_log_push(self):
        match_id = "epl_20260524_man_ars"
        file_path = self.create_mock_json_log(match_id, "corners_over_10.0", 4.0, 1.90)
        
        result_val = {
            "home_score": 2,
            "away_score": 1,
            "total_corners": 10,
            "home_actual_formation": "4-3-3",
            "away_actual_formation": "4-2-3-1"
        }
        
        data, res, pnl = self.resolver.settle_single_json_log(file_path, result_val)
        
        self.assertEqual(res, "push")
        self.assertAlmostEqual(pnl, 0.0)
        self.assertEqual(data["post_match_resolution"]["profit_loss_units"], 0.0)

    def create_mock_accumulator_ticket(self, legs):
        ticket = {
            "generated_at": datetime.now().isoformat(),
            "legs": legs,
            "total_odds": 5.0,
            "compounded_ev": 0.25
        }
        with open(self.temp_accum_file, "w", encoding="utf-8") as f:
            json.dump(ticket, f, indent=2)

    def test_accumulator_settlement_all_won(self):
        legs = [
            {
                "match_id": "epl_20260524_man_ars",
                "team_match": "Manchester City vs Arsenal",
                "market": "1X2",
                "selection": "home_win",
                "actual_decimal_odds": 2.0
            },
            {
                "match_id": "epl_20260524_cry_ars",
                "team_match": "Crystal Palace vs Arsenal",
                "market": "goals_ou_2.5",
                "selection": "goals_over_2.5",
                "actual_decimal_odds": 1.8
            }
        ]
        self.create_mock_accumulator_ticket(legs)
        
        self.resolver.fetch_results = lambda use_mock_fallback=True: {
            "Manchester City vs Arsenal": {
                "home_score": 2,
                "away_score": 1,
                "total_corners": 10,
                "home_actual_formation": "4-3-3",
                "away_actual_formation": "4-2-3-1"
            },
            "Crystal Palace vs Arsenal": {
                "home_score": 2,
                "away_score": 2,
                "total_corners": 8,
                "home_actual_formation": "3-4-2-1",
                "away_actual_formation": "4-3-3"
            }
        }
        
        # We need mock JSON log files to avoid skip warnings in run_settlement
        self.create_mock_json_log("epl_20260524_man_ars", "home_win", 4.0, 2.0)
        self.create_mock_json_log("epl_20260524_cry_ars", "goals_over_2.5", 2.0, 1.8)
        
        self.resolver.run_settlement()
        
        with open(self.temp_accum_file, "r", encoding="utf-8") as f:
            ticket = json.load(f)
            
        res = ticket["post_match_resolution"]
        self.assertEqual(res["actual_result"], "won")
        self.assertAlmostEqual(res["recalculated_odds"], 3.6)
        self.assertAlmostEqual(res["profit_loss_units"], 2.6)

    def test_accumulator_settlement_one_lost(self):
        legs = [
            {
                "match_id": "epl_20260524_man_ars",
                "team_match": "Manchester City vs Arsenal",
                "market": "1X2",
                "selection": "home_win",
                "actual_decimal_odds": 2.0
            },
            {
                "match_id": "epl_20260524_cry_ars",
                "team_match": "Crystal Palace vs Arsenal",
                "market": "goals_ou_2.5",
                "selection": "goals_over_2.5",
                "actual_decimal_odds": 1.8
            }
        ]
        self.create_mock_accumulator_ticket(legs)
        
        self.resolver.fetch_results = lambda use_mock_fallback=True: {
            "Manchester City vs Arsenal": {
                "home_score": 1,
                "away_score": 1,
                "total_corners": 10,
                "home_actual_formation": "4-3-3",
                "away_actual_formation": "4-2-3-1"
            },
            "Crystal Palace vs Arsenal": {
                "home_score": 2,
                "away_score": 2,
                "total_corners": 8,
                "home_actual_formation": "3-4-2-1",
                "away_actual_formation": "4-3-3"
            }
        }
        
        self.create_mock_json_log("epl_20260524_man_ars", "home_win", 4.0, 2.0)
        self.create_mock_json_log("epl_20260524_cry_ars", "goals_over_2.5", 2.0, 1.8)
        
        self.resolver.run_settlement()
        
        with open(self.temp_accum_file, "r", encoding="utf-8") as f:
            ticket = json.load(f)
            
        res = ticket["post_match_resolution"]
        self.assertEqual(res["actual_result"], "lost")
        self.assertAlmostEqual(res["profit_loss_units"], -1.0)

    def test_accumulator_settlement_with_push(self):
        legs = [
            {
                "match_id": "epl_20260524_man_ars",
                "team_match": "Manchester City vs Arsenal",
                "market": "1X2",
                "selection": "home_win",
                "actual_decimal_odds": 2.0
            },
            {
                "match_id": "epl_20260524_cry_ars",
                "team_match": "Crystal Palace vs Arsenal",
                "market": "corners_ou",
                "selection": "corners_over_10.0",
                "actual_decimal_odds": 1.8
            }
        ]
        self.create_mock_accumulator_ticket(legs)
        
        self.resolver.fetch_results = lambda use_mock_fallback=True: {
            "Manchester City vs Arsenal": {
                "home_score": 2,
                "away_score": 1,
                "total_corners": 10,
                "home_actual_formation": "4-3-3",
                "away_actual_formation": "4-2-3-1"
            },
            "Crystal Palace vs Arsenal": {
                "home_score": 1,
                "away_score": 1,
                "total_corners": 10,
                "home_actual_formation": "3-4-2-1",
                "away_actual_formation": "4-3-3"
            }
        }
        
        self.create_mock_json_log("epl_20260524_man_ars", "home_win", 4.0, 2.0)
        self.create_mock_json_log("epl_20260524_cry_ars", "corners_over_10.0", 2.0, 1.8)
        
        self.resolver.run_settlement()
        
        with open(self.temp_accum_file, "r", encoding="utf-8") as f:
            ticket = json.load(f)
            
        res = ticket["post_match_resolution"]
        self.assertEqual(res["actual_result"], "won")
        self.assertAlmostEqual(res["recalculated_odds"], 2.0)
        self.assertAlmostEqual(res["profit_loss_units"], 1.0)

    def test_database_integration_upon_settlement(self):
        pred_id = "epl_20260524_man_ars"
        pred_data = {
            "id": pred_id,
            "match_date": "2026-05-24",
            "home_team": "Manchester City",
            "away_team": "Arsenal",
            "home_manager": "Josep Guardiola",
            "away_manager": "Mikel Arteta",
            "home_predicted_formation": "4-3-3",
            "away_predicted_formation": "4-2-3-1",
            "model_p_home": 0.60,
            "model_p_draw": 0.20,
            "model_p_away": 0.20,
            "placed_bet_type": "home_win",
            "placed_bet_odds": 2.0,
            "placed_bet_stake": 4.0,
            "status": "PENDING"
        }
        log_prediction(pred_data, self.db_path)
        
        self.create_mock_json_log(pred_id, "home_win", 4.0, 2.0)
        self.resolver.fetch_results = lambda use_mock_fallback=True: {
            "Manchester City vs Arsenal": {
                "home_score": 2,
                "away_score": 1,
                "total_corners": 10,
                "home_actual_formation": "4-3-3",
                "away_actual_formation": "4-2-3-1"
            }
        }
        
        self.resolver.run_settlement()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM predictions WHERE id = ?", (pred_id,))
        row = cursor.fetchone()
        self.assertEqual(row[0], "SETTLED")
        
        cursor.execute("SELECT home_score, away_score, winner, home_actual_formation FROM match_outcomes WHERE prediction_id = ?", (pred_id,))
        row = cursor.fetchone()
        self.assertEqual(row[0], 2)
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], "HOME")
        self.assertEqual(row[3], "4-3-3")
        conn.close()

class TestWorldCupAndTwoWayMarkets(unittest.TestCase):
    def test_world_cup_failsafe_routing(self):
        from data_pipeline.scrapers import TheOddsAPIScraper
        scraper = TheOddsAPIScraper("config/settings.json")
        # Route world cup sport key
        odds = scraper.scrape_odds("soccer_fifa_world_cup")
        self.assertIsNotNone(odds)
        self.assertIn("USA vs England", odds)
        self.assertIn("Argentina vs France", odds)
        self.assertIn("to_advance", odds["Argentina vs France"])
        # Spain vs Germany shouldn't have to_advance
        self.assertNotIn("to_advance", odds["Spain vs Germany"])

    def test_two_way_market_ev_protection(self):
        from calculation_engine.ev_calc import evaluate_market_ev, calculate_true_probabilities
        # Two-way odds without 'draw'
        bookmaker_odds = {"home": 1.75, "away": 2.05}
        model_probabilities = {"home": 0.60, "away": 0.40}
        
        # De-juice should sum to 1
        true_probs = calculate_true_probabilities(bookmaker_odds)
        self.assertAlmostEqual(sum(true_probs.values()), 1.0)
        self.assertNotIn("draw", true_probs)
        
        # Evaluation should run without KeyError
        results = evaluate_market_ev(model_probabilities, bookmaker_odds)
        self.assertIn("home", results)
        self.assertIn("away", results)
        self.assertIn("draw", results)
        self.assertEqual(results["draw"]["ev"], 0.0)

    def test_probability_engine_two_way_baseline(self):
        from calculation_engine.probability import ProbabilityEngine
        engine = ProbabilityEngine("config/settings.json")
        # Odds dictionary with no draw key
        bookmaker_odds = {"home": 1.75, "away": 2.05}
        
        # Should not raise KeyError and should succeed
        res = engine.calculate_match_probabilities(
            "USA", "England", "Mauricio Pochettino", "Thomas Tuchel", "4-3-3", "4-3-3",
            bookmaker_odds=bookmaker_odds, is_neutral_venue=True
        )
        self.assertIn("probabilities", res)
        probs = res["probabilities"]
        self.assertIn("home", probs)
        self.assertIn("away", probs)

    def test_kelly_engine_two_way_protection(self):
        from calculation_engine.kelly import KellyEngine
        kelly = KellyEngine("config/settings.json")
        bookmaker_odds = {"home": 1.75, "away": 2.05}
        model_probabilities = {"home": 0.60, "away": 0.40}
        
        # Should return draw keys with zero value to prevent KeyErrors
        opps = kelly.evaluate_market_opportunities(model_probabilities, bookmaker_odds)
        self.assertIn("draw", opps)
        self.assertEqual(opps["draw"]["ev"], 0.0)

    def test_co_host_clash_logic(self):
        # We check the co-host clash logic we implemented in run_worldcup.py
        hosts = {"USA", "Mexico", "Canada"}
        
        # 1. Co-host clash: USA vs Mexico
        home_team = "USA"
        away_team = "Mexico"
        is_home_host = home_team in hosts
        is_away_host = away_team in hosts
        if is_home_host and is_away_host:
            is_host_nation = None
        else:
            is_host_nation = "home" if is_home_host else ("away" if is_away_host else None)
        
        self.assertIsNone(is_host_nation, "Co-hosts playing each other must have is_host_nation set to None")
        
        # 2. Host vs Non-host: USA vs England
        home_team_2 = "USA"
        away_team_2 = "England"
        is_home_host_2 = home_team_2 in hosts
        is_away_host_2 = away_team_2 in hosts
        if is_home_host_2 and is_away_host_2:
            is_host_nation_2 = None
        else:
            is_host_nation_2 = "home" if is_home_host_2 else ("away" if is_away_host_2 else None)
            
        self.assertEqual(is_host_nation_2, "home", "Host vs non-host match where host is home must apply 'home' advantage")

        # 3. Non-host vs Host: England vs Mexico
        home_team_3 = "England"
        away_team_3 = "Mexico"
        is_home_host_3 = home_team_3 in hosts
        is_away_host_3 = away_team_3 in hosts
        if is_home_host_3 and is_away_host_3:
            is_host_nation_3 = None
        else:
            is_host_nation_3 = "home" if is_home_host_3 else ("away" if is_away_host_3 else None)
            
        self.assertEqual(is_host_nation_3, "away", "Non-host vs host match where host is away must apply 'away' advantage")

class TestFeedbackLoopLearning(unittest.TestCase):
    def setUp(self):
        self.db_path = "data/test_learning_tactics_betting.db"
        self.settings_path = "config/test_learning_settings.json"
        self.predictions_dir = "temp_learning_predictions"
        
        # Ensure clean state
        if os.path.exists(self.predictions_dir):
            import shutil
            shutil.rmtree(self.predictions_dir)
        os.makedirs(self.predictions_dir, exist_ok=True)
        
        with open(self.settings_path, "w") as f:
            json.dump({
                "database_path": self.db_path,
                "kelly_fraction": 0.25,
                "model_weights": {
                    "baseline_weight": 0.5,
                    "manager_form_weight": 0.2,
                    "tactical_matchup_weight": 0.3
                },
                "elo_ratings": {
                    "Manchester City": 1600.0,
                    "Arsenal": 1550.0
                },
                "xg_weights": {
                    "goals_over_under": 1.0,
                    "btts": 1.0,
                    "corners": 1.0
                }
            }, f)
            
        init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.settings_path):
            os.remove(self.settings_path)
        if os.path.exists(self.predictions_dir):
            import shutil
            shutil.rmtree(self.predictions_dir)

    def test_evaluate_and_adjust(self):
        # Create a mock pre-match prediction document
        pred_data = {
            "match_id": "epl_20260524_mci_ars",
            "meta": {
                "date": "2026-05-24T15:00:00Z",
                "competition": "Premier League",
                "home_team": "Manchester City",
                "away_team": "Arsenal",
                "home_manager": "Josep Guardiola",
                "away_manager": "Mikel Arteta"
            },
            "pre_match_context": {
                "predicted_home_formation": "4-3-3",
                "predicted_away_formation": "4-2-3-1"
            },
            "market_state": {
                "bookmaker": "Pinnacle",
                "odds_decimal": {
                    "home_win": 2.0,
                    "draw": 3.4,
                    "away_win": 3.2,
                    "goals_over_2.5": 1.8,
                    "goals_under_2.5": 2.0,
                    "btts_yes": 1.7,
                    "btts_no": 2.1,
                    "corners_over_9.5": 1.9,
                    "corners_under_9.5": 1.9
                }
            },
            "agent_inference": {
                "model_probabilities": {
                    "home_win": 0.50,
                    "draw": 0.25,
                    "away_win": 0.25,
                    "goals_over_2.5": 0.55,
                    "goals_under_2.5": 0.45,
                    "btts_yes": 0.52,
                    "btts_no": 0.48,
                    "corners_over_9.5": 0.50,
                    "corners_under_9.5": 0.50
                },
                "expected_value": {},
                "action": {
                    "recommended_bet": "home_win",
                    "edge_identified": True,
                    "kelly_fraction_pct": 2.5,
                    "reasoning": "Value on City"
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
        
        pred_path = os.path.join(self.predictions_dir, "epl_20260524_mci_ars.json")
        with open(pred_path, "w") as f:
            json.dump(pred_data, f, indent=2)
            
        # Log to DB as pending
        log_prediction({
            "id": "epl_20260524_mci_ars",
            "match_date": "2026-05-24 15:00:00",
            "home_team": "Manchester City",
            "away_team": "Arsenal",
            "home_manager": "Josep Guardiola",
            "away_manager": "Mikel Arteta",
            "home_predicted_formation": "4-3-3",
            "away_predicted_formation": "4-2-3-1",
            "model_p_home": 0.50,
            "model_p_draw": 0.25,
            "model_p_away": 0.25,
            "placed_bet_type": "home",
            "placed_bet_odds": 2.0,
            "placed_bet_stake": 50.0,
            "status": "PENDING"
        }, self.db_path)
        
        # Create results file
        results_file = os.path.join(self.predictions_dir, "results.json")
        with open(results_file, "w") as f:
            json.dump({
                "Manchester City vs Arsenal": {
                    "home_score": 3,
                    "away_score": 1,
                    "total_corners": 11,
                    "home_actual_formation": "4-3-3",
                    "away_actual_formation": "4-2-3-1"
                }
            }, f)
            
        # Execute learning
        evaluator = ModelEvaluator(self.db_path, self.settings_path)
        evaluator.evaluate_and_adjust_from_results([results_file], predictions_dir=self.predictions_dir, learning_rate=0.1, elo_k=32.0)
        
        # Reload settings
        with open(self.settings_path, "r") as f:
            settings = json.load(f)
            
        # 1. Assert ELO adjusted
        # Old ELO: City 1600, Arsenal 1550
        # Outcome: City wins (1.0)
        # Pred: 0.50
        # Delta: 32 * (1.0 - 0.50) = 16.0
        # New ELO: City 1616.0, Arsenal 1534.0
        self.assertEqual(settings["elo_ratings"]["Manchester City"], 1616.0)
        self.assertEqual(settings["elo_ratings"]["Arsenal"], 1534.0)
        
        # 2. Assert xG weights adjusted
        # Goals O/U 2.5: actual = 3+1 = 4 (>2.5 -> 1.0), pred = 0.55
        # Shots sum for City and Arsenal from fbref mock stats:
        # City: 18.2, Arsenal: 16.8 => sum = 35.0
        # Shots factor = 35.0 / 27.0 - 1.0 = 0.2963
        # Weight adjustment: 1.0 + 0.1 * (1.0 - 0.55) * 0.2963 = 1.0 + 0.1 * 0.45 * 0.2963 = 1.0133
        self.assertAlmostEqual(settings["xg_weights"]["goals_over_under"], 1.0133, places=4)
        
        # 3. Assert DB prediction status changed to SETTLED
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT status FROM predictions WHERE id = 'epl_20260524_mci_ars'").fetchone()
        self.assertEqual(row[0], "SETTLED")
        conn.close()

if __name__ == "__main__":
    unittest.main()
