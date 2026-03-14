"""Claude Code usage statistics — reads local JSONL session logs.

Provides today's token stats, model breakdown, tool counts, and cost
estimates without any API calls.  Reads the lazyagent usage cache
(populated by usage_fetcher) for session/weekly usage percentages.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Per-million-token pricing: (input, output, cache_read, cache_write)
_MODEL_PRICING = {
    "claude-opus-4-6":           (15.0, 75.0, 1.875, 18.75),
    "claude-sonnet-4-6":         ( 3.0, 15.0, 0.30,   3.75),
    "claude-haiku-4-5-20251001": ( 0.80, 4.0, 0.08,   1.00),
}


@dataclass
class TokenStats:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    session_count: int = 0
    message_count: int = 0
    user_msg_count: int = 0
    tool_call_count: int = 0
    thinking_blocks: int = 0
    thinking_chars: int = 0
    turn_duration_ms: int = 0
    turn_count: int = 0
    models: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)


@dataclass
class UsageSection:
    percent: int = 0
    reset_time: str = ""


@dataclass
class BudgetInfo:
    current_day: int = 0
    total_days: int = 7
    cumulative_budget: float = 0.0
    actual_percent: int = 0
    budget_ratio: float = 0.0
    filled_blocks: int = 0


@dataclass
class UsageData:
    session: Optional[UsageSection] = None
    week: Optional[UsageSection] = None
    week_sonnet: Optional[UsageSection] = None
    week_budget: Optional[BudgetInfo] = None
    week_sonnet_budget: Optional[BudgetInfo] = None
    tokens: Optional[TokenStats] = None
    rate_limited: bool = False
    cache_age: int = 0  # seconds since last usage fetch


def short_model(model: str) -> str:
    if "opus" in model:
        return "Opus"
    if "sonnet" in model:
        return "Sonnet"
    if "haiku" in model:
        return "Haiku"
    if "synthetic" in model:
        return "synth"
    return model[:7]


def format_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}K"
    return f"{n / 1_000_000:.1f}M"


def estimate_cost(stats: TokenStats) -> dict[str, float]:
    costs: dict[str, float] = {}
    total = 0.0
    for model, data in stats.models.items():
        pricing = _MODEL_PRICING.get(model)
        if not pricing:
            continue
        inp_p, out_p, cr_p, cw_p = pricing
        c = (
            data["input"]       / 1_000_000 * inp_p
            + data["output"]    / 1_000_000 * out_p
            + data["cache_read"]  / 1_000_000 * cr_p
            + data["cache_write"] / 1_000_000 * cw_p
        )
        costs[model] = c
        total += c
    costs["_total"] = total
    return costs


def _is_date(ts_str: str, target_date) -> bool:
    try:
        utc_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return utc_dt.astimezone().date() == target_date
    except Exception:
        return False


def compute_tokens_for_date(day_offset: int = 0) -> Optional[TokenStats]:
    """Aggregate token usage for a given day. 0=today, -1=yesterday, etc."""
    if not PROJECTS_DIR.is_dir():
        return None

    from datetime import timedelta
    target = datetime.now().date() + timedelta(days=day_offset)
    target_start = datetime.combine(target, datetime.min.time()).timestamp()
    target_end = target_start + 86400

    stats = TokenStats()
    sessions: set[str] = set()

    for f in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            if f.stat().st_mtime < target_start:
                continue
        except OSError:
            continue

        for line in f.open(encoding="utf-8", errors="replace"):
            if '"_progress"' in line or '"file-history-snapshot"' in line:
                continue
            if '"progress"' in line and '"system"' not in line:
                continue
            if '"queue-operation"' in line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts_str = entry.get("timestamp", "")
            if not ts_str or not _is_date(ts_str, target):
                continue

            sid = entry.get("sessionId", "")
            if sid:
                sessions.add(sid)

            entry_type = entry.get("type")

            if entry_type == "assistant":
                msg = entry.get("message", {})
                usage = msg.get("usage")
                if not usage:
                    continue

                stats.input_tokens += usage.get("input_tokens", 0)
                stats.output_tokens += usage.get("output_tokens", 0)
                stats.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
                stats.cache_write_tokens += usage.get("cache_creation_input_tokens", 0)
                stats.message_count += 1

                model = msg.get("model", "unknown")
                if model not in stats.models:
                    stats.models[model] = {
                        "count": 0, "input": 0, "output": 0,
                        "cache_read": 0, "cache_write": 0,
                    }
                md = stats.models[model]
                md["count"] += 1
                md["input"] += usage.get("input_tokens", 0)
                md["output"] += usage.get("output_tokens", 0)
                md["cache_read"] += usage.get("cache_read_input_tokens", 0)
                md["cache_write"] += usage.get("cache_creation_input_tokens", 0)

                for block in msg.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "thinking":
                        stats.thinking_blocks += 1
                        stats.thinking_chars += len(block.get("thinking", ""))
                    elif btype == "tool_use":
                        stats.tool_call_count += 1
                        name = block.get("name", "unknown")
                        stats.tools[name] = stats.tools.get(name, 0) + 1

            elif entry_type == "user":
                stats.user_msg_count += 1

            elif entry_type == "system" and entry.get("subtype") == "turn_duration":
                stats.turn_duration_ms += entry.get("durationMs", 0)
                stats.turn_count += 1

    stats.session_count = len(sessions)
    return stats if stats.message_count > 0 else None




def _parse_section(raw: Optional[dict]) -> Optional[UsageSection]:
    if not raw:
        return None
    return UsageSection(
        percent=raw.get("percent", 0),
        reset_time=raw.get("resetTime", ""),
    )


def format_reset_compact(reset_str: str) -> str:
    """Compute time remaining until reset."""
    if not reset_str:
        return ""
    clean = re.sub(r'\s*\(.*\)', '', reset_str).strip()
    m = re.match(r'^(\d+)\s*h\s*(\d+)\s*m$', clean, re.IGNORECASE)
    if m:
        return f"{m.group(1)}h{m.group(2)}m"
    m = re.match(r'^(\d+)\s*h$', clean, re.IGNORECASE)
    if m:
        return f"{m.group(1)}h"
    try:
        from zoneinfo import ZoneInfo
        tz_match = re.search(r'\(([\w/]+)\)', reset_str)
        tz_name = tz_match.group(1) if tz_match else "UTC"
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        repaired = re.sub(r'(\d+)\s+([ap])\s*m$', r'\1\2m', clean, flags=re.IGNORECASE)
        repaired = re.sub(r'^(\d+)\s+m$', r'\1am', repaired)
        up = repaired.upper()
        from datetime import timedelta
        for fmt in ["%b %d, %I:%M%p", "%b %d, %I%p", "%I:%M%p", "%I%p"]:
            try:
                if fmt in ("%I%p", "%I:%M%p"):
                    dt = datetime.strptime(
                        f"{up} {now.year}-{now.month:02d}-{now.day:02d}",
                        f"{fmt} %Y-%m-%d"
                    )
                else:
                    dt = datetime.strptime(f"{up} {now.year}", f"{fmt} %Y")
                dt = dt.replace(tzinfo=tz)
                if dt <= now:
                    dt += timedelta(days=1)
                total_mins = max(0, int((dt - now).total_seconds() / 60))
                h, m2 = divmod(total_mins, 60)
                return f"{h}h{m2}m" if h else f"{m2}m"
            except ValueError:
                continue
    except Exception:
        pass
    return ""


def _parse_reset_dt(reset_str: str):
    """Parse resetTime into a datetime for budget computation."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import timedelta
        tz_match = re.search(r'\(([\w/]+)\)', reset_str)
        tz_name = tz_match.group(1) if tz_match else "UTC"
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        clean = re.sub(r'\s*\(.*\)', '', reset_str).strip()
        repaired = re.sub(r'(\d+)\s+([ap])\s*m$', r'\1\2m', clean, flags=re.IGNORECASE)
        repaired = re.sub(r'^(\d+)\s+m$', r'\1am', repaired)
        up = repaired.upper()
        for fmt in ["%b %d, %I:%M%p", "%b %d, %I%p", "%I:%M%p", "%I%p"]:
            try:
                if fmt in ("%I%p", "%I:%M%p"):
                    dt = datetime.strptime(
                        f"{up} {now.year}-{now.month:02d}-{now.day:02d}", f"{fmt} %Y-%m-%d"
                    )
                else:
                    dt = datetime.strptime(f"{up} {now.year}", f"{fmt} %Y")
                dt = dt.replace(tzinfo=tz)
                if dt <= now:
                    dt += timedelta(days=1)
                return dt, tz
            except ValueError:
                continue
    except Exception:
        pass
    return None, None


def compute_budget(section: Optional[UsageSection]) -> Optional[BudgetInfo]:
    """Compute weekly budget progress from a usage section."""
    if not section or not section.reset_time:
        return None
    result = _parse_reset_dt(section.reset_time)
    if result[0] is None:
        return None
    reset_dt, tz = result
    from datetime import timedelta
    now = datetime.now(tz)
    cycle_start = reset_dt - timedelta(days=7)
    current_day = max(1, min(7, (now.date() - cycle_start.date()).days + 1))
    daily_budget = 100.0 / 7
    cumulative_budget = daily_budget * current_day
    actual_percent = section.percent
    budget_ratio = (actual_percent / cumulative_budget * 100) if cumulative_budget > 0 else 0.0
    filled_blocks = max(0, min(7, round(budget_ratio / 100 * 7)))
    return BudgetInfo(
        current_day=current_day,
        total_days=7,
        cumulative_budget=cumulative_budget,
        actual_percent=actual_percent,
        budget_ratio=budget_ratio,
        filled_blocks=filled_blocks,
    )


def compute_today_tokens() -> Optional[TokenStats]:
    """Shortcut for today's tokens."""
    return compute_tokens_for_date(0)


def get_usage_data(day_offset: int = 0) -> UsageData:
    """Collect all usage data from local sources."""
    data = UsageData()

    # Token stats from JSONL files
    data.tokens = compute_tokens_for_date(day_offset)

    # Usage percentages from lazyagent's own usage cache
    from lazyagent.usage_fetcher import read_cache
    wb, age = read_cache()
    if wb:
        data.cache_age = age
        data.rate_limited = wb.get("error") == "rate_limited"
        data.session = _parse_section(wb.get("session"))
        data.week = _parse_section(wb.get("week"))
        data.week_sonnet = _parse_section(wb.get("weekSonnet"))
        data.week_budget = compute_budget(data.week)
        data.week_sonnet_budget = compute_budget(data.week_sonnet)

    return data
