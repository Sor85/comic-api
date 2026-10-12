"""Keep comic metadata labels separate from their upstream search identifiers."""


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
