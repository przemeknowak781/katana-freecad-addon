"""Matching contours between neighbouring sections.

When every plane cuts one contour there is nothing to do.  The moment a mesh has
separate limbs - a robot arm, a bracket with two bosses - each plane returns
several contours and the loft needs to know which contour in section *i* belongs
to the same body as which contour in section *i+1*.  Get it wrong and the loft
either fails or joins the wrong pair with a spectacular twist.

Pairing is by centroid distance measured *in the plane*, greedily, closest first.
Alternatives considered: by enclosed area (fails the moment a limb tapers) and by
containment (right for holes, wrong for separate bodies - that is the open
question in the spec, and it is a different relation, not a better metric for
this one).  Ambiguous matches are reported rather than silently resolved.
"""

import numpy as np

from .planes import normalize

#: A match is ambiguous when the runner-up is within this factor of the winner.
#: 0.6 means "the second candidate is more than 60% as close as the first".
AMBIGUITY_RATIO = 0.6


def in_plane_distance(a, b, direction):
    """Distance between two points ignoring their offset along ``direction``.

    Sections are separated along the direction by construction; including that
    component would make every pair look equally far apart.
    """
    d = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    return float(np.linalg.norm(d - np.dot(d, direction) * direction))


def match_pair(previous, current, direction, ambiguity_ratio=AMBIGUITY_RATIO):
    """One-to-one greedy match between two sets of centroids.

    Returns ``(mapping, ambiguous)`` where ``mapping[i] = j`` links index ``i``
    of ``previous`` to index ``j`` of ``current``, and ``ambiguous`` is the list
    of ``i`` whose runner-up was nearly as good as the winner.
    """
    if not len(previous) or not len(current):
        return {}, []

    cost = np.array([[in_plane_distance(a, b, direction) for b in current]
                     for a in previous], dtype=float)

    order = sorted(((cost[i, j], i, j)
                    for i in range(cost.shape[0])
                    for j in range(cost.shape[1])))
    mapping = {}
    used_current = set()
    for _, i, j in order:
        if i in mapping or j in used_current:
            continue
        mapping[i] = j
        used_current.add(j)

    ambiguous = []
    for i, j in mapping.items():
        # Rivals are the *other* candidates for this contour, selected by index
        # rather than by cost - an exact tie is the most ambiguous case there
        # is, and comparing by value would filter it out.
        rivals = [cost[i, k] for k in range(cost.shape[1]) if k != j]
        if rivals and min(rivals) <= cost[i, j] / ambiguity_ratio:
            ambiguous.append(i)
    return mapping, sorted(ambiguous)


def build_chains(centroids_per_section, direction, ambiguity_ratio=AMBIGUITY_RATIO):
    """Link contours across all sections into chains.

    ``centroids_per_section`` is a list (one entry per section) of ``(m, 3)``
    centroid arrays.  Returns ``(chains, ambiguous)``:

    * ``chains`` - list of chains, each a list of ``(section_index,
      contour_index)`` pairs in section order.  A chain is what gets lofted.
    * ``ambiguous`` - list of ``(section_index, contour_index)`` where the match
      was a close call and the user should look at the result.

    A contour that matches nothing starts a new chain, so a mesh whose limbs
    split halfway up produces one chain for the trunk and one per limb rather
    than a failure.
    """
    d = normalize(direction)
    chains = []
    ambiguous = []
    # Contours of the previous section that a chain can still grow from, as
    # (chain, centroid) - the centroid has to travel with the chain because the
    # chain itself only stores indices.
    open_chains = []

    for section_index, raw in enumerate(centroids_per_section):
        centroids = np.asarray(raw, dtype=float).reshape(-1, 3)
        next_open = {}
        matched = set()

        if open_chains and len(centroids):
            mapping, ambiguous_local = match_pair(
                [c for _, c in open_chains], centroids, d, ambiguity_ratio)
            for i, j in mapping.items():
                chain = open_chains[i][0]
                chain.append((section_index, j))
                next_open[j] = (chain, centroids[j])
                matched.add(j)
            for i in ambiguous_local:
                ambiguous.append((section_index, mapping[i]))

        for j in range(len(centroids)):
            if j in matched:
                continue
            chain = [(section_index, j)]
            chains.append(chain)
            next_open[j] = (chain, centroids[j])

        open_chains = [next_open[j] for j in sorted(next_open)]

    return chains, ambiguous
