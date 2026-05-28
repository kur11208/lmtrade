from __future__ import annotations


def feed_label_from_freshness(freshness: str) -> tuple[str, str]:
    freshness = str(freshness or "").strip()
    if freshness == "market_closed":
        return "市場外", ""
    if freshness in ("blocked_stale", "missing"):
        return "停止", "bad"
    if freshness == "stale":
        return "遅延", "warn"
    if freshness == "fresh":
        return "稼働中", "ok"
    if freshness == "future":
        return "未来足", "warn"
    return freshness or "-", ""


def display_reason(last_action: str, reason: str) -> str | None:
    if str(last_action or "").strip() in ("SELL_STALE_EOD", "BUY_STALE_EOD"):
        return "データ停止後にノーポジ化済み"
    return None


def aggregate_data_label(stopped_count: int, stale_count: int, market_open: bool) -> tuple[str, str]:
    if stopped_count:
        return f"停止 {stopped_count}", "bad"
    if stale_count:
        return f"遅延 {stale_count}", "warn"
    if not market_open:
        return "市場外", ""
    return "正常", "ok"
