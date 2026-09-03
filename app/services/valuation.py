"""
Reading the price trail a listing has accumulated.

Divar listings are re-scraped and the update path overwrites the stored price,
so until the trail existed there was nothing to read. This turns the recorded
entries into something a panel can show: which way the price moved, by how
much, newest first.
"""
from typing import Any, Dict, List


def price_moves(prop) -> List[Dict[str, Any]]:
    """The recorded price trail, newest first, with the direction worked out."""
    trail = getattr(prop, "price_history", None) or []
    out = []
    for entry in trail:
        if not isinstance(entry, dict):
            continue
        to = entry.get("total_price") or entry.get("price")
        frm = (entry.get("from") or {}).get("total_price") or \
              (entry.get("from") or {}).get("price")
        if to is None or frm is None:
            continue
        out.append({
            "at": entry.get("at"),
            "from": int(frm),
            "to": int(to),
            "delta_pct": round((to - frm) / frm * 100, 1) if frm else None,
            "direction": "down" if to < frm else "up",
        })
    out.reverse()
    return out
