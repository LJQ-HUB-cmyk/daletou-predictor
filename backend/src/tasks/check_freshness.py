"""
数据新鲜度告警：官网抓取是否还活着

判断逻辑：读 draws 表最新 draw_date，若距今 > MAX_STALE_DAYS（默认 4 天）
则视为"抓取断流"——可能的原因：
- 体彩官网改版/反爬（fetch_history 解析失败）
- 网络链路问题（GitHub runner 访问被限）
- 上游 workflow 没跑起来（心跳断链）

大乐透开奖节奏：周一/三/六。最长间隔 = 周六 → 周一 ≈ 2.5 天。
所以 4 天还没新数据 → 肯定异常。

被 notify_backtest.py 在通知链末尾调用；异常时发独立告警通知。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta

from ..config import DATA_DIR
from ..utils.notifier import notify

DB_PATH = DATA_DIR / "daletou.db"
MAX_STALE_DAYS = 4


def latest_draw_date() -> date | None:
    """
    查 draws 表最新一条 draw_date

    @returns date 或 None（DB/表不存在）
    """
    if not DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT draw_date FROM draws ORDER BY issue DESC LIMIT 1"
            ).fetchone()
        if not row or not row[0]:
            return None
        return datetime.strptime(row[0], "%Y-%m-%d").date()
    except Exception as e:
        print(f"[freshness] 读 DB 异常: {e}")
        return None


def check_and_alert(force: bool = False) -> bool:
    """
    检查新鲜度，超限时推一条告警通知

    @param force 强制发通知（即使数据新鲜），用于调试
    @returns True 表示健康，False 表示告警已发出
    """
    latest = latest_draw_date()
    today = date.today()
    if latest is None:
        notify(
            "⚠️ DaLeTou 数据断流",
            "**draws 表为空或无法读取**\n\n"
            "可能 DB 损坏或首次 bootstrap 未完成。\n"
            "建议排查：`python -m backend.src.scraper.fetch_history`",
        )
        return False

    stale_days = (today - latest).days
    if stale_days <= MAX_STALE_DAYS and not force:
        print(f"[freshness] ✓ 最新开奖 {latest}，距今 {stale_days} 天，健康")
        return True

    run_url = ""
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if repo and run_id:
        run_url = f"{server}/{repo}/actions/runs/{run_id}"

    title = f"⚠️ DaLeTou 数据已 {stale_days} 天未更新"
    lines = [
        "**体彩官网抓取疑似断流**",
        "",
        f"- 最新 draws 日期：`{latest}`",
        f"- 今天：`{today}`（差 {stale_days} 天）",
        f"- 正常最大间隔应 ≤ {MAX_STALE_DAYS} 天（大乐透每周三开）",
        "",
        "**常见原因**：",
        "- 体彩官网改版/反爬，`fetch_history` 解析失败",
        "- GitHub runner 访问上游被限",
        "- 所有 workflow 都没跑起来（心跳断）",
        "",
        f"run: {run_url}" if run_url else "",
    ]
    notify(title, "\n".join([l for l in lines if l is not None]))
    print(f"[freshness] ✗ 数据陈旧（{stale_days} 天），已发告警")
    return False


if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv
    ok = check_and_alert(force=force)
    sys.exit(0 if ok else 1)
