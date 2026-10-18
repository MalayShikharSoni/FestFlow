"""Template filters: rupee formatting and the Query Lab's JSON display."""
from typing import Any

from django import template

register = template.Library()


@register.filter
def inr(value: Any) -> str:
    """12345678 -> "₹1,23,45,678" (Indian digit grouping: last 3, then pairs)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    sign, digits = ("-", str(-number)) if number < 0 else ("", str(number))
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"₹{sign}{','.join(groups + [tail]) if groups else tail}"


@register.filter
def get_item(mapping: dict[str, Any], key: str) -> Any:
    """{{ row.byDuty|get_item:"gate" }} — dict lookup with a variable key."""
    return (mapping or {}).get(key, 0)


@register.filter
def nice(value: Any) -> str:
    """"pro_show" -> "Pro show", "help_desk" -> "Help desk"."""
    text = str(value).replace("_", " ")
    return text[:1].upper() + text[1:]
