"""Thin wrapper around shap.TreeExplainer so train.py and model.py read SHAP the same way,
whatever the installed shap version returns (list per class, or array [rows, features, classes])."""
from __future__ import annotations

import numpy as np
import pandas as pd


def tree_shap_class1(explainer, X: pd.DataFrame, approximate: bool = False) -> np.ndarray:
    """SHAP values for class 1 (= settlement FAILED) as an array [rows, features]."""
    sv = explainer.shap_values(X, approximate=approximate, check_additivity=False)
    if isinstance(sv, list):                 # older shap: [class0, class1]
        sv = sv[1]
    sv = np.asarray(sv)
    if sv.ndim == 3:                         # newer shap: [rows, features, classes]
        sv = sv[:, :, 1]
    return sv


def make_explainer(model):
    import shap                              # imported lazily: rules-only mode never needs shap
    return shap.TreeExplainer(model)