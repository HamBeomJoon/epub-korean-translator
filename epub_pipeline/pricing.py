"""Externally supplied rates. Monetary values are decimal strings, never invented."""
from decimal import Decimal, InvalidOperation


class RateCard:
    def __init__(self, data: dict | None = None):
        self.data = data or {}
        if not isinstance(self.data, dict):
            raise ValueError("요금표는 JSON 객체여야 합니다.")
        for model, rate in self.data.items():
            if not isinstance(rate, dict) or not isinstance(rate.get("currency"), str) or not rate["currency"]:
                raise ValueError(f"{model}: currency 및 토큰 요금이 필요합니다.")
            for key in ("input_per_million", "cached_input_per_million", "output_per_million"):
                try:
                    value = Decimal(str(rate[key]))
                    if not value.is_finite() or value < 0:
                        raise ValueError("요금은 유한한 0 이상 숫자여야 합니다.")
                except (KeyError, InvalidOperation) as exc:
                    raise ValueError(f"{model}: 잘못된 요금 {key}") from exc

    def cost(self, model: str, usage: dict | None) -> dict:
        rate = self.data.get(model)
        if not rate or usage is None:
            return {"amount": None, "currency": rate.get("currency") if rate else None}
        cached = usage.get("cached_input_tokens", 0)
        amount = ((usage["input_tokens"] - cached) * Decimal(str(rate["input_per_million"]))
                  + cached * Decimal(str(rate["cached_input_per_million"]))
                  + usage["output_tokens"] * Decimal(str(rate["output_per_million"]))) / Decimal(1_000_000)
        return {"amount": str(amount), "currency": rate["currency"], "rate": rate}


def summarize(records: list[dict]) -> dict:
    tokens = {k: 0 for k in ("input_tokens", "output_tokens", "cached_input_tokens")}
    known_costs = {}
    for record in records:
        for key in tokens:
            tokens[key] += (record.get("usage") or {}).get(key, 0)
        cost = record.get("cost", {})
        if cost.get("amount") is not None:
            currency = cost["currency"]
            known_costs[currency] = known_costs.get(currency, Decimal(0)) + Decimal(cost["amount"])
    return {"attempts": len(records), "observed_tokens": tokens,
            "unknown_usage_attempts": sum(r.get("usage") is None for r in records),
            "unpriced_attempts": sum(r.get("cost", {}).get("amount") is None for r in records),
            "known_cost_by_currency": {k: str(v) for k, v in known_costs.items()},
            "cost_complete": all(r.get("cost", {}).get("amount") is not None for r in records)}
