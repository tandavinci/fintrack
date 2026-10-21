"""Conflict-preserving fusion of recommendations (STEERING section 5, rule 4).

When the quant signal and the deep decision disagree we keep BOTH and flag the
conflict — we never average them. ``fuse`` is the minimal contract-adjacent
helper that implements that rule: the recommendations pass through verbatim and
a boolean flags whether their actions diverge.

NOTE: Step 5.1 replaces this with the richer product fusion view. Until then
this stays deliberately small and side-effect free.
"""


def fuse(recs):
    """Return the recommendations verbatim plus a conflict flag.

    ``recs`` is any iterable of Recommendation-shaped objects exposing an
    ``action`` attribute. The objects are passed through unchanged (no copying,
    no mutation, no averaging). ``conflict`` is True when more than one distinct
    action is present.
    """
    recs = list(recs)
    return {
        "recommendations": recs,
        "conflict": len({r.action for r in recs}) > 1,
    }
