"""WolfAnalytics: aggregate server activity counters."""
import json
import io
import threading
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta, timezone
from storage import data_path

_PATH = data_path("wolfanalytics.json")
_LOCK = threading.Lock()

def _load():
    try:
        value = json.loads(_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def _save(data):
    temp = _PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(_PATH)

def record(guild_id: int, event: str) -> None:
    """Persist event type and timestamp only; never message content or user IDs."""
    if event not in {"join", "leave", "message"}:
        return
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=31)
    with _LOCK:
        data = _load()
        events = data.setdefault(str(guild_id), [])
        events.append({"at": now.isoformat(), "type": event})
        kept = []
        for item in events:
            try:
                stamp = datetime.fromisoformat(item["at"])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp >= cutoff:
                    kept.append(item)
            except (TypeError, ValueError, KeyError):
                continue
        data[str(guild_id)] = kept[-50000:]
        _save(data)

def get_stats(guild_id: int, period: str) -> dict:
    hours = {"24h": 24, "7day": 168, "30day": 720}.get(period)
    if hours is None:
        raise ValueError("Period must be 24h, 7day or 30day.")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = {"joins": 0, "leaves": 0, "messages": 0}
    with _LOCK:
        events = list(_load().get(str(guild_id), []))
    for item in events:
        try:
            stamp = datetime.fromisoformat(item["at"])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, KeyError):
            continue
        if stamp >= cutoff:
            key = {"join": "joins", "leave": "leaves", "message": "messages"}.get(item.get("type"))
            if key:
                result[key] += 1
    return result

def build_report(guild_name: str, period: str, stats: dict) -> str:
    label = {"24h": "Last 24 hours", "7day": "Last 7 days", "30day": "Last 30 days"}[period]
    return (f"📊 **WolfAnalytics — {guild_name}**\n**Period:** {label}\n\n"
            f"👋 Members joined: **{stats['joins']:,}**\n"
            f"🚪 Members left: **{stats['leaves']:,}**\n"
            f"💬 Messages sent: **{stats['messages']:,}**")


def get_timeline(guild_id: int, period: str) -> list:
    """Return time-bucketed aggregate counts for the selected period."""
    hours = {"24h": 24, "7day": 168, "30day": 720}.get(period)
    if hours is None:
        raise ValueError("Period must be 24h, 7day or 30day.")
    now = datetime.now(timezone.utc)
    bucket_hours = 1 if period == "24h" else 24
    count = hours // bucket_hours
    buckets = [{"joins": 0, "leaves": 0, "messages": 0} for _ in range(count)]
    cutoff = now - timedelta(hours=hours)
    with _LOCK:
        events = list(_load().get(str(guild_id), []))
    for item in events:
        try:
            stamp = datetime.fromisoformat(item["at"])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, KeyError):
            continue
        if cutoff <= stamp <= now:
            index = min(count - 1, int((stamp - cutoff).total_seconds() // (bucket_hours * 3600)))
            key = {"join": "joins", "leave": "leaves", "message": "messages"}.get(item.get("type"))
            if key:
                buckets[index][key] += 1
    return buckets


def build_chart(guild_name: str, period: str, stats: dict, chart_type: str = "column") -> io.BytesIO:
    """Render aggregate activity as columns, bars or a time-series line chart."""
    if chart_type not in {"column", "bar", "line"}:
        raise ValueError("Chart type must be column, bar or line.")
    period_label = {"24h": "Last 24 hours", "7day": "Last 7 days", "30day": "Last 30 days"}[period]
    labels = [("Joins", int(stats.get("joins", 0))), ("Leaves", int(stats.get("leaves", 0))), ("Messages", int(stats.get("messages", 0)))]
    width, height = (1100, 650) if chart_type in {"column", "line"} else (1100, 520)
    image = Image.new("RGB", (width, height), "#101827")
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 38)
        label_font = ImageFont.truetype("DejaVuSans.ttf", 25)
        value_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 27)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        title_font = label_font = value_font = small_font = ImageFont.load_default()
    draw.text((55, 38), "WolfAnalytics", fill="#F4F7FB", font=title_font)
    draw.text((58, 92), f"{guild_name[:55]}  ·  {period_label}", fill="#AAB8CC", font=label_font)
    colors = ["#55C2A3", "#F08A79", "#78A9F5"]
    if chart_type == "line":
        timeline = stats.get("timeline") or []
        left, top, right, bottom = 90, 185, 1010, 515
        draw.line((left, bottom, right, bottom), fill="#536176", width=2)
        series = [("Joins", "joins", colors[0]), ("Leaves", "leaves", colors[1]), ("Messages", "messages", colors[2])]
        maximum = max((bucket.get(key, 0) for bucket in timeline for _, key, _ in series), default=0) or 1
        for label, key, color in series:
            points = []
            for index, bucket in enumerate(timeline):
                x = left + (index / max(1, len(timeline) - 1)) * (right - left)
                y = bottom - (bucket.get(key, 0) / maximum) * (bottom - top)
                points.append((int(x), int(y)))
            if len(points) > 1:
                draw.line(points, fill=color, width=5)
            for x, y in points:
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color)
        legend_x = 100
        for label, key, color in series:
            draw.ellipse((legend_x, 555, legend_x + 14, 569), fill=color)
            draw.text((legend_x + 22, 550), label, fill="#D5DEEA", font=small_font)
            legend_x += 270
    elif chart_type == "column":
        base_y, top_y = 520, 190
        draw.line((90, base_y, 1010, base_y), fill="#536176", width=2)
        maximum = max((value for _, value in labels), default=0) or 1
        bar_width, centers = 150, [250, 550, 850]
        for (label, value), color, center in zip(labels, colors, centers):
            bar_height = max(4, int((value / maximum) * (base_y - top_y)))
            x0, x1 = center - bar_width // 2, center + bar_width // 2
            draw.rounded_rectangle((x0, base_y - bar_height, x1, base_y), radius=16, fill=color)
            value_text = f"{value:,}"
            box = draw.textbbox((0, 0), value_text, font=value_font)
            draw.text((center - (box[2] - box[0]) / 2, base_y - bar_height - 42), value_text, fill="#F4F7FB", font=value_font)
            box = draw.textbbox((0, 0), label, font=label_font)
            draw.text((center - (box[2] - box[0]) / 2, base_y + 22), label, fill="#D5DEEA", font=label_font)
    else:
        left, right = 270, 1000
        maximum = max((value for _, value in labels), default=0) or 1
        for index, ((label, value), color) in enumerate(zip(labels, colors)):
            y = 205 + index * 105
            draw.text((65, y + 8), label, fill="#D5DEEA", font=label_font)
            bar_width = max(4, int((value / maximum) * (right - left)))
            draw.rounded_rectangle((left, y, left + bar_width, y + 58), radius=13, fill=color)
            draw.text((min(left + bar_width + 14, 990), y + 12), f"{value:,}", fill="#F4F7FB", font=value_font)
    draw.text((58, height - 38), "Counts include activity observed since WolfAnalytics was enabled.", fill="#8392A8", font=small_font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output
