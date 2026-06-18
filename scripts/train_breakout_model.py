"""Train the LightGBM breakout success predictor.

Run this script once (or after accumulating new historical data) to train
or retrain the ML model used by MLPipeline (Step 10 of run.py).

What it does:
    1. Runs a full historical backtest (all available dates, breakout_signal)
    2. Fetches rich feature data for every entry (composite_score, stage2_days,
       mtf_score, pa_score, regime_score, signal quality flags, base geometry)
    3. Labels each trade: 1 = hit profit target (TARGET), 0 = stopped out or timed out
    4. Trains a LightGBM binary classifier (regime-aware — bull/bear context included)
    5. Prints evaluation metrics (AUC, accuracy, precision, recall, feature importance)
    6. Saves model to models/breakout_predictor_v1.joblib

After running:
    - MLPipeline (Step 10 of run.py) will automatically load the model and
      score today's scanner candidates with P(breakout hits target)
    - Scanner shows ML% column (colored green ≥ 65%, orange ≥ 45%, red < 45%)
    - ML signal column (1 if ML% ≥ 60%) is available for "ML Picks" preset

Usage:
    python scripts/train_breakout_model.py

Notes:
    - Requires at least MLConfig.MIN_TRAINING_SAMPLES (50) labeled trades
    - Run after running the main pipeline (run.py) at least once so
      stock_signals, stock_mtf_signals, stock_pa_signals are populated
    - Re-run periodically (e.g. monthly) to incorporate new historical data
    - Model version tag = MLConfig.BREAKOUT_MODEL_FILE; bump manually when
      changing FEATURE_COLUMNS or hyperparameters for traceability
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.logging_config import get_logger
from app.config.strategy_config import MLConfig
from app.ml.backtest_labeler import BacktestLabeler
from app.ml.breakout_predictor import BreakoutPredictor

logger = get_logger("train_breakout_model")


def main() -> None:
    """Entry point — generate labels, train model, print results, save."""
    logger.info("=" * 60)
    logger.info("Breakout ML Training — LightGBM Regime-Aware Predictor")
    logger.info("=" * 60)
    logger.info("Signal:    %s", MLConfig.LABELER_SIGNAL)
    logger.info("Stop%%:     %.0f%%", MLConfig.LABELER_STOP_PCT * 100)
    logger.info("Target%%:   %.0f%%", MLConfig.LABELER_TARGET_PCT * 100)
    logger.info("Hold days: %d", MLConfig.LABELER_MAX_HOLDING_DAYS)
    logger.info("Features:  %s", MLConfig.FEATURE_COLUMNS)
    logger.info("-" * 60)

    # ── Step 1: Generate labeled training data ────────────────────────────────
    logger.info("Step 1/3: Generating labeled training data from backtest history...")
    labeled_df = BacktestLabeler.generate()

    if labeled_df.empty:
        logger.error(
            "No labeled data generated. Ensure the main pipeline (run.py) "
            "has run at least once and stock_signals is populated."
        )
        sys.exit(1)

    logger.info(
        "Labeled dataset: %d samples | wins=%d (%.1f%%) | losses=%d (%.1f%%)",
        len(labeled_df),
        labeled_df["label"].sum(),
        labeled_df["label"].mean() * 100,
        (labeled_df["label"] == 0).sum(),
        (1 - labeled_df["label"].mean()) * 100,
    )

    # ── Step 2: Train model ──────────────────────────────────────────────────
    logger.info("Step 2/3: Training LightGBM model...")
    predictor = BreakoutPredictor()

    try:
        metrics = predictor.train(labeled_df)
    except ValueError as exc:
        logger.error("Training aborted: %s", exc)
        sys.exit(1)

    logger.info("-" * 60)
    logger.info("TRAINING RESULTS")
    logger.info("  Train samples : %d  (win rate: %.1f%%)", metrics["n_train"], metrics["win_rate_train"] * 100)
    logger.info("  Test  samples : %d  (win rate: %.1f%%)", metrics["n_test"],  metrics["win_rate_test"]  * 100)
    logger.info("  AUC           : %.3f", metrics["auc"])
    logger.info("  Accuracy      : %.1f%%", metrics["accuracy"] * 100)
    logger.info("  Precision     : %.1f%%", metrics["precision"] * 100)
    logger.info("  Recall        : %.1f%%", metrics["recall"] * 100)
    logger.info("  Top features  :")
    for feat, imp in metrics["top5_features"].items():
        logger.info("    %-30s %d", feat, imp)

    # ── Step 3: Save model ───────────────────────────────────────────────────
    model_path = MLConfig.MODEL_DIR / MLConfig.BREAKOUT_MODEL_FILE
    logger.info("Step 3/3: Saving model → %s", model_path)
    predictor.save(model_path)

    logger.info("=" * 60)
    logger.info("Training complete. Run run.py to activate ML scoring.")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
