"""In-app usage meter: how much you've recorded, how much budget is left.

Whisper is billed per AUDIO HOUR (~$0.04/h on Groq), not per token. The user
sets a monthly budget in hours (default 8 h — the size of the free trial and
a comfortable free-tier ceiling) and the Hub shows used / remaining with a
bar, plus a tray tooltip and one-time warnings at 80 % and 100 %.

Source of truth is the local transcriptions.db (successful rows only), so it
works identically for BYOK and managed users, online or offline.
"""
from __future__ import annotations

from datetime import datetime, timezone

from config import get_setting, set_setting

COST_PER_HOUR_USD = 0.04
DEFAULT_BUDGET_HOURS = 8.0


def fmt_hms(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def budget_hours() -> float:
    try:
        return float(get_setting("usage_budget_hours_month", DEFAULT_BUDGET_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_HOURS


def summary(db) -> dict:
    now = datetime.now(timezone.utc)
    month_start = now.strftime("%Y-%m-01 00:00:00")
    day_start = now.strftime("%Y-%m-%d 00:00:00")
    month = db.usage_seconds(since=month_start)
    today = db.usage_seconds(since=day_start)
    budget = budget_hours() * 3600.0
    remaining = max(0.0, budget - month)
    pct = (month / budget * 100.0) if budget > 0 else 0.0
    return {
        "month_seconds": month,
        "today_seconds": today,
        "budget_seconds": budget,
        "remaining_seconds": remaining,
        "pct": round(pct, 1),
        "cost_month_usd": round(month / 3600.0 * COST_PER_HOUR_USD, 3),
        "month_label": now.strftime("%Y-%m"),
    }


def tooltip(db) -> str:
    s = summary(db)
    return (
        f"KeyLess by Sinsajo — este mes {fmt_hms(s['month_seconds'])} "
        f"de {fmt_hms(s['budget_seconds'])} ({s['pct']:.0f}%)"
    )


def budget_warning(db) -> str | None:
    """One-shot warnings when crossing 80 % and 100 % of the monthly budget.
    Flags are stored per month so each fires once."""
    s = summary(db)
    key80 = f"usage_warned_80:{s['month_label']}"
    key100 = f"usage_warned_100:{s['month_label']}"
    if s["pct"] >= 100 and not get_setting(key100, False):
        set_setting(key100, True)
        return (
            f"Llegaste al 100% de tu presupuesto mensual ({fmt_hms(s['budget_seconds'])}). "
            "Sigue funcionando, pero revisa tu consumo en el Hub."
        )
    if s["pct"] >= 80 and not get_setting(key80, False):
        set_setting(key80, True)
        return (
            f"Vas por el {s['pct']:.0f}% de tu presupuesto mensual — "
            f"te quedan {fmt_hms(s['remaining_seconds'])}."
        )
    return None
