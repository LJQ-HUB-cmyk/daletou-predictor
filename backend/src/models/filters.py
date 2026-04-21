"""
组合筛选器

模型抽出候选号码后，检查其特征是否落在"历史合理区间"。
不把样本一票否决，而是计算偏离度得分，偏离过大才拒绝。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import pandas as pd

from ..config import MAX_HISTORY_WINDOW
from ..utils.features import extract, history_feature_bounds


class CombinationFilter:
    """
    基于历史分位学到的指标区间，做软筛选

    - 对每个指标检查是否落在 (lo, hi) 内
    - 超出比例 > tolerance（如 30%）的组合判为"反常"
    - 仅启用部分关键指标（避免过严）
    """

    KEY_METRICS = (
        "sum_front", "span_front", "odd_count_front",
        "big_count_front", "consec_count_front", "ac_value",
    )

    def __init__(self, history: pd.DataFrame, recent: int = MAX_HISTORY_WINDOW) -> None:
        """
        @param recent 用于学指标分位区间的期数（默认全量窗口上限）
        """
        r = min(len(history), max(1, recent))
        self.bounds: Dict[str, Tuple[float, float]] = history_feature_bounds(
            history, recent=r, quantile=(0.05, 0.95)
        )

    def evaluate(self, front, back) -> Tuple[bool, int, list]:
        """
        @returns (是否通过, 违规条目数, 违规指标列表)
        """
        feats = extract(front, back).to_dict()
        violations = []
        for name in self.KEY_METRICS:
            lo, hi = self.bounds.get(name, (-1e9, 1e9))
            v = feats[name]
            if v < lo or v > hi:
                violations.append((name, v, lo, hi))
        passed = len(violations) <= 1
        return passed, len(violations), violations

    def is_reasonable(self, front, back) -> bool:
        """
        快速版：只返回 bool
        """
        return self.evaluate(front, back)[0]
