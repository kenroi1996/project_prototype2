"""
Integration Example: Two-Phase EarlyAlert System
=================================================

This file demonstrates how to wire all the services together.
Copy the relevant parts into your application entry points.
"""

import pandas as pd
from pathlib import Path

# ── Phase 1: TRAINING (historical data) ───────────────────────────────────

def train_on_historical_data(csv_path: str):
    """
    Train the model on historical first-year student records.
    These records already have Final_Avg_GRD from completed semesters.
    """
    from services.pipeline_orchestrator import PipelineOrchestrator
    from services.model_registry import ModelRegistry

    orchestrator = PipelineOrchestrator()

    results = orchestrator.run(
        excel_path=csv_path,
        model_type="random_forest",
        save_path="ml/outputs",
        on_step=lambda step, msg: print(f"[{step}] {msg}"),
    )

    print(f"\n✅ Training complete!")
    print(f"   Accuracy: {results['training_metrics']['accuracy']:.2%}")
    print(f"   Features: {len(results['training_metrics'].get('feature_names', []))}")
    print(f"   Model saved to: ml/outputs/trained_model.pkl")

    # The model is also auto-saved to ModelRegistry
    latest = ModelRegistry.load_latest_model()
    print(f"   Registry latest: {latest['path'] if latest else 'None'}")


# ── Phase 2: PREDICTION (new enrollments) ──────────────────────────────────

def predict_new_enrollments(csv_path: str):
    """
    Predict risk for incoming students.
    These records have pre-enrollment data only (no Final_Avg_GRD).
    """
    from services.feature_engineering import run_prediction_pipeline
    from services.prediction_engine import PredictionEngine
    from services.model_registry import ModelRegistry

    # 1. Load the trained model
    model_pkg = ModelRegistry.load_latest_model()
    if not model_pkg:
        raise RuntimeError("No trained model found. Run training first.")

    print(f"📦 Loaded model: {model_pkg['model_id']} "
          f"(accuracy: {model_pkg['metadata'].get('accuracy', 'N/A')})")

    # 2. Load new enrollment data
    df = pd.read_csv(csv_path)
    print(f"📄 Loaded {len(df)} new enrollment records")

    # 3. Run Phase 2 pipeline: normalize → engineer → drop_raw
    #    (no target definition — grades don't exist yet)
    df = run_prediction_pipeline(df)
    print(f"🔧 Engineered features: {list(df.columns)}")

    # 4. Run prediction
    result = PredictionEngine.run(
        model_data=model_pkg,
        df=df,
        progress_cb=lambda step, pct: print(f"  [{pct}%] {step}"),
    )

    if not result.success:
        print(f"❌ Prediction failed: {result.errors}")
        return

    # 5. Display results
    print(f"\n📊 Prediction Summary:")
    print(f"   Total students: {result.summary.total}")
    print(f"   High risk: {result.summary.high_risk} ({result.summary.high_risk_pct}%)")
    print(f"   Moderate risk: {result.summary.moderate_risk}")
    print(f"   Low risk: {result.summary.low_risk}")
    print(f"   Average risk score: {result.summary.overall_risk_score}")

    print(f"\n🔴 Top 5 High-Risk Students:")
    high_risk = [p for p in result.predictions if p["category"] == "high_risk"]
    high_risk.sort(key=lambda p: p["score"], reverse=True)
    for p in high_risk[:5]:
        print(f"   {p['student_id']} | {p['name']} | {p['program']} | "
              f"Score: {p['score']}% | Top factor: {p['factor']}")

    return result


# ── Alternative: Upload engineered file directly ───────────────────────────

def train_from_uploaded_file(file_path: str):
    """
    Train using a previously downloaded engineered dataset.
    Skips portal data collection entirely.
    """
    from services.feature_engineering import run_full_feature_pipeline
    from services.preprocessing_service import DataPipeline
    from services.ml_service import MLService
    from services.model_registry import ModelRegistry

    # Load engineered file
    if file_path.endswith('.csv'):
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)

    print(f"📁 Loaded engineered file: {len(df)} rows")

    # If already engineered, skip; otherwise run pipeline
    if 'risk_label' not in df.columns:
        df = run_full_feature_pipeline(df)

    # Preprocess
    pipeline = DataPipeline(df)
    pipeline._target_column = "risk_label"
    pipeline.remove_duplicates()
    pipeline.fill_missing(strategy="auto")
    pipeline.encode_categorical(drop_first=False)
    pipeline.scale_numerical(method="standard")

    X, y, feature_names = pipeline.prepare_features(target_col="risk_label")

    # Train
    ml_service = MLService()
    ml_service.feature_names = feature_names
    metrics = ml_service.train(X, y, model_type="random_forest")

    # Save
    ModelRegistry.save_model(
        ml_service.model,
        "rf",
        feature_names,
        metadata={
            "accuracy": metrics['accuracy'],
            "cv_mean": metrics['cv_mean'],
            "cv_std": metrics['cv_std'],
        },
    )

    print(f"✅ Training from upload complete! Accuracy: {metrics['accuracy']:.2%}")
    return ml_service


# ── Main entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    # Example usage:

    # Phase 1: Train on historical data
    # train_on_historical_data("data/historical_first_year.csv")

    # Phase 2: Predict on new enrollments
    # predict_new_enrollments("data/new_enrollments_2025.csv")

    # Or train from uploaded engineered file
    # train_from_uploaded_file("ml/outputs/engineered_dataset.csv")

    pass