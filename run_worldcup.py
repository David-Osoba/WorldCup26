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
    print("               FIFA World Cup 2026 Execution Script                ")
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
    
    # 3. Scrape odds from The Odds API for FIFA World Cup
    print("\n[STEP 1] Scraping World Cup odds from The Odds API...")
    scraped_odds = scraper.scrape_odds("soccer_fifa_world_cup")
    if not scraped_odds:
        print("[ERROR] Failed to scrape any odds. Exiting.")
        sys.exit(1)
        
    print(f"[INFO] Scraped {len(scraped_odds)} matches from The Odds API.")
    
    # 4. Define World Cup Fixtures and Manager mappings
    fixtures = []
    world_cup_managers = {
        "USA": "Mauricio Pochettino",
        "England": "Thomas Tuchel",
        "Argentina": "Lionel Scaloni",
        "France": "Didier Deschamps",
        "Mexico": "Javier Aguirre",
        "Canada": "Jesse Marsch",
        "Spain": "Luis de la Fuente",
        "Germany": "Julian Nagelsmann",
        "Brazil": "Dorival Júnior"
    }
    
    for key in scraped_odds.keys():
        if ' vs ' not in key:
            continue
        raw_home, raw_away = key.split(' vs ')
        
        # Resolve names with diacritic-protection
        home_team = resolver.resolve_team(raw_home)
        away_team = resolver.resolve_team(raw_away)
        
        home_mgr_name = world_cup_managers.get(home_team, "Unknown")
        away_mgr_name = world_cup_managers.get(away_team, "Unknown")
        
        fixtures.append({
            "home": home_team,
            "away": away_team,
            "home_manager": home_mgr_name,
            "away_manager": away_mgr_name,
            "home_form": "4-3-3",
            "away_form": "4-3-3",
            "manager_h2h_win_rate_home": 0.5,
            "tactical_flexibility_index_home": 0.5,
            "tactical_flexibility_index_away": 0.5
        })
    
    print("\n[STEP 2] Running multi-market predictions and EV calculations...")
    
    all_value_bets = []
    success_count = 0
    bankroll = 1000.0
    
    hosts = {"USA", "Mexico", "Canada"}
    
    for fixture in fixtures:
        raw_home = fixture["home"]
        raw_away = fixture["away"]
        
        home_team = resolver.resolve_team(raw_home)
        away_team = resolver.resolve_team(raw_away)
        
        if fixture["home_manager"] == "Unknown":
            home_mgr = "Unknown"
        else:
            home_mgr = resolver.resolve_manager(fixture["home_manager"])
            
        if fixture["away_manager"] == "Unknown":
            away_mgr = "Unknown"
        else:
            away_mgr = resolver.resolve_manager(fixture["away_manager"])
        
        # Find scraped odds
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
        
        # Generate Match ID starting with 'wc_2026'
        home_pref = "".join([c for c in home_team if c.isalnum()]).lower()[:3]
        away_pref = "".join([c for c in away_team if c.isalnum()]).lower()[:3]
        match_id = f"wc_20260615_{home_pref}_{away_pref}"
        
        # Host nation clash and multiplier logic
        is_home_host = home_team in hosts
        is_away_host = away_team in hosts
        
        if is_home_host and is_away_host:
            is_host_nation = None
            print(f"  [HOST LOGIC] Co-host clash: {home_team} vs {away_team}. Treating as pure neutral game.")
        elif is_home_host:
            is_host_nation = "home"
            print(f"  [HOST LOGIC] Host advantage applied to home team: {home_team}")
        elif is_away_host:
            is_host_nation = "away"
            print(f"  [HOST LOGIC] Host advantage applied to away team: {away_team}")
        else:
            is_host_nation = None
            print("  [HOST LOGIC] Neutral game, no host advantage.")
            
        # JSON logs dictionaries
        odds_decimal_dict = {}
        model_probabilities_dict = {}
        expected_value_dict = {}
        
        # ----------------------------------------------------
        # 1. 1X2 Market (Universal KeyError Protection)
        # ----------------------------------------------------
        book_1x2_odds = {
            "home": odds.get("1X2", {}).get("home_win", 0.0),
            "draw": odds.get("1X2", {}).get("draw", 0.0),
            "away": odds.get("1X2", {}).get("away_win", 0.0)
        }
        
        odds_decimal_dict["home_win"] = book_1x2_odds.get("home", 0.0)
        odds_decimal_dict["draw"] = book_1x2_odds.get("draw", 0.0)
        odds_decimal_dict["away_win"] = book_1x2_odds.get("away", 0.0)
        
        probs_1x2_result = prob_engine.calculate_match_probabilities(
            home_team, away_team, home_mgr, away_mgr,
            fixture["home_form"], fixture["away_form"],
            bookmaker_odds=book_1x2_odds,
            is_neutral_venue=True, # Enforced for World Cup
            is_host_nation=is_host_nation
        )
        probs_1x2 = probs_1x2_result["probabilities"]
        
        model_probabilities_dict["home_win"] = probs_1x2.get("home", 0.0)
        model_probabilities_dict["draw"] = probs_1x2.get("draw", 0.0)
        model_probabilities_dict["away_win"] = probs_1x2.get("away", 0.0)
        
        opportunities_1x2 = kelly_engine.evaluate_market_opportunities(probs_1x2, book_1x2_odds)
        
        expected_value_dict["home_win"] = opportunities_1x2.get("home", {}).get("ev", 0.0)
        expected_value_dict["draw"] = opportunities_1x2.get("draw", {}).get("ev", 0.0)
        expected_value_dict["away_win"] = opportunities_1x2.get("away", {}).get("ev", 0.0)
        
        # Check for 1X2 value
        for outcome in ["home", "draw", "away"]:
            opp = opportunities_1x2.get(outcome)
            if not opp:
                continue
            selection_name = f"{outcome}_win" if outcome != "draw" else "draw"
            model_p = probs_1x2.get(outcome, 0.0)
            true_p = opp.get("true_fair_p", 0.0)
            ev = opp.get("ev", 0.0)
            
            if ev > 0 and model_p > true_p:
                bet_dict = {
                    "match_id": match_id,
                    "team_match": f"{home_team} vs {away_team}",
                    "market": "1X2",
                    "selection": selection_name,
                    "true_fair_p": model_p,
                    "de_juiced_p": true_p,
                    "actual_decimal_odds": book_1x2_odds.get(outcome, 0.0),
                    "ev": ev,
                    "suggested_stake_fraction": opp.get("suggested_stake_fraction", 0.0)
                }
                all_value_bets.append(bet_dict)
                match_value_bets.append(bet_dict)
                
        # ----------------------------------------------------
        # 2. To Advance Market (Graceful Fallback & Synthetic Calculations)
        # ----------------------------------------------------
        book_to_advance_odds = odds.get("to_advance")
        
        # Calculate synthetic advancement probabilities regardless of bookmaker odds presence
        probs_to_advance = prob_engine.calculate_advancement_probability(
            probs_1x2.get("home", 0.0),
            probs_1x2.get("draw", 0.0),
            probs_1x2.get("away", 0.0)
        )
        model_probabilities_dict["to_advance_home"] = probs_to_advance["home"]
        model_probabilities_dict["to_advance_away"] = probs_to_advance["away"]
        
        if book_to_advance_odds:
            print("  [ADVANCEMENT] Live 'To Advance' odds found in scraped data.")
            odds_decimal_dict["to_advance_home"] = book_to_advance_odds.get("home", 0.0)
            odds_decimal_dict["to_advance_away"] = book_to_advance_odds.get("away", 0.0)
            
            opportunities_adv = kelly_engine.evaluate_market_opportunities(probs_to_advance, book_to_advance_odds)
            expected_value_dict["to_advance_home"] = opportunities_adv.get("home", {}).get("ev", 0.0)
            expected_value_dict["to_advance_away"] = opportunities_adv.get("away", {}).get("ev", 0.0)
            
            for outcome in ["home", "away"]:
                opp = opportunities_adv.get(outcome)
                if not opp:
                    continue
                selection_name = f"to_advance_{outcome}"
                model_p = probs_to_advance.get(outcome, 0.0)
                true_p = opp.get("true_fair_p", 0.0)
                ev = opp.get("ev", 0.0)
                
                if ev > 0 and model_p > true_p:
                    bet_dict = {
                        "match_id": match_id,
                        "team_match": f"{home_team} vs {away_team}",
                        "market": "to_advance",
                        "selection": selection_name,
                        "true_fair_p": model_p,
                        "de_juiced_p": true_p,
                        "actual_decimal_odds": book_to_advance_odds.get(outcome, 0.0),
                        "ev": ev,
                        "suggested_stake_fraction": opp.get("suggested_stake_fraction", 0.0)
                    }
                    all_value_bets.append(bet_dict)
                    match_value_bets.append(bet_dict)
        else:
            print("  [ADVANCEMENT] No 'To Advance' odds. Logging synthetic advancement probabilities only.")
            expected_value_dict["to_advance_home"] = 0.0
            expected_value_dict["to_advance_away"] = 0.0
            
        # ----------------------------------------------------
        # 3. Goals O/U 2.5 Market
        # ----------------------------------------------------
        book_goals_odds = odds.get("goals_ou_2.5", {})
        if book_goals_odds:
            odds_decimal_dict["goals_over_2.5"] = book_goals_odds.get("over", 0.0)
            odds_decimal_dict["goals_under_2.5"] = book_goals_odds.get("under", 0.0)
            
            probs_goals = prob_engine.calculate_goals_ou_probability(
                home_team, away_team, bookmaker_odds=book_goals_odds
            )
            model_probabilities_dict["goals_over_2.5"] = probs_goals.get("over", 0.0)
            model_probabilities_dict["goals_under_2.5"] = probs_goals.get("under", 0.0)
            
            opportunities_goals = kelly_engine.evaluate_market_opportunities(probs_goals, book_goals_odds)
            expected_value_dict["goals_over_2.5"] = opportunities_goals.get("over", {}).get("ev", 0.0)
            expected_value_dict["goals_under_2.5"] = opportunities_goals.get("under", {}).get("ev", 0.0)
            
            for outcome in ["over", "under"]:
                opp = opportunities_goals.get(outcome)
                if not opp:
                    continue
                model_p = probs_goals.get(outcome, 0.0)
                true_p = opp.get("true_fair_p", 0.0)
                ev = opp.get("ev", 0.0)
                
                if ev > 0 and model_p > true_p:
                    bet_dict = {
                        "match_id": match_id,
                        "team_match": f"{home_team} vs {away_team}",
                        "market": "goals_ou_2.5",
                        "selection": f"goals_{outcome}_2.5",
                        "true_fair_p": model_p,
                        "de_juiced_p": true_p,
                        "actual_decimal_odds": book_goals_odds.get(outcome, 0.0),
                        "ev": ev,
                        "suggested_stake_fraction": opp.get("suggested_stake_fraction", 0.0)
                    }
                    all_value_bets.append(bet_dict)
                    match_value_bets.append(bet_dict)

        # ----------------------------------------------------
        # 4. Both Teams to Score (BTTS) Market
        # ----------------------------------------------------
        book_btts_odds = odds.get("btts", {})
        if book_btts_odds:
            odds_decimal_dict["btts_yes"] = book_btts_odds.get("yes", 0.0)
            odds_decimal_dict["btts_no"] = book_btts_odds.get("no", 0.0)
            
            probs_btts = prob_engine.calculate_btts_probability(
                home_team, away_team, bookmaker_odds=book_btts_odds
            )
            model_probabilities_dict["btts_yes"] = probs_btts.get("yes", 0.0)
            model_probabilities_dict["btts_no"] = probs_btts.get("no", 0.0)
            
            opportunities_btts = kelly_engine.evaluate_market_opportunities(probs_btts, book_btts_odds)
            expected_value_dict["btts_yes"] = opportunities_btts.get("yes", {}).get("ev", 0.0)
            expected_value_dict["btts_no"] = opportunities_btts.get("no", {}).get("ev", 0.0)
            
            for outcome in ["yes", "no"]:
                opp = opportunities_btts.get(outcome)
                if not opp:
                    continue
                model_p = probs_btts.get(outcome, 0.0)
                true_p = opp.get("true_fair_p", 0.0)
                ev = opp.get("ev", 0.0)
                
                if ev > 0 and model_p > true_p:
                    bet_dict = {
                        "match_id": match_id,
                        "team_match": f"{home_team} vs {away_team}",
                        "market": "btts",
                        "selection": f"btts_{outcome}",
                        "true_fair_p": model_p,
                        "de_juiced_p": true_p,
                        "actual_decimal_odds": book_btts_odds.get(outcome, 0.0),
                        "ev": ev,
                        "suggested_stake_fraction": opp.get("suggested_stake_fraction", 0.0)
                    }
                    all_value_bets.append(bet_dict)
                    match_value_bets.append(bet_dict)

        # ----------------------------------------------------
        # 5. Corners Market
        # ----------------------------------------------------
        corners_market = odds.get("corners_ou", {})
        for line, line_odds in corners_market.items():
            odds_decimal_dict[f"corners_over_{line}"] = line_odds.get("over", 0.0)
            odds_decimal_dict[f"corners_under_{line}"] = line_odds.get("under", 0.0)
            
            probs_corners = prob_engine.calculate_corners_probability(
                home_team, away_team, bookmaker_odds=line_odds
            )
            model_probabilities_dict[f"corners_over_{line}"] = probs_corners.get("over", 0.0)
            model_probabilities_dict[f"corners_under_{line}"] = probs_corners.get("under", 0.0)
            
            opportunities_corners = kelly_engine.evaluate_market_opportunities(probs_corners, line_odds)
            expected_value_dict[f"corners_over_{line}"] = opportunities_corners.get("over", {}).get("ev", 0.0)
            expected_value_dict[f"corners_under_{line}"] = opportunities_corners.get("under", {}).get("ev", 0.0)
            
            for outcome in ["over", "under"]:
                opp = opportunities_corners.get(outcome)
                if not opp:
                    continue
                model_p = probs_corners.get(outcome, 0.0)
                true_p = opp.get("true_fair_p", 0.0)
                ev = opp.get("ev", 0.0)
                
                if ev > 0 and model_p > true_p:
                    bet_dict = {
                        "match_id": match_id,
                        "team_match": f"{home_team} vs {away_team}",
                        "market": "corners_ou",
                        "selection": f"corners_{outcome}_{line}",
                        "true_fair_p": model_p,
                        "de_juiced_p": true_p,
                        "actual_decimal_odds": line_odds.get(outcome, 0.0),
                        "ev": ev,
                        "suggested_stake_fraction": opp.get("suggested_stake_fraction", 0.0)
                    }
                    all_value_bets.append(bet_dict)
                    match_value_bets.append(bet_dict)

        # Identify best EV bet for overall recommended bet
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
            implied_pct = (1.0 / best_match_bet["actual_decimal_odds"]) * 100 if best_match_bet["actual_decimal_odds"] > 0 else 0.0
            reasoning_str = (
                f"Model probability ({prob_pct:.1f}%) exceeds implied bookmaker odds ({implied_pct:.1f}%) "
                f"for market '{best_match_bet['market']}' selection '{best_match_bet['selection']}'. "
                f"Tactics and co-host setups favor this outcome."
            )
            print(f"  ==> EDGE IDENTIFIED: Suggested Bet is {recommended_bet.upper()} "
                  f"(EV={best_match_bet['ev']*100:+.2f}%, Stake={round(kelly_fraction*100, 2)}%)")
        else:
            print("  ==> NO EDGE IDENTIFIED.")
            
        # ----------------------------------------------------
        # Safe Log to SQLite Database (1X2 predictions)
        # ----------------------------------------------------
        # Prevent any potential crash if opportunities_1x2 outcomes are missing
        positive_1x2_evs = {k: o.get("ev", 0.0) for k, o in opportunities_1x2.items() if o.get("ev", 0.0) > 0}
        recommended_1x2 = "none"
        placed_bet_stake = 0.0
        placed_bet_odds = 0.0
        
        if positive_1x2_evs:
            best_outcome = max(positive_1x2_evs, key=positive_1x2_evs.get)
            bet_map = {"home": "home_win", "draw": "draw", "away": "away_win"}
            recommended_1x2 = bet_map.get(best_outcome, "none")
            
            opt_outcome = opportunities_1x2.get(best_outcome, {})
            placed_bet_stake = bankroll * opt_outcome.get("suggested_stake_fraction", 0.0)
            placed_bet_odds = book_1x2_odds.get(best_outcome, 0.0)
            
        margin_sum_1x2 = sum(1.0 / book_1x2_odds[k] for k in ["home", "draw", "away"] if book_1x2_odds.get(k, 0.0) > 0)
        
        pred_data = {
            "id": match_id,
            "match_date": "2026-06-15 18:00:00",
            "home_team": home_team,
            "away_team": away_team,
            "home_manager": home_mgr,
            "away_manager": away_mgr,
            "home_predicted_formation": fixture["home_form"],
            "away_predicted_formation": fixture["away_form"],
            "model_p_home": probs_1x2.get("home", 0.0),
            "model_p_draw": probs_1x2.get("draw", 0.0),
            "model_p_away": probs_1x2.get("away", 0.0),
            "odds_home": book_1x2_odds.get("home", 0.0),
            "odds_draw": book_1x2_odds.get("draw", 0.0),
            "odds_away": book_1x2_odds.get("away", 0.0),
            "implied_p_home": round((1.0 / book_1x2_odds.get("home", 1.0)) / margin_sum_1x2, 4) if book_1x2_odds.get("home", 0.0) > 0 and margin_sum_1x2 > 0 else 0.0,
            "implied_p_draw": round((1.0 / book_1x2_odds.get("draw", 1.0)) / margin_sum_1x2, 4) if book_1x2_odds.get("draw", 0.0) > 0 and margin_sum_1x2 > 0 else 0.0,
            "implied_p_away": round((1.0 / book_1x2_odds.get("away", 1.0)) / margin_sum_1x2, 4) if book_1x2_odds.get("away", 0.0) > 0 and margin_sum_1x2 > 0 else 0.0,
            "calculated_ev_home": opportunities_1x2.get("home", {}).get("ev", 0.0),
            "calculated_ev_draw": opportunities_1x2.get("draw", {}).get("ev", 0.0),
            "calculated_ev_away": opportunities_1x2.get("away", {}).get("ev", 0.0),
            "kelly_stake_home": opportunities_1x2.get("home", {}).get("suggested_stake_fraction", 0.0),
            "kelly_stake_draw": opportunities_1x2.get("draw", {}).get("suggested_stake_fraction", 0.0),
            "kelly_stake_away": opportunities_1x2.get("away", {}).get("suggested_stake_fraction", 0.0),
            "placed_bet_type": recommended_1x2.split("_")[0] if recommended_1x2 != "none" else "none",
            "placed_bet_odds": placed_bet_odds if recommended_1x2 != "none" else None,
            "placed_bet_stake": placed_bet_stake if recommended_1x2 != "none" else 0.0,
            "status": "PENDING"
        }
        log_prediction(pred_data, db_path)
        
        # ----------------------------------------------------
        # Log Pre-Match JSON Document
        # ----------------------------------------------------
        meta_data = {
            "date": "2026-06-15T18:00:00Z",
            "competition": "FIFA World Cup",
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
    print(f"[SUCCESS] Successfully processed {success_count} World Cup matches!")
    
    # ----------------------------------------------------
    # Assemble Premium Accumulator Parlay
    # ----------------------------------------------------
    print("\n[STEP 3] Assembling Premium Accumulator Parlay...")
    premium_parlay = build_premium_accumulator(all_value_bets, max_legs=3)
    
    # Save the parlay ticket JSON to predictions/ and project root
    for file_path in ["predictions/wc_accumulator_ticket.json", "wc_accumulator_ticket.json"]:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(premium_parlay, f, indent=2)
    print(f"[INFO] Premium parlay ticket written to 'predictions/wc_accumulator_ticket.json' and 'wc_accumulator_ticket.json'")
    
    # ----------------------------------------------------
    # Print EV Summary Table
    # ----------------------------------------------------
    print("\n" + "=" * 110)
    print("                      WORLD CUP EXPECTED VALUE (EV) ALL VALUE BETS")
    print("=" * 110)
    print(f"{'Match':<35} | {'Market':<13} | {'Selection':<20} | {'Odds':<6} | {'Model Prob':<10} | {'EV %':<9} | {'Staking %':<9}")
    print("-" * 110)
    
    sorted_value_bets = sorted(all_value_bets, key=lambda x: x.get("ev", 0.0), reverse=True)
    for bet in sorted_value_bets:
        match_str = bet.get("team_match", "")
        market_str = bet.get("market", "")
        selection_str = bet.get("selection", "")
        odds_str = f"{bet.get('actual_decimal_odds', 0.0):.2f}"
        prob_str = f"{bet.get('true_fair_p', 0.0)*100:.2f}%"
        ev_str = f"{bet.get('ev', 0.0)*100:+.2f}%"
        stake_str = f"{bet.get('suggested_stake_fraction', 0.0)*100:.2f}%" if bet.get('suggested_stake_fraction', 0.0) > 0 else "0.00%"
        print(f"{match_str:<35} | {market_str:<13} | {selection_str:<20} | {odds_str:>6} | {prob_str:>10} | {ev_str:>9} | {stake_str:>9}")
    print("=" * 110)
    
    # ----------------------------------------------------
    # Print Premium 3-Leg Accumulator Ticket
    # ----------------------------------------------------
    print("\n" + "=" * 80)
    print("                      PREMIUM 3-LEG ACCUMULATOR TICKET                       ")
    print("=" * 80)
    
    legs = premium_parlay.get("legs", [])
    for i, leg in enumerate(legs, 1):
        print(f"Leg {i}: {leg.get('team_match', '')}")
        print(f"       Market:    {leg.get('market', '')}")
        print(f"       Selection: {leg.get('selection', '').upper()} (Odds: {leg.get('actual_decimal_odds', 0.0):.2f})")
        print(f"       Model Prob: {leg.get('true_fair_p', 0.0)*100:.2f}% (De-juiced Fair: {leg.get('de_juiced_p', 0.0)*100:.2f}%)")
        print(f"       Edge (EV):  {leg.get('ev', 0.0)*100:+.2f}%")
        print("-" * 80)
        
    print(f"COMBINED ODDS:        {premium_parlay.get('combined_odds', 1.0):.2f}")
    print(f"COMBINED PROBABILITY: {premium_parlay.get('combined_p', 1.0)*100:.2f}%")
    print(f"COMPOUNDED EV:        {premium_parlay.get('ev', 0.0)*100:+.2f}%")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    main()
