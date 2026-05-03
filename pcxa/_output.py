"""Output formatting helpers shared across all command modules."""

import json


def out_json(data):
    print(json.dumps(data, indent=2, default=str))


def out_table(rows, columns):
    if not rows:
        print("No results.")
        return
    widths = {}
    for col in columns:
        widths[col] = len(col)
        for row in rows:
            widths[col] = max(widths[col], len(str(row.get(col, ""))))
    header = " | ".join(col.ljust(widths[col]) for col in columns)
    sep = "-+-".join("-" * widths[col] for col in columns)
    print(header)
    print(sep)
    for row in rows:
        print(" | ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns))


def fmt_size(b):
    if b is None:
        return "-"
    for u in ("B", "KB", "MB", "GB"):
        if abs(b) < 1024:
            return f"{b:.1f}{u}"
        b /= 1024
    return f"{b:.1f}TB"


def tag_names(tags):
    if not tags:
        return ""
    return ",".join(
        t.get("name", t) if isinstance(t, dict) else str(t) for t in tags
    )


__all__ = ["out_json", "out_table", "fmt_size", "tag_names"]
