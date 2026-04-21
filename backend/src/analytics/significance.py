"""
显著性检验：判决 9 个模型是否"统计意义上" 跑赢了随机基线

理论基础
-------
大乐透前区单号单期出现概率 = C(34,4)/C(35,5) = 5/35 ≈ 0.14286
后区单号单期出现概率 = 2/12 ≈ 0.16667

对某模型在 N 期回测中的预测，设它"号码命中事件"总次数为 K（每期贡献
5 个前区槽和 2 个后区槽），则在"模型实为随机"的原假设下：

    K ~ Binomial(n = N * slots, p = theoretical)

本模块给出：
  - Wilson 95% 置信区间
  - 精确 / 正态近似二项检验 p 值（双侧）
  - 判决词：显著偏离 / 与随机不可区分
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Dict, List

from ..config import BACK_COUNT, BACK_MAX, EXPORT_DIR, FRONT_COUNT, FRONT_MAX
from ..db import get_conn

THEORETICAL_FRONT = FRONT_COUNT / FRONT_MAX
THEORETICAL_BACK = BACK_COUNT / BACK_MAX

Z_95 = 1.959964


def wilson_ci(k: int, n: int, z: float = Z_95) -> tuple[float, float, float]:
    """
    Wilson score 置信区间（比正态近似更稳，即使 p 接近 0 / 1 / n 较小都成立）

    @param k 成功次数
    @param n 总次数
    @param z 置信水平对应的 z 分数（默认 95%）
    @returns (p_hat, ci_low, ci_high)
    """
    if n == 0:
        return 0.0, 0.0, 1.0
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def _normal_sf(x: float) -> float:
    """标准正态分布的 1-CDF（survival function）"""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def binom_test_two_sided(k: int, n: int, p0: float) -> float:
    """
    双侧二项检验 p 值

    @param k 观测到的成功次数
    @param n 总试验次数
    @param p0 原假设下的概率
    @returns 双侧 p 值
    """
    if n == 0:
        return 1.0
    if n <= 1000:
        log_p0 = math.log(p0) if p0 > 0 else float("-inf")
        log_q0 = math.log(1 - p0) if p0 < 1 else float("-inf")
        log_coef = [0.0] * (n + 1)
        for i in range(1, n + 1):
            log_coef[i] = log_coef[i - 1] + math.log((n - i + 1) / i)
        log_pmf = [log_coef[i] + i * log_p0 + (n - i) * log_q0 for i in range(n + 1)]
        m = max(log_pmf)
        pmf = [math.exp(x - m) for x in log_pmf]
        norm = sum(pmf)
        pmf = [x / norm for x in pmf]
        obs = pmf[k]
        return float(sum(x for x in pmf if x <= obs + 1e-12))
    mu = n * p0
    sigma = math.sqrt(n * p0 * (1.0 - p0))
    z = (k - mu) / sigma
    return float(2.0 * _normal_sf(abs(z)))


def _collect_per_model_hits() -> Dict[str, dict]:
    """
    从 results 表中聚合每个模型的 (命中前区数, 命中后区数, 期数, 注数)

    @returns model -> {front_hits, front_slots, back_hits, back_slots,
                       issues, tickets}
    """
    stat: Dict[str, dict] = defaultdict(lambda: {
        "front_hits": 0,
        "front_slots": 0,
        "back_hits": 0,
        "back_slots": 0,
        "issues": set(),
        "tickets": 0,
    })
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT issue, model, ticket_idx, front_hit, back_hit
            FROM results
        """).fetchall()
    for r in rows:
        s = stat[r["model"]]
        s["front_hits"] += int(r["front_hit"])
        s["back_hits"] += int(r["back_hit"])
        s["front_slots"] += FRONT_COUNT
        s["back_slots"] += BACK_COUNT
        s["issues"].add(r["issue"])
        s["tickets"] += 1
    return stat


def compute_significance() -> List[dict]:
    """
    给每个模型生成"显著性卡片"

    @returns 列表，每项形如
      {
        "model": "bayesian",
        "issues": 412,
        "tickets": 412,
        "front": {
          "hits": 298, "slots": 2060,
          "observed": 0.1447, "theoretical": 0.14286,
          "ci95": [0.129, 0.161],
          "p_value": 0.83, "significant": False
        },
        "back":  { ... 同上 },
        "verdict": "与随机基线在 α=0.05 下不可区分"
      }
    """
    raw = _collect_per_model_hits()
    out: List[dict] = []
    for model, s in raw.items():
        f_hits, f_slots = s["front_hits"], s["front_slots"]
        b_hits, b_slots = s["back_hits"], s["back_slots"]

        f_obs, f_lo, f_hi = wilson_ci(f_hits, f_slots)
        b_obs, b_lo, b_hi = wilson_ci(b_hits, b_slots)
        f_p = binom_test_two_sided(f_hits, f_slots, THEORETICAL_FRONT)
        b_p = binom_test_two_sided(b_hits, b_slots, THEORETICAL_BACK)
        f_sig = f_p < 0.05
        b_sig = b_p < 0.05

        low_sample = len(s["issues"]) < 30
        if low_sample:
            verdict = (
                f"样本不足（仅 {len(s['issues'])} 期），无法做统计判决；"
                "需要 ≥30 期回测才能给出置信区间较窄的结论。"
            )
        elif f_sig or b_sig:
            parts = []
            if f_sig:
                parts.append(
                    f"前区{('高于' if f_obs > THEORETICAL_FRONT else '低于')}随机基线 (p={f_p:.3g})"
                )
            if b_sig:
                parts.append(
                    f"后区{('高于' if b_obs > THEORETICAL_BACK else '低于')}随机基线 (p={b_p:.3g})"
                )
            verdict = "显著偏离随机：" + "；".join(parts)
        else:
            verdict = f"与随机基线在 α=0.05 下不可区分 (前区 p={f_p:.3g}, 后区 p={b_p:.3g})"

        out.append({
            "model": model,
            "issues": len(s["issues"]),
            "tickets": s["tickets"],
            "front": {
                "hits": f_hits,
                "slots": f_slots,
                "observed": round(f_obs, 6),
                "theoretical": round(THEORETICAL_FRONT, 6),
                "ci95": [round(f_lo, 6), round(f_hi, 6)],
                "p_value": round(f_p, 6),
                "significant": f_sig,
            },
            "back": {
                "hits": b_hits,
                "slots": b_slots,
                "observed": round(b_obs, 6),
                "theoretical": round(THEORETICAL_BACK, 6),
                "ci95": [round(b_lo, 6), round(b_hi, 6)],
                "p_value": round(b_p, 6),
                "significant": b_sig,
            },
            "verdict": verdict,
        })
    out.sort(key=lambda x: x["model"])
    return out


def export_significance() -> None:
    """
    把显著性检验结果导出到前端可读的 JSON
    """
    data = {
        "methodology": {
            "name": "Walk-forward backtest + Binomial test",
            "description": (
                "对每个模型：每期只用此期之前的历史训练/拟合，单期单模型出 1 注预测；"
                "开奖后记录命中个数。汇总 N 期后，对号码命中事件做二项检验 "
                "(H0: 模型 ≡ 随机抽号)，并给出 Wilson 95% 置信区间。"
            ),
            "theoretical_front": round(THEORETICAL_FRONT, 6),
            "theoretical_back": round(THEORETICAL_BACK, 6),
            "significance_level": 0.05,
        },
        "models": compute_significance(),
    }
    path = EXPORT_DIR / "significance.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"已导出 {path}")


if __name__ == "__main__":
    export_significance()
