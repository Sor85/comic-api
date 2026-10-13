"""Normalize comic metadata without confusing labels, identifiers and counts."""
import re
import unicodedata


UNKNOWN_AUTHORS = {"佚名", "未知", "未知作者", "不详", "不詳", "作者不详", "作者不詳",
                   "无", "無", "暂无", "暫無", "多人", "多位作者", "unknown", "anonymous", "n/a", "-"}


def comic_authors(values):
    if isinstance(values, str):
        key = unicodedata.normalize("NFKC", values.strip()).casefold()
        values = re.split(r"[/／、;；|\n]+", values) if key not in UNKNOWN_AUTHORS else []
    if not isinstance(values, list):
        return []
    names, seen = [], set()
    for item in values:
        name = item.get("name") if isinstance(item, dict) else item
        if not isinstance(name, str):
            continue
        name = name.strip()
        key = unicodedata.normalize("NFKC", name).casefold()
        if name and key not in UNKNOWN_AUTHORS and key not in seen:
            names.append(name)
            seen.add(key)
    return names


def comic_page_count(value):
    # Only actual work-level image counts belong here, never chapters or API pages.
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value.strip()):
        value = int(value.strip())
    return value if type(value) is int and value > 0 else None


def comic_labels(values, value_key="name"):
    if not isinstance(values, list):
        return []
    labels, seen = [], set()
    for item in values:
        if isinstance(item, str) and value_key == "name":
            label = value = item.strip()
        elif isinstance(item, dict):
            label = item.get("name")
            value = item.get(value_key)
            if not isinstance(label, str) or not isinstance(value, str):
                continue
            label, value = label.strip(), value.strip()
        else:
            continue
        if label and value and value not in seen:
            labels.append({"label": label, "value": value})
            seen.add(value)
    return labels
