def find_duplicates(items):
    """Return all values that appear more than once, in first-occurrence order,
    each listed only once."""
    counts = {}
    order = []
    for x in items:
        if x not in counts:
            order.append(x)
        counts[x] = counts.get(x, 0) + 1
    return [x for x in order if counts[x] > 1]
