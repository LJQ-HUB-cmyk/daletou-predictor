"""
大乐透组合特征库

对一张投注的号码组合，提取常用分析指标；同时支持从历史数据中批量提取。
后续用于：
- XGBoost / Transformer 等模型的手工特征输入
- 组合筛选器（过滤反常组合）
- 前端统计图表
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

from ..config import (
    BACK_COUNT,
    BACK_MAX,
    BACK_MIN,
    FRONT_COUNT,
    FRONT_MAX,
    FRONT_MIN,
)


@dataclass
class TicketFeatures:
    """
    投注组合的特征摘要
    """
    sum_front: int
    sum_back: int
    span_front: int  # 前区最大值 - 最小值
    span_back: int
    odd_count_front: int
    big_count_front: int  # 大号数量（>= mid）
    prime_count_front: int
    consec_count_front: int  # 连号数（相邻整数对的数量）
    tail_same_front: int  # 同尾号数量（最大同尾组大小）
    zone_counts_front: List[int]  # 三区分布：1-12 / 13-24 / 25-35
    ac_value: int  # AC 值：前区两两差值的去重数 - (k - 1)

    def to_vector(self) -> np.ndarray:
        """
        转为固定长度的 float 向量，供模型直接拼接
        """
        return np.array(
            [
                self.sum_front, self.sum_back, self.span_front, self.span_back,
                self.odd_count_front, self.big_count_front, self.prime_count_front,
                self.consec_count_front, self.tail_same_front,
                *self.zone_counts_front,
                self.ac_value,
            ],
            dtype=np.float32,
        )

    def to_dict(self) -> Dict:
        return asdict(self)


_PRIMES_FRONT = {2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31}


def _consec_count(nums: Sequence[int]) -> int:
    """
    连号对数：排序后相邻差 == 1 的次数

    例：[1,2,3,10,11] → 3（1-2, 2-3, 10-11）
    """
    s = sorted(nums)
    return sum(1 for i in range(len(s) - 1) if s[i + 1] - s[i] == 1)


def _tail_same_max(nums: Sequence[int]) -> int:
    """
    同尾号：个位数相同的号码最大成组大小（>=2 才算）
    """
    tails: Dict[int, int] = {}
    for n in nums:
        tails[n % 10] = tails.get(n % 10, 0) + 1
    return max(tails.values(), default=0)


def _ac_value(nums: Sequence[int]) -> int:
    """
    AC 值：两两之差的绝对值去重后，个数减去 (k - 1)。越大越"散"
    """
    s = sorted(nums)
    diffs = {abs(s[i] - s[j]) for i in range(len(s)) for j in range(i + 1, len(s))}
    return max(0, len(diffs) - (len(s) - 1))


def extract(front: Sequence[int], back: Sequence[int]) -> TicketFeatures:
    """
    抽取一张投注的全部特征

    @param front 前区号码列表
    @param back 后区号码列表
    @returns TicketFeatures
    """
    front = list(front)
    back = list(back)
    mid = (FRONT_MIN + FRONT_MAX) // 2  # 大小号分界（含 mid 算小号）
    zone_bounds = [(1, 12), (13, 24), (25, 35)]
    zone_counts = [
        sum(1 for n in front if lo <= n <= hi) for lo, hi in zone_bounds
    ]

    return TicketFeatures(
        sum_front=sum(front),
        sum_back=sum(back),
        span_front=max(front) - min(front),
        span_back=max(back) - min(back),
        odd_count_front=sum(1 for n in front if n % 2 == 1),
        big_count_front=sum(1 for n in front if n > mid),
        prime_count_front=sum(1 for n in front if n in _PRIMES_FRONT),
        consec_count_front=_consec_count(front),
        tail_same_front=_tail_same_max(front),
        zone_counts_front=zone_counts,
        ac_value=_ac_value(front),
    )


def missing_stats(history: pd.DataFrame, is_front: bool = True) -> Dict[int, int]:
    """
    计算各号码的遗漏值（距最后一次出现的期数；若从未出现返回 len(history)）

    @param history 历史 DataFrame，按 issue 升序
    @param is_front 是否前区
    @returns {号码: 遗漏值}
    """
    lo, hi = (FRONT_MIN, FRONT_MAX) if is_front else (BACK_MIN, BACK_MAX)
    key = "front" if is_front else "back"
    miss: Dict[int, int] = {n: len(history) for n in range(lo, hi + 1)}
    n_hist = len(history)
    for idx, row in enumerate(history.itertuples(index=False)):
        gap_from_now = n_hist - 1 - idx
        for num in getattr(row, key):
            miss[num] = gap_from_now
    return miss


def history_feature_bounds(
    history: pd.DataFrame, recent: int = 100_000, quantile: tuple = (0.05, 0.95)
) -> Dict[str, tuple]:
    """
    从历史开奖中学习各指标的合理区间，供筛选器使用

    @param history 历史数据
    @param recent 仅用最近 N 期（默认极大，即全量可用历史）
    @param quantile 分位区间（下限, 上限）
    @returns {指标名: (lo, hi)}
    """
    q_lo, q_hi = quantile
    tail = history.tail(recent)
    feats: List[np.ndarray] = []
    for r in tail.itertuples(index=False):
        feats.append(extract(r.front, r.back).to_vector())
    arr = np.stack(feats)
    names = [
        "sum_front", "sum_back", "span_front", "span_back",
        "odd_count_front", "big_count_front", "prime_count_front",
        "consec_count_front", "tail_same_front",
        "zone0", "zone1", "zone2", "ac_value",
    ]
    bounds = {}
    for i, name in enumerate(names):
        bounds[name] = (
            float(np.quantile(arr[:, i], q_lo)),
            float(np.quantile(arr[:, i], q_hi)),
        )
    return bounds
