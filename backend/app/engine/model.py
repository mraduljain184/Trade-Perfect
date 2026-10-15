"""Predictor: loads a trained artifact and scores live trades (PROJECT_CONTEXT §6.5-6.6, ML-3).

    p = Predictor.load("ml_artifacts/model_v1.joblib")      # raises ModelLoadError if missing/corrupt
    score_trades(records, refs, cfg, business_date, predictor=p)

SHAP speed rule (§6.6): batches > 100 rows use approximate SHAP (fast); <= 100 rows use exact SHAP.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from .config import SHAP_EXACT_MAX_ROWS, SCORE_DECIMALS
from .errors import ModelLoadError
from .features import FEATURE_LIST, FeatureEncoders, build_features
from .shap_utils import make_explainer, tree_shap_class1
from .types import ReferenceData

log = logging.getLogger(__name__)

ENGINE_SCHEMA_VERSION = 1           # must match train.ENGINE_SCHEMA_VERSION
REQUIRED_KEYS = {"model", "feature_list", "encoders", "decision_threshold", "version"}


@dataclass
class MLBatchResult:
    scores: list[float]                  # P(fail) per row
    features: list[dict]                 # feature name -> value (JSON-safe floats)
    shap_values: list[Optional[dict]]    # feature name -> SHAP for class 1, None if SHAP failed
    version: str


class Predictor:
    version: str
    feature_list: list[str]
    decision_threshold: float

    def __init__(self, artifact: dict, path: str = ""):
        self.path = path
        self.model = artifact["model"]
        self.version = str(artifact["version"])
        self.feature_list = list(artifact["feature_list"])
        self.decision_threshold = float(artifact["decision_threshold"])
        self.encoders = FeatureEncoders.from_dict(artifact["encoders"])
        self.trained_at = artifact.get("trained_at")
        self.algorithm = artifact.get("algorithm", type(self.model).__name__)
        self._explainer = None

    # ---- loading ------------------------------------------------------------
    @classmethod
    def load(cls, artifact_path: str) -> "Predictor":
        """Load + validate an artifact. Any problem -> ModelLoadError (backend falls back to rules-only)."""
        if not artifact_path or not os.path.isfile(artifact_path):
            raise ModelLoadError(f"Model artifact not found: {artifact_path}")
        try:
            artifact = joblib.load(artifact_path)
        except Exception as exc:                                       # corrupt / incompatible pickle
            raise ModelLoadError(f"Cannot read model artifact {artifact_path}: {exc}") from exc
        if not isinstance(artifact, dict) or not REQUIRED_KEYS <= set(artifact):
            raise ModelLoadError(f"{artifact_path} is not a Trade Perfect model artifact")
        if artifact.get("engine_schema_version") != ENGINE_SCHEMA_VERSION:
            raise ModelLoadError(f"{artifact_path} was built for engine schema "
                                 f"{artifact.get('engine_schema_version')}, expected {ENGINE_SCHEMA_VERSION} - retrain")
        if list(artifact["feature_list"]) != FEATURE_LIST:
            raise ModelLoadError(f"{artifact_path} feature list does not match features.py - retrain")
        if not hasattr(artifact["model"], "predict_proba"):
            raise ModelLoadError(f"{artifact_path} model has no predict_proba")
        p = cls(artifact, artifact_path)
        log.info("loaded model %s (%s) from %s", p.version, p.algorithm, artifact_path)
        return p

    # ---- core API (§8.3) ------------------------------------------------------
    def predict(self, features: pd.DataFrame) -> np.ndarray:
        """P(settlement fails) for each row."""
        return self.model.predict_proba(features[self.feature_list])[:, 1]

    def explain(self, features: pd.DataFrame, approximate: Optional[bool] = None) -> pd.DataFrame:
        """SHAP values for class 1, same columns as features.
        approximate=None -> automatic: exact for <= 100 rows, approximate for bigger batches."""
        if approximate is None:
            approximate = len(features) > SHAP_EXACT_MAX_ROWS
        if self._explainer is None:
            self._explainer = make_explainer(self.model)
        sv = tree_shap_class1(self._explainer, features[self.feature_list], approximate=approximate)
        return pd.DataFrame(sv, columns=self.feature_list, index=features.index)

    def build_features(self, df: pd.DataFrame, rule_score_ml, refs: ReferenceData) -> pd.DataFrame:
        return build_features(df, rule_score_ml, self.encoders, refs)

    # ---- used by pipeline.score_trades ------------------------------------------
    def score_batch(self, df: pd.DataFrame, rule_result, refs: ReferenceData, cfg=None) -> MLBatchResult:
        """Features -> probabilities -> SHAP for a batch of ACCEPTED trades (same row order)."""
        X = self.build_features(df, rule_result.rule_score_ml, refs)
        proba = self.predict(X)
        try:
            shap_df = self.explain(X)
            shaps = [{f: round(float(v), 6) for f, v in row.items()}
                     for row in shap_df.to_dict(orient="records")]
        except Exception:                                    # SHAP must never block scoring
            log.exception("SHAP failed for model %s - scores kept, explanations rules+score only", self.version)
            shaps = [None] * len(X)
        feats = [{f: round(float(v), 6) for f, v in row.items()} for row in X.to_dict(orient="records")]
        scores = [round(float(p), SCORE_DECIMALS) for p in proba]
        return MLBatchResult(scores=scores, features=feats, shap_values=shaps, version=self.version)