"""Rank only measured popularity, within the configured collection window."""
def count(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = int(value)
        return n if n >= 0 and str(value).strip() == str(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def rank(candidates, mode, minimum=0):
    if mode == 'recent':
        return sorted(candidates, key=lambda i: candidates[i]['published'], reverse=True)
    eligible = [i for i, c in candidates.items() if c['reads'] is not None and c['reads'] >= minimum]
    return sorted(eligible, key=lambda i: (candidates[i]['reads'], candidates[i]['likes'] or 0,
                                         candidates[i]['published']), reverse=True)
