import os
import sqlite3
import json

def get_db_connection(db_path="data/tactics_betting.db"):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path="data/tactics_betting.db"):
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Create predictions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        id TEXT PRIMARY KEY,
        match_date TEXT NOT NULL,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        home_manager TEXT NOT NULL,
        away_manager TEXT NOT NULL,
        home_predicted_formation TEXT NOT NULL,
        away_predicted_formation TEXT NOT NULL,
        implied_p_home REAL,
        implied_p_draw REAL,
        implied_p_away REAL,
        model_p_home REAL NOT NULL,
        model_p_draw REAL NOT NULL,
        model_p_away REAL NOT NULL,
        odds_home REAL,
        odds_draw REAL,
        odds_away REAL,
        calculated_ev_home REAL,
        calculated_ev_draw REAL,
        calculated_ev_away REAL,
        kelly_stake_home REAL,
        kelly_stake_draw REAL,
        kelly_stake_away REAL,
        placed_bet_type TEXT,
        placed_bet_odds REAL,
        placed_bet_stake REAL,
        status TEXT DEFAULT 'PENDING'
    )
    """)
    
    # Create match_outcomes table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS match_outcomes (
        prediction_id TEXT PRIMARY KEY,
        home_score INTEGER,
        away_score INTEGER,
        winner TEXT,
        home_actual_formation TEXT,
        away_actual_formation TEXT,
        net_profit_loss REAL,
        human_notes TEXT,
        FOREIGN KEY (prediction_id) REFERENCES predictions (id)
    )
    """)
    
    # Create manual_tactical_weights table to store calibration adjustments over time
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS model_weights (
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        baseline_weight REAL,
        manager_form_weight REAL,
        tactical_matchup_weight REAL
    )
    """)
    
    conn.commit()
    conn.close()

def log_prediction(pred_data, db_path="data/tactics_betting.db"):
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT OR REPLACE INTO predictions (
        id, match_date, home_team, away_team, home_manager, away_manager,
        home_predicted_formation, away_predicted_formation,
        implied_p_home, implied_p_draw, implied_p_away,
        model_p_home, model_p_draw, model_p_away,
        odds_home, odds_draw, odds_away,
        calculated_ev_home, calculated_ev_draw, calculated_ev_away,
        kelly_stake_home, kelly_stake_draw, kelly_stake_away,
        placed_bet_type, placed_bet_odds, placed_bet_stake, status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pred_data['id'], pred_data['match_date'], pred_data['home_team'], pred_data['away_team'],
        pred_data['home_manager'], pred_data['away_manager'],
        pred_data['home_predicted_formation'], pred_data['away_predicted_formation'],
        pred_data.get('implied_p_home'), pred_data.get('implied_p_draw'), pred_data.get('implied_p_away'),
        pred_data.get('model_p_home', 0.0), pred_data.get('model_p_draw', 0.0), pred_data.get('model_p_away', 0.0),
        pred_data.get('odds_home'), pred_data.get('odds_draw'), pred_data.get('odds_away'),
        pred_data.get('calculated_ev_home'), pred_data.get('calculated_ev_draw'), pred_data.get('calculated_ev_away'),
        pred_data.get('kelly_stake_home'), pred_data.get('kelly_stake_draw'), pred_data.get('kelly_stake_away'),
        pred_data.get('placed_bet_type', 'none'), pred_data.get('placed_bet_odds'), pred_data.get('placed_bet_stake'),
        pred_data.get('status', 'PENDING')
    ))
    conn.commit()
    conn.close()

def settle_prediction(prediction_id, outcome_data, db_path="data/tactics_betting.db"):
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Insert or replace in match_outcomes
    cursor.execute("""
    INSERT OR REPLACE INTO match_outcomes (
        prediction_id, home_score, away_score, winner,
        home_actual_formation, away_actual_formation, net_profit_loss, human_notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        prediction_id, outcome_data['home_score'], outcome_data['away_score'], outcome_data['winner'],
        outcome_data['home_actual_formation'], outcome_data['away_actual_formation'],
        outcome_data['net_profit_loss'], outcome_data.get('human_notes', '')
    ))
    
    # Update status in predictions table
    cursor.execute("""
    UPDATE predictions SET status = 'SETTLED' WHERE id = ?
    """, (prediction_id,))
    
    conn.commit()
    conn.close()

def get_pending_predictions(db_path="data/tactics_betting.db"):
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM predictions WHERE status = 'PENDING'")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_all_settled_predictions(db_path="data/tactics_betting.db"):
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT p.*, o.home_score, o.away_score, o.winner, o.home_actual_formation, o.away_actual_formation, o.net_profit_loss, o.human_notes
    FROM predictions p
    JOIN match_outcomes o ON p.id = o.prediction_id
    WHERE p.status = 'SETTLED'
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
