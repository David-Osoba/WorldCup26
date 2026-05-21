# Skill: Football Tactical Prediction & Bankroll Management Agent

This file describes a reusable operational skill enabling an agent to perform advanced tactical scraping, probability modeling, EV calculation, Kelly Criterion bankroll allocation, and accumulator building for football matches.

---

## 1. Domain & Competition Restraints
This skill operates across the following domestic and international tournaments:
* **English Premier League (EPL)**
* **La Liga (Spain)**
* **Serie A (Italy)**
* **FIFA World Cup**

---

## 2. Core Operational Rules

### Rule 1: Never Guess / Impute Missing Data
* When manager history, preferred formations, or tactical matchups are missing, always fallback to robust defaults (e.g., formation `"4-3-3"`, manager points-per-game `1.5`) rather than halting execution or throwing errors.
* Utilize entity resolution with diacritic-insensitive matching.

### Rule 2: Rely strictly on Underlying Metrics
* Ignore generic standings. Focus strictly on underlying metrics:
  - **Rolling advanced stats**: PPDA, crosses in penalty area, and shot volumes.
  - **Manager form**: Points-per-game (PPG).
  - **Tactical Matchups**: Formation advantages (e.g., 4-3-3 vs 4-2-3-1).

### Rule 3: Strict Bankroll Management (Kelly Criterion)
* Strip the bookmaker's margin (de-juice) using the **Multiplicative Method**:
  $$P_{\text{fair}} = \frac{P_{\text{implied}}}{\sum P_{\text{implied}}}$$
* Evaluate Expected Value (EV):
  $$\text{EV} = (P_{\text{model}} \cdot \text{Odds}_{\text{bookmaker}}) - 1.0$$
* Calculate fractional Kelly stakes only for positive EV outcomes where $P_{\text{model}} > P_{\text{fair}}$:
  $$f^* = \frac{\text{EV}}{\text{Odds}_{\text{bookmaker}} - 1.0} \cdot \text{Fraction}$$
* Clamp stakes to the configured maximum bankroll fraction per bet.

### Rule 4: World Cup Tournament Rules
* **Neutral Venues**: Pass `is_neutral_venue=True` to strip standard Home-Field Advantage (HFA). If bookmaker odds are absent, fallback to symmetric baseline probabilities: $36\%$ Home / $28\%$ Draw / $36\%$ Away.
* **Host nation multiplier**: Apply a `1.07x` multiplier to a host team's pre-distribution baseline rating if `is_host_nation` is active.
* **Co-Host Clash Avoidance**: If two host nations (from USA, Mexico, Canada) play each other, do **NOT** apply `is_host_nation` multipliers. Treat as a pure neutral game.
* **Synthetic Advancement Fallback**: If 'To Advance' / 'To Qualify' markets are missing, calculate synthetic advancement probabilities $Q_H$ and $Q_A$ using:
  $$Q_H = P(\text{Win}_{90, H}) + P(\text{Draw}_{90}) \cdot P(\text{Win}_{\text{ET+PSO}, H})$$
  $$Q_A = P(\text{Win}_{90, A}) + P(\text{Draw}_{90}) \cdot P(\text{Win}_{\text{ET+PSO}, A})$$
  (Defaulting Extra Time + PSO weights to $0.5$ each).

### Rule 5: Global KeyError & Two-Way Market Protections
* When processing two-way markets (e.g., Over/Under, BTTS, To Advance) that lack a draw outcome, never index `["draw"]` directly.
* Use `.get("draw", 0.0)` or check `if "draw" in ...` across all runners, loggers, and evaluation engines to prevent runtime crashes.

---

## 3. Code Architecture & Directory Mappings

* **Entity Resolution**: [aliases.json](file:///C:/Users/Lamar%20Davies/Desktop/Nick%20ex/config/aliases.json) and [resolver.py](file:///C:/Users/Lamar%20Davies/Desktop/Nick%20ex/feedback_loop/resolver.py). Resolves team names and manager names.
* **API Scrapers**: [scrapers.py](file:///C:/Users/Lamar%20Davies/Desktop/Nick%20ex/data_pipeline/scrapers.py). Scrapes odds from The Odds API for `soccer_epl`, `soccer_spain_la_liga`, or `soccer_fifa_world_cup`.
* **Probability Engine**: [probability.py](file:///C:/Users/Lamar%20Davies/Desktop/Nick%20ex/calculation_engine/probability.py). Calculates probabilities for 1X2, Goals Over/Under, BTTS, Corners, and Tournament Advancement.
* **Kelly & EV Engine**: [kelly.py](file:///C:/Users/Lamar%20Davies/Desktop/Nick%20ex/calculation_engine/kelly.py) and [ev_calc.py](file:///C:/Users/Lamar%20Davies/Desktop/Nick%20ex/calculation_engine/ev_calc.py). Calculates de-juiced odds, expected values, and optimal stake sizes.
* **Accumulator Engine**: [accumulator.py](file:///C:/Users/Lamar%20Davies/Desktop/Nick%20ex/calculation_engine/accumulator.py). Groups top +EV independent legs into a parlay.
* **Database & JSON Loggers**: [logger.py](file:///C:/Users/Lamar%20Davies/Desktop/Nick%20ex/feedback_loop/logger.py) and [json_logger.py](file:///C:/Users/Lamar%20Davies/Desktop/Nick%20ex/feedback_loop/json_logger.py). Records SQL predictions and structured pre-match JSON documents.

---

## 4. Execution Step-by-Step

1. **Scrape Odds**:
   ```python
   from data_pipeline.scrapers import TheOddsAPIScraper
   scraper = TheOddsAPIScraper("config/settings.json")
   scraped_odds = scraper.scrape_odds("soccer_fifa_world_cup")
   ```
2. **Iterate & Resolve Fixtures**:
   Extract Home/Away teams and lookup managers (e.g. from a custom manager dict). Resolve names through `resolver.resolve_team` and `resolver.resolve_manager`.
3. **Execute Probability Engine**:
   Calculate baseline and tactical probabilities, applying neutral venue and host checks:
   ```python
   probs = prob_engine.calculate_match_probabilities(
       home_team, away_team, home_mgr, away_mgr,
       home_form, away_form, bookmaker_odds=book_1x2_odds,
       is_neutral_venue=True, is_host_nation=is_host_nation
   )
   ```
4. **Evaluate EV & Kelly Stakes**:
   Run `kelly_engine.evaluate_market_opportunities(probs, book_1x2_odds)` for 1X2, and evaluate Goals, BTTS, and Corners/Advancement.
5. **Log and Ticket**:
   - Write SQLite prediction records.
   - Save pre-match JSON documents in `predictions/` starting with the competition prefix (e.g., `wc_2026...`).
   - Run `build_premium_accumulator(all_value_bets)` and save output to `wc_accumulator_ticket.json`.
