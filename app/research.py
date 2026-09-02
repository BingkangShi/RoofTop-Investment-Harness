"""Local factor research and analysis-only backtesting.

AKShare-derived market bars are read from the local system of record. AKQuant
provides the expression engine and event-driven backtester. Nothing in this
module can connect to a broker or submit a real order.
"""

from __future__ import annotations

import json
import math
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import DATA_LAKE, connect


STRATEGIES = (
    {
        "key": "ma_trend_20_60", "name": "20/60 日均线趋势", "category": "趋势",
        "description": "只用前一交易日及更早数据；MA20 高于 MA60 入场，反向退出。",
        "implementation": "akquant_functional_ma", "factors": ("momentum_20d",),
        "risk": dict(stop_loss_pct=.07, take_profit_pct=.20, trailing_stop_pct=.08,
                     max_position_pct=.35, max_drawdown_pct=.16, max_daily_loss_pct=.035,
                     turnover_limit=8.0, liquidity_rule="近20日平均成交额不足2000万元时禁止产生新信号"),
    },
    {
        "key": "mean_reversion_z20", "name": "20 日价格偏离反转", "category": "均值回归",
        "description": "昨日价格低于 MA20 一个标准差时研究入场，回到均线退出。",
        "implementation": "akquant_functional_zscore", "factors": ("price_reversion_z20", "volatility_20d"),
        "risk": dict(stop_loss_pct=.05, take_profit_pct=.10, trailing_stop_pct=.04,
                     max_position_pct=.25, max_drawdown_pct=.10, max_daily_loss_pct=.025,
                     turnover_limit=12.0, liquidity_rule="近20日平均成交额不足3000万元时禁止产生新信号"),
    },
    {
        "key": "breakout_20", "name": "20 日高点突破", "category": "突破",
        "description": "昨日收盘突破此前20日高点时研究入场，跌破10日低点退出。",
        "implementation": "akquant_functional_breakout", "factors": ("momentum_20d", "volume_acceleration_5d"),
        "risk": dict(stop_loss_pct=.06, take_profit_pct=.18, trailing_stop_pct=.07,
                     max_position_pct=.30, max_drawdown_pct=.14, max_daily_loss_pct=.03,
                     turnover_limit=10.0, liquidity_rule="突破日成交量需高于20日均量，否则不产生新信号"),
    },
)

FACTORS = (
    ("momentum_20d", "20 日动量", "动量", "Delta(Close, 20) / Delay(Close, 20)", 1,
     "20 日价格变化率，正向因子。"),
    ("price_reversion_z20", "20 日价格反转 Z 值", "反转", "(Ts_Mean(Close, 20) - Close) / Ts_Std(Close, 20)", 1,
     "价格越低于20日均线，数值越高。"),
    ("volatility_20d", "20 日实现波动", "风险", "Ts_Std(Close / Delay(Close, 1) - 1, 20)", -1,
     "日收益率20日标准差，风险惩罚方向。"),
    ("volume_acceleration_5d", "5 日量能加速度", "量价", "Delta(Volume, 5) / Ts_Mean(Volume, 20)", 1,
     "5日成交量变化相对20日均量。"),
)

FACTOR_PROFILES = {
    "momentum_20d": {
        "advantages": ["趋势延续期逻辑直观", "表达式简单、可审计", "与趋势策略容易组合"],
        "limitations": ["震荡期容易反复失效", "急跌时回撤可能放大", "对回看窗口较敏感"],
    },
    "price_reversion_z20": {
        "advantages": ["适合寻找短期过度悲观", "与逆向情绪纪律一致", "信号尺度经过波动标准化"],
        "limitations": ["下跌趋势中可能持续接刀", "均值本身会移动", "必须配合止损和基本面排雷"],
    },
    "volatility_20d": {
        "advantages": ["可直接作为风险惩罚项", "有助于控制组合波动", "不依赖价格绝对水平"],
        "limitations": ["低波动不等于低风险", "突发事件前可能失真", "单独使用通常不是买入信号"],
    },
    "volume_acceleration_5d": {
        "advantages": ["能识别资金关注度变化", "可作为突破信号确认", "和纯价格因子互补"],
        "limitations": ["放量也可能对应出货", "不同标的成交口径不完全一致", "容易受单日异常成交干扰"],
    },
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed_research_catalog() -> None:
    now = utcnow()
    with closing(connect()) as conn:
        for item in STRATEGIES:
            conn.execute(
                """INSERT INTO strategies(strategy_key,name,category,description,implementation,
                   source_framework,version,status,created_at,updated_at) VALUES(?,?,?,?,?,'AKQuant 0.3.38','0.1','RESEARCH',?,?)
                   ON CONFLICT(strategy_key) DO UPDATE SET name=excluded.name,description=excluded.description,
                   implementation=excluded.implementation,updated_at=excluded.updated_at""",
                (item["key"], item["name"], item["category"], item["description"], item["implementation"], now, now),
            )
            strategy_id = conn.execute("SELECT id FROM strategies WHERE strategy_key=?", (item["key"],)).fetchone()[0]
            risk = item["risk"]
            stress = json.dumps({"gap_down": "按-8%跳空重算", "liquidity_freeze": "无法成交时不假定红线成交",
                                 "correlation_spike": "相关性升至0.9时重算组合回撤"}, ensure_ascii=False)
            conn.execute(
                """INSERT INTO strategy_risk_policies(strategy_id,stop_loss_pct,take_profit_pct,trailing_stop_pct,
                   max_position_pct,max_drawdown_pct,max_daily_loss_pct,turnover_limit,liquidity_rule,
                   stress_rules_json,version,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'0.1',?)
                   ON CONFLICT(strategy_id) DO UPDATE SET stop_loss_pct=excluded.stop_loss_pct,
                   take_profit_pct=excluded.take_profit_pct,trailing_stop_pct=excluded.trailing_stop_pct,
                   max_position_pct=excluded.max_position_pct,max_drawdown_pct=excluded.max_drawdown_pct,
                   max_daily_loss_pct=excluded.max_daily_loss_pct,turnover_limit=excluded.turnover_limit,
                   liquidity_rule=excluded.liquidity_rule,stress_rules_json=excluded.stress_rules_json,
                   updated_at=excluded.updated_at""",
                (strategy_id, risk["stop_loss_pct"], risk["take_profit_pct"], risk["trailing_stop_pct"],
                 risk["max_position_pct"], risk["max_drawdown_pct"], risk["max_daily_loss_pct"],
                 risk["turnover_limit"], risk["liquidity_rule"], stress, now),
            )
        for key, name, family, expression, direction, description in FACTORS:
            conn.execute(
                """INSERT INTO factors(factor_key,name,family,expression,direction,description,
                   source_framework,version,status,created_at) VALUES(?,?,?,?,?,?,'AKQuant FactorEngine 0.3.38','0.1','RESEARCH',?)
                   ON CONFLICT(factor_key) DO UPDATE SET name=excluded.name,expression=excluded.expression,
                   description=excluded.description""", (key, name, family, expression, direction, description, now),
            )
        for item in STRATEGIES:
            strategy_id = conn.execute("SELECT id FROM strategies WHERE strategy_key=?", (item["key"],)).fetchone()[0]
            for factor_key in item["factors"]:
                factor_id = conn.execute("SELECT id FROM factors WHERE factor_key=?", (factor_key,)).fetchone()[0]
                conn.execute("INSERT OR REPLACE INTO strategy_factors(strategy_id,factor_id,weight,role) VALUES(?,?,1,'SIGNAL')",
                             (strategy_id, factor_id))
        conn.commit()


def _daily_frame(symbol: str):
    import pandas as pd
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT trade_date AS date,open,high,low,close,COALESCE(volume,0) AS volume,
                      COALESCE(amount,0) AS amount FROM market_daily_bars
               WHERE asset_symbol=? ORDER BY trade_date""", (symbol,),
        ).fetchall()
    if not rows:
        raise ValueError(f"没有 {symbol} 的本地日线")
    frame = pd.DataFrame([dict(row) for row in rows])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["symbol"] = symbol
    return frame


def _strategy_callbacks(strategy_key: str, risk: dict[str, float]):
    def initialize(ctx):
        ctx.strategy_key = strategy_key
        ctx.warmup_period = 62
        ctx.entry_price = 0.0
        ctx.peak_price = 0.0

    def on_bar(ctx, bar):
        history = ctx.get_history(count=62, symbol=bar.symbol, field="close")
        if len(history) < 62:
            return
        prior = history[:-1]
        position = ctx.get_position(bar.symbol)
        if position > 0:
            ctx.peak_price = max(ctx.peak_price, float(bar.close))
            pnl = float(bar.close) / ctx.entry_price - 1 if ctx.entry_price else 0
            trailing = float(bar.close) / ctx.peak_price - 1 if ctx.peak_price else 0
            if pnl <= -risk["stop_loss_pct"] or pnl >= risk["take_profit_pct"] or trailing <= -risk["trailing_stop_pct"]:
                ctx.close_position(bar.symbol)
                return
        enter = exit_ = False
        if strategy_key == "ma_trend_20_60":
            ma20, ma60 = prior[-20:].mean(), prior[-60:].mean()
            enter, exit_ = ma20 > ma60, ma20 < ma60
        elif strategy_key == "mean_reversion_z20":
            mean, std = prior[-20:].mean(), prior[-20:].std()
            enter, exit_ = prior[-1] < mean - std, prior[-1] >= mean
        elif strategy_key == "breakout_20":
            enter, exit_ = prior[-1] >= prior[-21:-1].max(), prior[-1] <= prior[-11:-1].min()
        if position <= 0 and enter:
            ctx.order_target_percent(symbol=bar.symbol, target_percent=risk["max_position_pct"])
            ctx.entry_price = float(bar.close)
            ctx.peak_price = float(bar.close)
        elif position > 0 and exit_:
            ctx.close_position(bar.symbol)
    return initialize, on_bar


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _json_value(value.item())
    return str(value)


def _curve_points(series, max_points: int = 260) -> list[dict]:
    if series is None or len(series) == 0:
        return []
    step = max(1, math.ceil(len(series) / max_points))
    indices = list(range(0, len(series), step))
    if indices[-1] != len(series) - 1:
        indices.append(len(series) - 1)
    return [{"time": str(series.index[index])[:10], "value": round(float(series.iloc[index]), 6)} for index in indices]


def _normalized_buy_hold(frame) -> list[dict]:
    close = frame.set_index("date")["close"].astype(float)
    return _curve_points(close / close.iloc[0] * 100000)


def run_backtest(strategy_key: str, symbol: str = "510300") -> dict:
    import akquant as aq
    seed_research_catalog()
    frame = _daily_frame(symbol)
    with closing(connect()) as conn:
        row = conn.execute(
            """SELECT s.id,s.strategy_key,r.* FROM strategies s JOIN strategy_risk_policies r ON r.strategy_id=s.id
               WHERE s.strategy_key=?""", (strategy_key,),
        ).fetchone()
        if not row:
            raise ValueError("未知策略")
        policy = dict(row)
        started = utcnow()
        config = {"initial_cash": 100000, "commission_rate": .0001, "t_plus_one": True,
                  "signal_lag": "只用上一交易日及更早数据", "order_execution": False}
        cursor = conn.execute(
            """INSERT INTO backtest_runs(strategy_id,asset_symbol,framework,data_start,data_end,started_at,status,config_json)
               VALUES(?,?,?,?,?,?,'RUNNING',?)""",
            (row["id"], symbol, "AKQuant 0.3.38", frame.date.min().date().isoformat(),
             frame.date.max().date().isoformat(), started, json.dumps(config, ensure_ascii=False)),
        )
        run_id = cursor.lastrowid
        conn.commit()
    try:
        initialize, on_bar = _strategy_callbacks(strategy_key, policy)
        result = aq.run_backtest(
            data=frame, strategy=on_bar, initialize=initialize, initial_cash=100000,
            commission_rate=.0001, min_commission=0, t_plus_one=True, show_progress=False,
            risk_config={"max_position_pct": policy["max_position_pct"],
                         "max_account_drawdown": policy["max_drawdown_pct"],
                         "max_daily_loss": policy["max_daily_loss_pct"]},
        )
        metrics = {str(index): _json_value(value) for index, value in result.metrics_df["value"].items()}
        curve = _curve_points(result.equity_curve)
        benchmark_curve = _normalized_buy_hold(frame)
        output = {"run_id": run_id, "strategy_key": strategy_key, "symbol": symbol, "metrics": metrics,
                  "data_start": frame.date.min().date().isoformat(), "data_end": frame.date.max().date().isoformat(),
                  "equity_curve": curve, "benchmark_curve": benchmark_curve}
        raw_path = DATA_LAKE / "research" / "backtests" / f"backtest_{run_id}.json"
        raw_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        with closing(connect()) as conn:
            conn.execute("UPDATE backtest_runs SET finished_at=?,status='SUCCESS',metrics_json=?,raw_path=? WHERE id=?",
                         (utcnow(), json.dumps(metrics, ensure_ascii=False), str(raw_path), run_id))
            conn.execute("UPDATE backtest_runs SET equity_json=? WHERE id=?",
                         (json.dumps({"strategy": curve, "benchmark": benchmark_curve}, ensure_ascii=False), run_id))
            conn.commit()
        return output
    except Exception as exc:
        with closing(connect()) as conn:
            conn.execute("UPDATE backtest_runs SET finished_at=?,status='FAILED',error=? WHERE id=?", (utcnow(), str(exc), run_id))
            conn.commit()
        raise


def run_factor(factor_key: str) -> dict:
    import pandas as pd
    from akquant.data import ParquetDataCatalog
    from akquant.factor import FactorEngine
    seed_research_catalog()
    catalog_path = DATA_LAKE / "research" / "factors" / "akquant_catalog"
    catalog = ParquetDataCatalog(root_path=str(catalog_path))
    with closing(connect()) as conn:
        factor = conn.execute("SELECT * FROM factors WHERE factor_key=?", (factor_key,)).fetchone()
        symbols = [row[0] for row in conn.execute("SELECT DISTINCT asset_symbol FROM market_daily_bars ORDER BY asset_symbol")]
        if not factor:
            raise ValueError("未知因子")
        cursor = conn.execute(
            "INSERT INTO factor_runs(factor_id,universe,started_at,status,params_json) VALUES(?,?,?,'RUNNING',?)",
            (factor["id"], ",".join(symbols), utcnow(), json.dumps({"forward_days": 5}, ensure_ascii=False)),
        )
        run_id = cursor.lastrowid
        conn.commit()
    try:
        market = []
        for symbol in symbols:
            frame = _daily_frame(symbol)
            market.append(frame[["date", "symbol", "close"]].copy())
            catalog.write(symbol, frame.set_index("date"))
        result = FactorEngine(catalog).run(factor["expression"]).to_pandas()
        prices = pd.concat(market, ignore_index=True).sort_values(["symbol", "date"])
        prices["forward_return_5d"] = prices.groupby("symbol")["close"].shift(-5) / prices["close"] - 1
        prices["forward_return_1d"] = prices.groupby("symbol")["close"].shift(-1) / prices["close"] - 1
        evaluated = result.merge(prices[["date", "symbol", "forward_return_5d", "forward_return_1d"]],
                                 on=["date", "symbol"], how="left").dropna()
        daily_ic = evaluated.groupby("date").apply(
            lambda group: group["factor_value"].corr(group["forward_return_5d"]) if len(group) >= 3 else float("nan"),
            include_groups=False,
        ).dropna()
        daily_rank_ic = evaluated.groupby("date").apply(
            lambda group: group["factor_value"].rank().corr(group["forward_return_5d"].rank()) if len(group) >= 3 else float("nan"),
            include_groups=False,
        ).dropna()
        direction = int(factor["direction"])
        evaluated["score"] = evaluated["factor_value"] * direction
        evaluated["rank"] = evaluated.groupby("date")["score"].rank(method="first", ascending=False)
        selected = evaluated[evaluated["rank"] == 1].sort_values("date").copy()
        selected["strategy_return"] = selected["forward_return_1d"].clip(-.15, .15)
        selected["equity"] = (1 + selected["strategy_return"].fillna(0)).cumprod() * 100
        factor_curve = _curve_points(selected.set_index("date")["equity"])
        simulated_return = float(selected["equity"].iloc[-1] - 100) if len(selected) else None
        selected_win_rate = float((selected["strategy_return"] > 0).mean() * 100) if len(selected) else None
        metrics = {"ic": _json_value(daily_ic.mean()), "rank_ic": _json_value(daily_rank_ic.mean()),
                   "ic_observations": int(len(daily_ic)), "forward_days": 5,
                   "simulated_return_pct": simulated_return, "selected_win_rate_pct": selected_win_rate,
                   "note": "曲线为每日持有因子排名第一标的至下一交易日的研究模拟；仅4标的小样本，不足以支持投资结论"}
        metrics = {key: _json_value(value) for key, value in metrics.items()}
        output = {"run_id": run_id, "factor_key": factor_key, "expression": factor["expression"],
                  "row_count": int(len(result)), "metrics": metrics, "equity_curve": factor_curve}
        raw_path = DATA_LAKE / "research" / "factors" / f"factor_{run_id}.json"
        raw_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        coverage = float(result["factor_value"].notna().mean()) if len(result) else 0
        with closing(connect()) as conn:
            conn.execute(
                """UPDATE factor_runs SET finished_at=?,status='SUCCESS',row_count=?,ic=?,rank_ic=?,coverage=?,
                   metrics_json=?,raw_path=? WHERE id=?""",
                (utcnow(), len(result), metrics["ic"], metrics["rank_ic"], coverage,
                 json.dumps(metrics, ensure_ascii=False), str(raw_path), run_id),
            )
            conn.commit()
        return output
    except Exception as exc:
        with closing(connect()) as conn:
            conn.execute("UPDATE factor_runs SET finished_at=?,status='FAILED',error=? WHERE id=?", (utcnow(), str(exc), run_id))
            conn.commit()
        raise


def strategy_lab_payload() -> dict:
    seed_research_catalog()
    with closing(connect()) as conn:
        strategies = [dict(row) for row in conn.execute(
            """SELECT s.*,r.stop_loss_pct,r.take_profit_pct,r.trailing_stop_pct,r.max_position_pct,
                      r.max_drawdown_pct,r.max_daily_loss_pct,r.turnover_limit,r.liquidity_rule,r.stress_rules_json,
                      (SELECT metrics_json FROM backtest_runs b WHERE b.strategy_id=s.id AND b.status='SUCCESS'
                       ORDER BY b.id DESC LIMIT 1) AS latest_metrics_json,
                      (SELECT asset_symbol FROM backtest_runs b WHERE b.strategy_id=s.id AND b.status='SUCCESS'
                       ORDER BY b.id DESC LIMIT 1) AS latest_symbol
               FROM strategies s JOIN strategy_risk_policies r ON r.strategy_id=s.id ORDER BY s.id"""
        )]
        factors = [dict(row) for row in conn.execute(
            """SELECT f.*,(SELECT ic FROM factor_runs x WHERE x.factor_id=f.id AND x.status='SUCCESS' ORDER BY x.id DESC LIMIT 1) AS ic,
                      (SELECT rank_ic FROM factor_runs x WHERE x.factor_id=f.id AND x.status='SUCCESS' ORDER BY x.id DESC LIMIT 1) AS rank_ic,
                      (SELECT row_count FROM factor_runs x WHERE x.factor_id=f.id AND x.status='SUCCESS' ORDER BY x.id DESC LIMIT 1) AS row_count,
                      (SELECT metrics_json FROM factor_runs x WHERE x.factor_id=f.id AND x.status='SUCCESS' ORDER BY x.id DESC LIMIT 1) AS latest_metrics_json,
                      (SELECT raw_path FROM factor_runs x WHERE x.factor_id=f.id AND x.status='SUCCESS' ORDER BY x.id DESC LIMIT 1) AS latest_raw_path
               FROM factors f ORDER BY f.id"""
        )]
        recent = [dict(row) for row in conn.execute(
            """SELECT b.id,s.strategy_key,s.name AS strategy,b.asset_symbol,b.data_start,b.data_end,b.status,
                      b.metrics_json,b.equity_json,b.error,b.started_at
               FROM backtest_runs b JOIN strategies s ON s.id=b.strategy_id ORDER BY b.id DESC LIMIT 20"""
        )]
    for item in strategies:
        item["latest_metrics"] = json.loads(item.pop("latest_metrics_json")) if item.get("latest_metrics_json") else None
        item["stress_rules"] = json.loads(item.pop("stress_rules_json"))
        latest_run = next((run for run in recent if run["strategy_key"] == item["strategy_key"] and run["status"] == "SUCCESS"), None)
        item["latest_curve"] = json.loads(latest_run["equity_json"]) if latest_run and latest_run.get("equity_json") else None
    for item in factors:
        item["latest_metrics"] = json.loads(item.pop("latest_metrics_json")) if item.get("latest_metrics_json") else None
        raw_path = item.pop("latest_raw_path", None)
        item["equity_curve"] = None
        if raw_path and Path(raw_path).exists():
            try:
                item["equity_curve"] = json.loads(Path(raw_path).read_text(encoding="utf-8")).get("equity_curve")
            except (OSError, json.JSONDecodeError):
                pass
        item.update(FACTOR_PROFILES.get(item["factor_key"], {"advantages": [], "limitations": []}))
    for item in recent:
        item["metrics"] = json.loads(item.pop("metrics_json")) if item.get("metrics_json") else None
        item["equity"] = json.loads(item.pop("equity_json")) if item.get("equity_json") else None
    return {"strategies": strategies, "factors": factors, "backtests": recent,
            "frameworks": ["AKQuant 0.3.38 FactorEngine", "AKQuant 0.3.38 run_backtest", "AKShare/本地落库行情"],
            "order_execution": False, "universe": ["000001.SH", "510300", "159915", "600519"]}
