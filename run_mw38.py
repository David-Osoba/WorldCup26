from data_pipeline.utils import load_dotenv
load_dotenv()

import os
import sys
import json
from datetime import datetime
from data_pipeline.scrapers import TheOddsAPIScraper
from data_pipeline.entity_resolution import EntityResolver
from calculation_engine.probability import ProbabilityEngine
from calculation_engine.kelly import KellyEngine
from calculation_engine.accumulator import build_premium_accumulator
from feedback_loop.logger import init_db, log_prediction
from feedback_loop.json_logger import JsonDocumentLogger

def main():
    print("=" * 80)
    print("                EPL Matchweek 38 Odds API Execution Script                  ")
    print("=" * 80)
    
    db_path = "data/tactics_betting.db"
    settings_path = "config/settings.json"
    aliases_path = "config/aliases.json"
    
    # 1. Initialize database if not present
    if not os.path.exists(db_path):
        print("[INFO] Initializing SQLite database...")
        init_db(db_path)
    
    # 2. Initialize engines
    resolver = EntityResolver(aliases_path)
    scraper = TheOddsAPIScraper(settings_path)
    prob_engine = ProbabilityEngine(settings_path)
    kelly_engine = KellyEngine(settings_path)
    json_logger = JsonDocumentLogger("predictions")
    
    # 3. Scrape odds from The Odds API
    print("\n[STEP 1] Scraping Premier League odds from The Odds API...")
    scraped_odds = scraper.scrape_odds("soccer_epl")
    if not scraped_odds:
        print("[ERROR] Failed to scrape any odds. Exiting.")
        sys.exit(1)
        
    print(f"[INFO] Scraped {len(scraped_odds)} matches from The Odds API.")
    
    # 4. Define Matchweek 38 Fixtures (Sunday, May 24, 2026)
    fixtures = [
        {
            "home": "Brighton",
            "away": "Manchester United",
            "home_manager": "Fabian Hürzeler",
            "away_manager": "Rúben Amorim",
            "home_form": "4-2-3-1",
            "away_form": "3-4-2-1",
            "manager_h2h_win_rate_home": 0.0,
            "tactical_flexibility_index_home": 0.7,
            "tactical_flexibility_index_away": 0.8
        },
        {
            "home": "Burnley",
            "away": "Wolverhampton",
            "home_manager": "Scott Parker",
            "away_manager": "Gary O'Neil",
            "home_form": "4-4-2",
            "away_form": "3-4-2-1",
            "manager_h2h_win_rate_home": 0.5,
            "tactical_flexibility_index_home": 0.5,
            "tactical_flexibility_index_away": 0.6
        },
        {
            "home": "Crystal Palace",
            "away": "Arsenal",
            "home_manager": "Oliver Glasner",
            "away_manager": "Mikel Arteta",
            "home_form": "3-4-2-1",
            "away_form": "4-3-3",
            "manager_h2h_win_rate_home": 0.0,
            "tactical_flexibility_index_home": 0.6,
            "tactical_flexibility_index_away": 0.3
        },
        {
            "home": "Fulham",
            "away": "Newcastle",
            "home_manager": "Marco Silva",
            "away_manager": "Eddie Howe",
            "home_form": "4-2-3-1",
            "away_form": "4-3-3",
            "manager_h2h_win_rate_home": 0.4,
            "tactical_flexibility_index_home": 0.6,
            "tactical_flexibility_index_away": 0.5
        },
        {
            "home": "Liverpool",
            "away": "Brentford",
            "home_manager": "Arne Slot",
            "away_manager": "Thomas Frank",
            "home_form": "4-3-3",
            "away_form": "4-3-3",
            "manager_h2h_win_rate_home": 0.0,
            "tactical_flexibility_index_home": 0.6,
            "tactical_flexibility_index_away": 0.6
        },
        {
            "home": "Manchester City",
            "away": "Aston Villa",
            "home_manager": "Josep Guardiola",
            "away_manager": "Unai Emery",
            "home_form": "4-3-3",
            "away_form": "4-2-3-1",
            "manager_h2h_win_rate_home": 0.65,
            "tactical_flexibility_index_home": 0.8,
            "tactical_flexibility_index_away": 0.7
        },
        {
            "home": "Nottingham Forest",
            "away": "Bournemouth",
            "home_manager": "Nuno Espírito Santo",
            "away_manager": "Andoni Iraola",
            "home_form": "4-2-3-1",
            "away_form": "4-2-3-1",
            "manager_h2h_win_rate_home": 0.3,
            "tactical_flexibility_index_home": 0.5,
            "tactical_flexibility_index_away": 0.6
        },
        {
            "home": "Sunderland",
            "away": "Chelsea",
            "home_manager": "Régis Le Bris",
            "away_manager": "Enzo Maresca",
            "home_form": "4-2-3-1",
            "away_form": "4-3-3",
            "manager_h2h_win_rate_home": 0.0,
            "tactical_flexibility_index_home": 0.5,
            "tactical_flexibility_index_away": 0.7
        },
        {
            "home": "Tottenham",
            "away": "Everton",
            "home_manager": "Ange Postecoglou",
            "away_manager": "Sean Dyche",
            "home_form": "4-3-3",
            "away_form": "4-4-2",
            "manager_h2h_win_rate_home": 0.5,
            "tactical_flexibility_index_home": 0.4,
            "tactical_flexibility_index_away": 0.5
        },
        {
            "home": "West Ham",
            "away": "Leeds",
            "home_manager": "Julen Lopetegui",
            "away_manager": "Daniel Farke",
            "home_form": "4-2-3-1",
            "away_form": "4-2-3-1",
            "manager_h2h_win_rate_home": 0.5,
            "tactical_flexibility_index_home": 0.6,
            "tactical_flexibility_index_away": 0.5
        }
    ]
    
    print("\n[STEP 2] Running multi-market predictions and EV calculations...")
    
    all_value_bets = []
    success_count = 0
    bankroll = 1000.0
    
    for fixture in fixtures:
        raw_home = fixture["home"]
        raw_away = fixture["away"]
        
        # Resolve entities to canonical names
        home_team = resolver.resolve_team(raw_home)
        away_team = resolver.resolve_team(raw_away)
        home_mgr = resolver.resolve_manager(fixture["home_manager"])
        away_mgr = resolver.resolve_manager(fixture["away_manager"])
        
        # Find corresponding odds in the scraped Odds API mapping
        odds = None
        for key, val in scraped_odds.items():
            if ' vs ' in key:
                s_home, s_away = key.split(' vs ')
                if resolver.resolve_team(s_home) == home_team and resolver.resolve_team(s_away) == away_team:
                    odds = val
                    break
        
        if not odds:
            print(f"[WARNING] Could not find scraped odds for {home_team} vs {away_team}. Skipping.")
            continue
            
        print(f"\nEvaluating Match: {home_team} vs {away_team}")
        match_value_bets = []
        
        # Generate Match ID following strict convention: epl_20260524_<home_pref>_<away_pref>
        home_pref = "".join([c for c in home_team if c.isalnum()]).lower()[:3]
        away_pref = "".join([c for c in away_team if c.isalnum()]).lower()[:3]
        match_id = f"epl_20260524_{home_pref}_{away_pref}"
        
        # Dictionaries for JSON Logging
        odds_decimal_dict = {}
        model_probabilities_dict = {}
        expected_value_dict = {}
        
        # ----------------------------------------------------
        # 1. 1X2 Market
        # ----------------------------------------------------
        book_1x2_odds = {
            "home": odds["1X2"]["home_win"],
            "draw": odds["1X2"]["draw"],
            "away": odds["1X2"]["away_win"]
        }
        
        # Populate odds decimal for logging
        odds_decimal_dict["home_win"] = book_1x2_odds["home"]
        odds_decimal_dict["draw"] = book_1x2_odds["draw"]
        odds_decimal_dict["away_win"] = book_1x2_odds["away"]
        
        probs_1x2_result = prob_engine.calculate_match_probabilities(
            home_team, away_team, home_mgr, away_mgr,
            fixture["home_form"], fixture["away_form"],
            bookmaker_odds=book_1x2_odds
        )
        probs_1x2 = probs_1x2_result["probabilities"]
        
        # Populate model probabilities for logging
        model_probabilities_dict["home_win"] = probs_1x2["home"]
        model_probabilities_dict["draw"] = probs_1x2["draw"]
        model_probabilities_dict["away_win"] = probs_1x2["away"]
        
        opportunities_1x2 = kelly_engine.evaluate_market_opportunities(probs_1x2, book_1x2_odds)
        
        # Populate expected value for logging
        expected_value_dict["home_win"] = opportunities_1x2["home"]["ev"]
        expected_value_dict["draw"] = opportunities_1x2["draw"]["ev"]
        expected_value_dict["away_win"] = opportunities_1x2["away"]["ev"]
        
        # Check for 1X2 value selections
        for outcome in ["home", "draw", "away"]:
            opp = opportunities_1x2[outcome]
            selection_name = f"{outcome}_win" if outcome != "draw" else "draw"
            if opp["ev"] > 0 and probs_1x2[outcome] > opp["true_fair_p"]:
                bet_dict = {
                    "match_id": match_id,
                    "team_match": f"{home_team} vs {away_team}",
                    "market": "1X2",
                    "selection": selection_name,
                    "true_fair_p": probs_1x2[outcome],
                    "de_juiced_p": opp["true_fair_p"],
                    "actual_decimal_odds": book_1x2_odds[outcome],
                    "ev": opp["ev"],
                    "suggested_stake_fraction": opp["suggested_stake_fraction"]
                }
                all_value_bets.append(bet_dict)
                match_value_bets.append(bet_dict)
                
        # ----------------------------------------------------
        # 2. Goals O/U 2.5 Market
        # ----------------------------------------------------
        book_goals_odds = odds.get("goals_ou_2.5", {})
        if book_goals_odds:
            odds_decimal_dict["goals_over_2.5"] = book_goals_odds["over"]
            odds_decimal_dict["goals_under_2.5"] = book_goals_odds["under"]
            
            probs_goals = prob_engine.calculate_goals_ou_probability(
                home_team, away_team, bookmaker_odds=book_goals_odds
            )
            model_probabilities_dict["goals_over_2.5"] = probs_goals["over"]
            model_probabilities_dict["goals_under_2.5"] = probs_goals["under"]
            
            opportunities_goals = kelly_engine.evaluate_market_opportunities(probs_goals, book_goals_odds)
            expected_value_dict["goals_over_2.5"] = opportunities_goals["over"]["ev"]
            expected_value_dict["goals_under_2.5"] = opportunities_goals["under"]["ev"]
            
            for outcome in ["over", "under"]:
                opp = opportunities_goals[outcome]
                if opp["ev"] > 0 and probs_goals[outcome] > opp["true_fair_p"]:
                    bet_dict = {
                        "match_id": match_id,
                        "team_match": f"{home_team} vs {away_team}",
                        "market": "goals_ou_2.5",
                        "selection": f"goals_{outcome}_2.5",
                        "true_fair_p": probs_goals[outcome],
                        "de_juiced_p": opp["true_fair_p"],
                        "actual_decimal_odds": book_goals_odds[outcome],
                        "ev": opp["ev"],
                        "suggested_stake_fraction": opp["suggested_stake_fraction"]
                    }
                    all_value_bets.append(bet_dict)
                    match_value_bets.append(bet_dict)

        # ----------------------------------------------------
        # 3. Both Teams to Score (BTTS) Market
        # ----------------------------------------------------
        book_btts_odds = odds.get("btts", {})
        if book_btts_odds:
            odds_decimal_dict["btts_yes"] = book_btts_odds["yes"]
            odds_decimal_dict["btts_no"] = book_btts_odds["no"]
            
            probs_btts = prob_engine.calculate_btts_probability(
                home_team, away_team, bookmaker_odds=book_btts_odds
            )
            model_probabilities_dict["btts_yes"] = probs_btts["yes"]
            model_probabilities_dict["btts_no"] = probs_btts["no"]
            
            opportunities_btts = kelly_engine.evaluate_market_opportunities(probs_btts, book_btts_odds)
            expected_value_dict["btts_yes"] = opportunities_btts["yes"]["ev"]
            expected_value_dict["btts_no"] = opportunities_btts["no"]["ev"]
            
            for outcome in ["yes", "no"]:
                opp = opportunities_btts[outcome]
                if opp["ev"] > 0 and probs_btts[outcome] > opp["true_fair_p"]:
                    bet_dict = {
                        "match_id": match_id,
                        "team_match": f"{home_team} vs {away_team}",
                        "market": "btts",
                        "selection": f"btts_{outcome}",
                        "true_fair_p": probs_btts[outcome],
                        "de_juiced_p": opp["true_fair_p"],
                        "actual_decimal_odds": book_btts_odds[outcome],
                        "ev": opp["ev"],
                        "suggested_stake_fraction": opp["suggested_stake_fraction"]
                    }
                    all_value_bets.append(bet_dict)
                    match_value_bets.append(bet_dict)

        # ----------------------------------------------------
        # 4. Corners Market
        # ----------------------------------------------------
        corners_market = odds.get("corners_ou", {})
        for line, line_odds in corners_market.items():
            odds_decimal_dict[f"corners_over_{line}"] = line_odds["over"]
            odds_decimal_dict[f"corners_under_{line}"] = line_odds["under"]
            
            probs_corners = prob_engine.calculate_corners_probability(
                home_team, away_team, bookmaker_odds=line_odds
            )
            model_probabilities_dict[f"corners_over_{line}"] = probs_corners["over"]
            model_probabilities_dict[f"corners_under_{line}"] = probs_corners["under"]
            
            opportunities_corners = kelly_engine.evaluate_market_opportunities(probs_corners, line_odds)
            expected_value_dict[f"corners_over_{line}"] = opportunities_corners["over"]["ev"]
            expected_value_dict[f"corners_under_{line}"] = opportunities_corners["under"]["ev"]
            
            for outcome in ["over", "under"]:
                opp = opportunities_corners[outcome]
                if opp["ev"] > 0 and probs_corners[outcome] > opp["true_fair_p"]:
                    bet_dict = {
                        "match_id": match_id,
                        "team_match": f"{home_team} vs {away_team}",
                        "market": "corners_ou",
                        "selection": f"corners_{outcome}_{line}",
                        "true_fair_p": probs_corners[outcome],
                        "de_juiced_p": opp["true_fair_p"],
                        "actual_decimal_odds": line_odds[outcome],
                        "ev": opp["ev"],
                        "suggested_stake_fraction": opp["suggested_stake_fraction"]
                    }
                    all_value_bets.append(bet_dict)
                    match_value_bets.append(bet_dict)

        # Determine best EV bet for this match (overall recommended bet)
        best_match_bet = None
        if match_value_bets:
            best_match_bet = max(match_value_bets, key=lambda x: x["ev"])
            
        recommended_bet = "none"
        edge_identified = False
        kelly_fraction = 0.0
        reasoning_str = "No positive expected value identified across the market outcomes under current tactical settings."
        
        if best_match_bet:
            recommended_bet = best_match_bet["selection"]
            edge_identified = True
            kelly_fraction = best_match_bet["suggested_stake_fraction"]
            
            prob_pct = best_match_bet["true_fair_p"] * 100
            implied_pct = (1.0 / best_match_bet["actual_decimal_odds"]) * 100
            reasoning_str = (
                f"Model probability ({prob_pct:.1f}%) exceeds implied bookmaker odds ({implied_pct:.1f}%) "
                f"for market '{best_match_bet['market']}' selection '{best_match_bet['selection']}'. "
                f"Manager tactics and matchups favor this outcome."
            )
            print(f"  ==> EDGE IDENTIFIED: Suggested Bet is {recommended_bet.upper()} "
                  f"(EV={best_match_bet['ev']*100:+.2f}%, Stake={round(kelly_fraction*100, 2)}%)")
        else:
            print("  ==> NO EDGE IDENTIFIED.")
            
        # ----------------------------------------------------
        # Log to SQLite Database (1X2 Prediction as before)
        # ----------------------------------------------------
        # Determine 1X2 recommended bet for SQLite
        positive_1x2_evs = {k: o["ev"] for k, o in opportunities_1x2.items() if o["ev"] > 0}
        recommended_1x2 = "none"
        placed_bet_stake = 0.0
        placed_bet_odds = 0.0
        
        if positive_1x2_evs:
            best_outcome = max(positive_1x2_evs, key=positive_1x2_evs.get)
            bet_map = {"home": "home_win", "draw": "draw", "away": "away_win"}
            recommended_1x2 = bet_map[best_outcome]
            placed_bet_stake = bankroll * opportunities_1x2[best_outcome]["suggested_stake_fraction"]
            placed_bet_odds = book_1x2_odds[best_outcome]
            
        margin_sum_1x2 = sum(1.0 / book_1x2_odds[k] for k in ["home", "draw", "away"])
        pred_data = {
            "id": match_id,
            "match_date": "2026-05-24 15:00:00",
            "home_team": home_team,
            "away_team": away_team,
            "home_manager": home_mgr,
            "away_manager": away_mgr,
            "home_predicted_formation": fixture["home_form"],
            "away_predicted_formation": fixture["away_form"],
            "model_p_home": probs_1x2["home"],
            "model_p_draw": probs_1x2["draw"],
            "model_p_away": probs_1x2["away"],
            "odds_home": book_1x2_odds["home"],
            "odds_draw": book_1x2_odds["draw"],
            "odds_away": book_1x2_odds["away"],
            "implied_p_home": round((1.0 / book_1x2_odds["home"]) / margin_sum_1x2, 4),
            "implied_p_draw": round((1.0 / book_1x2_odds["draw"]) / margin_sum_1x2, 4),
            "implied_p_away": round((1.0 / book_1x2_odds["away"]) / margin_sum_1x2, 4),
            "calculated_ev_home": opportunities_1x2["home"]["ev"],
            "calculated_ev_draw": opportunities_1x2["draw"]["ev"],
            "calculated_ev_away": opportunities_1x2["away"]["ev"],
            "kelly_stake_home": opportunities_1x2["home"]["suggested_stake_fraction"],
            "kelly_stake_draw": opportunities_1x2["draw"]["suggested_stake_fraction"],
            "kelly_stake_away": opportunities_1x2["away"]["suggested_stake_fraction"],
            "placed_bet_type": recommended_1x2.split("_")[0] if recommended_1x2 != "none" else "none",
            "placed_bet_odds": placed_bet_odds if recommended_1x2 != "none" else None,
            "placed_bet_stake": placed_bet_stake if recommended_1x2 != "none" else 0.0,
            "status": "PENDING"
        }
        log_prediction(pred_data, db_path)
        
        # ----------------------------------------------------
        # Log Pre-Match JSON Document (Multi-Market)
        # ----------------------------------------------------
        meta_data = {
            "date": "2026-05-24T15:00:00Z",
            "competition": "Premier League",
            "home_team": home_team,
            "away_team": away_team,
            "home_manager": home_mgr,
            "away_manager": away_mgr
        }
        context_data = {
            "predicted_home_formation": fixture["home_form"],
            "predicted_away_formation": fixture["away_form"],
            "manager_h2h_win_rate_home": fixture["manager_h2h_win_rate_home"],
            "tactical_flexibility_index_home": fixture["tactical_flexibility_index_home"],
            "tactical_flexibility_index_away": fixture["tactical_flexibility_index_away"]
        }
        market_data = {
            "bookmaker": "The Odds API",
            "odds_decimal": odds_decimal_dict
        }
        inference_data = {
            "model_probabilities": model_probabilities_dict,
            "expected_value": expected_value_dict,
            "action": {
                "recommended_bet": recommended_bet,
                "edge_identified": edge_identified,
                "kelly_fraction_pct": round(kelly_fraction * 100, 2),
                "reasoning": reasoning_str
            }
        }
        
        json_logger.log_pre_match(match_id, meta_data, context_data, market_data, inference_data)
        success_count += 1
        
    print("-" * 80)
    print(f"[SUCCESS] Successfully processed {success_count} matches!")
    
    # ----------------------------------------------------
    # 5. Build Premium Accumulator (Parlay)
    # ----------------------------------------------------
    print("\n[STEP 3] Assembling Premium Accumulator Parlay...")
    premium_parlay = build_premium_accumulator(all_value_bets, max_legs=3)
    
    # Save the parlay ticket JSON to predictions/ and project root
    for file_path in ["predictions/accumulator_ticket.json", "accumulator_ticket.json"]:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(premium_parlay, f, indent=2)
    print(f"[INFO] Premium parlay ticket written to 'predictions/accumulator_ticket.json' and 'accumulator_ticket.json'")
    
    # ----------------------------------------------------
    # 6. Print EV Summary Table
    # ----------------------------------------------------
    print("\n" + "=" * 110)
    print("                         EPL MATCHWEEK 38 EXPECTED VALUE (EV) ALL VALUE BETS")
    print("=" * 110)
    print(f"{'Match':<35} | {'Market':<13} | {'Selection':<20} | {'Odds':<6} | {'Model Prob':<10} | {'EV %':<9} | {'Staking %':<9}")
    print("-" * 110)
    
    sorted_value_bets = sorted(all_value_bets, key=lambda x: x["ev"], reverse=True)
    for bet in sorted_value_bets:
        match_str = bet["team_match"]
        market_str = bet["market"]
        selection_str = bet["selection"]
        odds_str = f"{bet['actual_decimal_odds']:.2f}"
        prob_str = f"{bet['true_fair_p']*100:.2f}%"
        ev_str = f"{bet['ev']*100:+.2f}%"
        stake_str = f"{bet['suggested_stake_fraction']*100:.2f}%" if bet['suggested_stake_fraction'] > 0 else "0.00%"
        print(f"{match_str:<35} | {market_str:<13} | {selection_str:<20} | {odds_str:>6} | {prob_str:>10} | {ev_str:>9} | {stake_str:>9}")
    print("=" * 110)
    
    # ----------------------------------------------------
    # 7. Print Premium 3-Leg Accumulator Ticket
    # ----------------------------------------------------
    print("\n" + "=" * 80)
    print("                      PREMIUM 3-LEG ACCUMULATOR TICKET                       ")
    print("=" * 80)
    
    legs = premium_parlay["legs"]
    for i, leg in enumerate(legs, 1):
        print(f"Leg {i}: {leg['team_match']}")
        print(f"       Market:    {leg['market']}")
        print(f"       Selection: {leg['selection'].upper()} (Odds: {leg['actual_decimal_odds']:.2f})")
        print(f"       Model Prob: {leg['true_fair_p']*100:.2f}% (De-juiced Fair: {leg['de_juiced_p']*100:.2f}%)")
        print(f"       Edge (EV):  {leg['ev']*100:+.2f}%")
        print("-" * 80)
        
    print(f"COMBINED ODDS:        {premium_parlay['combined_odds']:.2f}")
    print(f"COMBINED PROBABILITY: {premium_parlay['combined_p']*100:.2f}%")
    print(f"COMPOUNDED EV:        {premium_parlay['ev']*100:+.2f}%")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    main()
