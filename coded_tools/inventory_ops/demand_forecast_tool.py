"""
Demand forecasting CodedTool for Neuro-SAN.
Produces deterministic SKU forecasts, intervals, explanations, backtests, and
reliability flags without asking an LLM to perform arithmetic.
"""

import asyncio
import datetime
import logging
import math
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore
from coded_tools.inventory_ops.inventory_analytics_tool import InventoryAnalyticsTool

logger = logging.getLogger(__name__)


class DemandForecastTool(CodedTool):
    """
    CodedTool that forecasts demand and exposes uncertainty, drivers, and backtest quality.
    """

    MODELS = ("naive_7", "seasonal_naive", "ses", "holt", "croston")

    def __init__(self) -> None:
        """Initialize the tool with the shared data store."""
        self.store = InventoryDataStore()
        self.analytics_tool = InventoryAnalyticsTool()
        logger.info("%s initialized.", self.__class__.__name__)

    def _series(self, sku: str) -> Tuple[List[datetime.date], List[float]]:
        """Build a continuous daily series for a SKU, filling missing days with zero."""
        rows = [row for row in self.store.sales() if row.get("sku") == sku and row.get("status") == "fulfilled"]
        quantities: Dict[datetime.date, float] = {}
        for row in rows:
            parsed = self.store.to_datetime(row.get("order_date"))
            if not parsed:
                continue
            day = parsed.date()
            quantities[day] = quantities.get(day, 0.0) + self.store.to_float(row.get("quantity"))
        if not quantities:
            return [], []
        start = min(quantities)
        end = max(quantities)
        dates = []
        values = []
        current = start
        while current <= end:
            dates.append(current)
            values.append(quantities.get(current, 0.0))
            current += datetime.timedelta(days=1)
        return dates, values

    def _predict(
        self,
        model: str,
        train_values: List[float],
        future_dates: List[datetime.date],
    ) -> List[float]:
        """Predict daily demand with one of the deterministic candidate models."""
        if not train_values:
            return [0.0 for _ in future_dates]
        if model == "naive_7":
            baseline = sum(train_values[-7:]) / min(7, len(train_values))
            return [max(0.0, baseline) for _ in future_dates]
        if model == "seasonal_naive":
            weekday_values: Dict[int, List[float]] = {}
            for offset, value in enumerate(train_values):
                weekday = offset % 7
                weekday_values.setdefault(weekday, []).append(value)
            overall = sum(train_values) / len(train_values)
            predictions = []
            for future in future_dates:
                samples = weekday_values.get(future.weekday(), [])
                predictions.append(sum(samples) / len(samples) if samples else overall)
            return [max(0.0, value) for value in predictions]
        if model == "ses":
            alpha = 0.3
            level = train_values[0]
            for value in train_values[1:]:
                level = alpha * value + (1.0 - alpha) * level
            return [max(0.0, level) for _ in future_dates]
        if model == "holt":
            alpha, beta = 0.3, 0.1
            level = train_values[0]
            trend = (train_values[1] - train_values[0]) if len(train_values) > 1 else 0.0
            for value in train_values[1:]:
                previous_level = level
                level = alpha * value + (1.0 - alpha) * (level + trend)
                trend = beta * (level - previous_level) + (1.0 - beta) * trend
            return [max(0.0, level + trend * (step + 1)) for step in range(len(future_dates))]
        if model == "croston":
            nonzero = [value for value in train_values if value > 0]
            if not nonzero:
                return [0.0 for _ in future_dates]
            intervals = []
            last_nonzero = None
            for offset, value in enumerate(train_values):
                if value > 0:
                    if last_nonzero is not None:
                        intervals.append(offset - last_nonzero)
                    last_nonzero = offset
            average_interval = sum(intervals) / len(intervals) if intervals else 7.0
            rate = (sum(nonzero) / len(nonzero)) / max(average_interval, 1.0)
            return [max(0.0, rate) for _ in future_dates]
        return [0.0 for _ in future_dates]

    def _backtest(self, dates: List[datetime.date], values: List[float]) -> Dict[str, Any]:
        """Run a 28-day holdout and score each candidate model."""
        if len(values) < 21:
            return {"available": False, "reason": "At least 21 days of history are required."}
        horizon = min(28, max(7, len(values) // 4))
        train_values = values[:-horizon]
        actual = values[-horizon:]
        future_dates = dates[-horizon:]
        scores = {}
        for model in self.MODELS:
            predictions = self._predict(model, train_values, future_dates)
            absolute_error = sum(abs(pred - act) for pred, act in zip(predictions, actual))
            actual_total = sum(actual)
            wape = absolute_error / actual_total if actual_total > 0 else 1.0
            bias = (
                sum(pred - act for pred, act in zip(predictions, actual)) / actual_total if actual_total > 0 else 0.0
            )
            scores[model] = {"wape": wape, "bias": bias, "predictions": predictions}
        best_model = min(scores, key=lambda model: scores[model]["wape"])
        naive_error = sum(
            abs(pred - act) for pred, act in zip(self._predict("naive_7", train_values, future_dates), actual)
        ) / max(1, horizon)
        best_mae = sum(abs(pred - act) for pred, act in zip(scores[best_model]["predictions"], actual)) / max(
            1, horizon
        )
        mase = best_mae / naive_error if naive_error > 0 else 1.0
        return {
            "available": True,
            "holdout_days": horizon,
            "best_model": best_model,
            "wape": round(scores[best_model]["wape"], 3),
            "bias": round(scores[best_model]["bias"], 3),
            "mase": round(mase, 3),
            "model_scores": {
                model: {"wape": round(score["wape"], 3), "bias": round(score["bias"], 3)}
                for model, score in scores.items()
            },
        }

    def _forecast_sku(self, sku: str, horizon: int, uplift_percent: float = 0.0) -> Dict[str, Any]:
        """Forecast one SKU, including intervals, confidence, and model selection."""
        dates, values = self._series(sku)
        product = self.store.products().get(sku, {})
        if len(values) < 14 or sum(values) <= 0:
            return self._new_product_forecast(sku, horizon)
        backtest = self._backtest(dates, values)
        zero_ratio = sum(1 for value in values if value == 0) / len(values)
        model = backtest.get("best_model", "seasonal_naive") if backtest.get("available") else "seasonal_naive"
        if zero_ratio > 0.4:
            model = "croston"
        future_dates = [dates[-1] + datetime.timedelta(days=offset) for offset in range(1, horizon + 1)]
        daily_predictions = self._predict(model, values, future_dates)
        historical_mean = sum(values) / len(values)
        cap = max(historical_mean * 1.5, 0.01)
        daily_predictions = [min(value, cap) for value in daily_predictions]
        uplift = 1.0 + uplift_percent / 100.0
        daily_predictions = [value * uplift for value in daily_predictions]
        total = sum(daily_predictions)
        if backtest.get("available"):
            actual = values[-backtest["holdout_days"] :]
            predictions = self._predict(model, values[: -backtest["holdout_days"]], future_dates[: len(actual)])
            residuals = [pred - act for pred, act in zip(predictions, actual)]
            residual_std = math.sqrt(sum(error**2 for error in residuals) / max(1, len(residuals) - 1))
        else:
            recent_values = values[-28:]
            residual_std = math.sqrt(
                sum((value - total / horizon) ** 2 for value in recent_values) / max(1, min(28, len(values)) - 1)
            )
        interval_80 = 1.28 * residual_std * math.sqrt(horizon)
        interval_95 = 1.96 * residual_std * math.sqrt(horizon)
        confidence, reasons = self._confidence(values, zero_ratio, backtest)
        return {
            "sku": sku,
            "product_name": product.get("product_name"),
            "horizon_days": horizon,
            "forecast_units": int(round(total)),
            "daily_average": round(total / horizon, 2),
            "interval_80": [max(0, int(round(total - interval_80))), int(round(total + interval_80))],
            "interval_95": [max(0, int(round(total - interval_95))), int(round(total + interval_95))],
            "model": model,
            "confidence": confidence,
            "confidence_reasons": reasons,
            "history_days": len(values),
            "zero_sales_ratio": round(zero_ratio, 3),
            "backtest": backtest,
        }

    def _confidence(
        self,
        values: List[float],
        zero_ratio: float,
        backtest: Dict[str, Any],
    ) -> Tuple[str, List[str]]:
        """Assign a confidence tier from history length, sparsity, and backtest error."""
        reasons = []
        if len(values) < 28:
            reasons.append("Less than 28 days of history")
        if zero_ratio > 0.4:
            reasons.append("Sparse demand with many zero-sales days")
        if backtest.get("available") and backtest.get("wape", 1.0) > 0.6:
            reasons.append("Holdout forecast error is high")
        if backtest.get("available") and backtest.get("mase", 1.0) > 1.2:
            reasons.append("Forecast is worse than the naive baseline")
        if len(values) >= 60 and zero_ratio <= 0.3 and backtest.get("wape", 1.0) <= 0.5:
            return "HIGH", reasons
        if len(values) >= 28 and backtest.get("wape", 1.0) <= 0.8:
            return "MEDIUM", reasons
        return "LOW", reasons

    def _new_product_forecast(self, sku: str, horizon: int) -> Dict[str, Any]:
        """Forecast a new SKU from category peers and label the uncertainty explicitly."""
        products = self.store.products()
        product = products.get(sku, {})
        category = product.get("category")
        peers = []
        for peer_sku, peer in products.items():
            if peer_sku == sku or peer.get("category") != category:
                continue
            peer_dates, peer_values = self._series(peer_sku)
            if len(peer_values) < 14 or sum(peer_values) <= 0:
                continue
            peers.append(
                {
                    "sku": peer_sku,
                    "daily_average": sum(peer_values) / len(peer_values),
                    "history_days": len(peer_values),
                }
            )
        if peers:
            peer_rate = sum(peer["daily_average"] for peer in peers) / len(peers)
            model = "comparable_category"
        else:
            peer_rate = 0.25
            model = "category_default"
        total = peer_rate * horizon
        return {
            "sku": sku,
            "product_name": product.get("product_name"),
            "horizon_days": horizon,
            "forecast_units": int(round(total)),
            "daily_average": round(peer_rate, 2),
            "interval_80": [int(round(total * 0.4)), int(round(total * 1.8))],
            "interval_95": [int(round(total * 0.2)), int(round(total * 2.4))],
            "model": model,
            "confidence": "INSUFFICIENT_HISTORY",
            "confidence_reasons": ["No reliable sales history", "Forecast uses category peers"],
            "history_days": 0,
            "zero_sales_ratio": 1.0,
            "comparable_skus": peers,
            "backtest": {"available": False, "reason": "New product has insufficient history."},
        }

    def _stockout_horizon(self, horizon: int) -> Dict[str, Any]:
        """Rank SKUs by expected runout inside the horizon."""
        audit = self.analytics_tool.invoke({"action": "audit_all"}, {})
        items = []
        for item in audit.get("items", []):
            sku = item.get("sku")
            forecast = self._forecast_sku(sku, horizon)
            daily_average = forecast.get("daily_average", 0.0)
            net_position = item.get("inventory", {}).get("net_position", 0)
            runout_days = net_position / daily_average if daily_average > 0 else 999.0
            if runout_days <= horizon:
                unmet_units = max(0.0, daily_average * horizon - net_position)
                items.append(
                    {
                        "sku": sku,
                        "product_name": item.get("product_name"),
                        "net_position": net_position,
                        "forecast_daily_average": daily_average,
                        "runout_days": round(runout_days, 1),
                        "expected_unmet_units": int(math.ceil(unmet_units)),
                        "expected_lost_gmv_inr": round(unmet_units * item.get("selling_price", 0.0), 2),
                        "confidence": forecast.get("confidence"),
                    }
                )
        items.sort(key=lambda item: item["expected_lost_gmv_inr"], reverse=True)
        return {
            "horizon_days": horizon,
            "summary": {
                "at_risk_skus": len(items),
                "total_expected_lost_gmv_inr": round(sum(item["expected_lost_gmv_inr"] for item in items), 2),
            },
            "items": items,
        }

    def _explain_change(self, sku: str) -> Dict[str, Any]:
        """Explain recent demand change using trend, weekly seasonality, promotions, and anomalies."""
        dates, values = self._series(sku)
        if len(values) < 21:
            return {"sku": sku, "error": "At least 21 days of history are required."}
        recent = values[-7:]
        prior = values[-35:-7]
        recent_mean = sum(recent) / len(recent)
        prior_mean = sum(prior) / len(prior) if prior else 0.0
        change_pct = ((recent_mean - prior_mean) / prior_mean * 100.0) if prior_mean > 0 else 0.0
        weekday_totals: Dict[int, List[float]] = {}
        for offset, value in enumerate(values[-56:]):
            weekday_totals.setdefault(offset % 7, []).append(value)
        weekday_index = {
            str(day): round(sum(day_values) / len(day_values) / max(recent_mean, 0.01), 2)
            for day, day_values in weekday_totals.items()
        }
        promotions = [row for row in self.store.load("promotions.csv") if row.get("sku") == sku]
        recent_start = dates[-7]
        recent_promos = [
            row
            for row in promotions
            if self.store.to_datetime(row.get("end_date"))
            and self.store.to_datetime(row.get("end_date")).date() >= recent_start
        ]
        promo_uplift = sum(self.store.to_float(row.get("uplift_percent")) for row in recent_promos)
        anomalies = []
        if len(prior) >= 7:
            mean = sum(prior) / len(prior)
            std = math.sqrt(sum((value - mean) ** 2 for value in prior) / max(1, len(prior) - 1))
            for offset, value in enumerate(recent):
                if std > 0 and abs(value - mean) / std > 2.0:
                    anomalies.append(
                        {
                            "date": dates[-7 + offset].isoformat(),
                            "units": value,
                            "z_score": round((value - mean) / std, 2),
                        }
                    )
        drivers = []
        if abs(change_pct) >= 10:
            drivers.append({"driver": "trend", "contribution_pct": round(min(100.0, abs(change_pct)), 1)})
        if promo_uplift > 0:
            drivers.append({"driver": "promotion", "contribution_pct": round(min(100.0, promo_uplift), 1)})
        if max(weekday_index.values(), default=1.0) > 1.15:
            drivers.append({"driver": "weekly_seasonality", "contribution_pct": 15.0})
        if anomalies:
            drivers.append({"driver": "anomaly", "contribution_pct": round(min(100.0, len(anomalies) * 20.0), 1)})
        return {
            "sku": sku,
            "recent_7_day_average": round(recent_mean, 2),
            "prior_28_day_average": round(prior_mean, 2),
            "change_percent": round(change_pct, 1),
            "weekday_index": weekday_index,
            "promotions_in_window": recent_promos,
            "anomalies": anomalies,
            "drivers": drivers,
        }

    def _classify_spike(self, sku: str) -> Dict[str, Any]:
        """Distinguish a one-off spike from a sustained demand shift."""
        dates, values = self._series(sku)
        if len(values) < 14:
            return {"sku": sku, "classification": "INSUFFICIENT_HISTORY"}
        baseline_values = values[-28:-3] if len(values) >= 31 else values[:-3]
        baseline = sorted(baseline_values)[len(baseline_values) // 2] if baseline_values else 0.0
        recent = values[-3:]
        elevated = [value > max(1.0, baseline * 1.5) for value in recent]
        if baseline <= 0:
            classification = "SPARSE_HISTORY"
        elif all(elevated):
            classification = "SUSTAINED_SHIFT"
        elif any(elevated):
            classification = "ONE_OFF_OR_UNCERTAIN"
        else:
            classification = "NORMAL"
        return {
            "sku": sku,
            "baseline_median_units": baseline,
            "last_3_days": recent,
            "classification": classification,
            "confidence": "MEDIUM" if len(values) >= 28 else "LOW",
            "explanation": {
                "SUSTAINED_SHIFT": "All of the last three days are well above baseline.",
                "ONE_OFF_OR_UNCERTAIN": "Only part of the recent window is elevated; monitor before changing policy.",
                "NORMAL": "Recent demand is within the normal range.",
                "SPARSE_HISTORY": "Too few non-zero observations to classify the spike.",
                "INSUFFICIENT_HISTORY": "At least 14 days of history are required.",
            }.get(classification, ""),
        }

    def _promotion_scenario(self, sku: str, uplift_percent: float, horizon: int) -> Dict[str, Any]:
        """Recalculate stockout risk under a demand uplift scenario."""
        forecast = self._forecast_sku(sku, horizon, uplift_percent)
        audit = self.analytics_tool.invoke({"sku": sku}, {})
        analysis = audit.get("sku_analysis", {})
        net_position = analysis.get("inventory", {}).get("net_position", 0)
        daily_average = forecast.get("daily_average", 0.0)
        runout_days = net_position / daily_average if daily_average > 0 else 999.0
        unmet_units = max(0.0, daily_average * horizon - net_position)
        return {
            "sku": sku,
            "uplift_percent": uplift_percent,
            "horizon_days": horizon,
            "net_position": net_position,
            "scenario_daily_average": daily_average,
            "runout_days": round(runout_days, 1),
            "expected_unmet_units": int(math.ceil(unmet_units)),
            "mitigation_buffer_units": int(math.ceil(unmet_units)),
            "expected_lost_gmv_inr": round(unmet_units * analysis.get("selling_price", 0.0), 2),
            "confidence": forecast.get("confidence"),
        }

    def _reliability(self, sku: Optional[str]) -> Dict[str, Any]:
        """Flag SKUs whose forecasts should not be automated against."""
        products = self.store.products()
        items = []
        for product_sku in products:
            if sku and product_sku != sku:
                continue
            dates, values = self._series(product_sku)
            forecast = self._forecast_sku(product_sku, 30)
            zero_ratio = sum(1 for value in values if value == 0) / len(values) if values else 1.0
            backtest = forecast.get("backtest", {})
            flags = []
            if len(values) < 28:
                flags.append("SPARSE_HISTORY")
            if zero_ratio > 0.4:
                flags.append("INTERMITTENT_DEMAND")
            if backtest.get("available") and backtest.get("wape", 0.0) > 0.6:
                flags.append("HIGH_BACKTEST_ERROR")
            if forecast.get("confidence") in ("LOW", "INSUFFICIENT_HISTORY"):
                flags.append("LOW_CONFIDENCE")
            items.append(
                {
                    "sku": product_sku,
                    "confidence": forecast.get("confidence"),
                    "history_days": len(values),
                    "zero_sales_ratio": round(zero_ratio, 3),
                    "wape": backtest.get("wape"),
                    "flags": flags,
                    "automatable": not flags,
                }
            )
        items.sort(key=lambda item: len(item["flags"]), reverse=True)
        return {
            "summary": {
                "skus_reviewed": len(items),
                "not_automatable": sum(1 for item in items if not item["automatable"]),
            },
            "items": items,
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute a demand forecasting action.

        :param args: Dictionary with "action", optional "sku", "horizon_days", and "uplift_percent".
        :param sly_data: Unused context dictionary.
        :return: Structured forecast result.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = args.get("action", "forecast")
        sku = self.store.normalize_optional(args.get("sku"))
        horizon = self.store.to_int(args.get("horizon_days"), 30)
        uplift = self.store.to_float(args.get("uplift_percent"), 0.0)
        if action == "forecast":
            if sku:
                return self._forecast_sku(sku, horizon)
            return {
                "horizon_days": horizon,
                "stockout_horizon": self._stockout_horizon(horizon),
                "forecasts": [self._forecast_sku(product_sku, horizon) for product_sku in self.store.products()],
            }
        if action == "stockout_horizon":
            return self._stockout_horizon(horizon)
        if action == "explain_change":
            if not sku:
                return {"error": "explain_change requires a sku"}
            return self._explain_change(sku)
        if action == "classify_spike":
            if not sku:
                return {"error": "classify_spike requires a sku"}
            return self._classify_spike(sku)
        if action == "promotion_scenario":
            if not sku:
                return {"error": "promotion_scenario requires a sku"}
            return self._promotion_scenario(sku, uplift, horizon)
        if action == "new_product_forecast":
            if not sku:
                return {"error": "new_product_forecast requires a sku"}
            return self._new_product_forecast(sku, horizon)
        if action == "backtest":
            if sku:
                dates, values = self._series(sku)
                return {"sku": sku, "backtest": self._backtest(dates, values)}
            return {
                "backtests": [
                    {
                        "sku": product_sku,
                        "backtest": self._backtest(*self._series(product_sku)),
                    }
                    for product_sku in self.store.products()
                ]
            }
        if action == "reliability":
            return self._reliability(sku)
        return {"error": f"Unknown action: {action}"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
