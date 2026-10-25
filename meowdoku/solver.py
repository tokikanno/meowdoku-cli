"""Backtracking solver: one cat per row, column and region, no two cats touching."""


def solve(regions, limit=2):
    """Return up to `limit` solutions, each a tuple of the cat's column per row."""
    n = len(regions)
    solutions = []
    cols = [0] * n
    used_cols = [False] * n
    used_regions = [False] * n

    def place(r):
        if r == n:
            solutions.append(tuple(cols))
            return len(solutions) >= limit
        for c in range(n):
            reg = regions[r][c]
            if used_cols[c] or used_regions[reg]:
                continue
            if r > 0 and abs(cols[r - 1] - c) <= 1:
                continue
            cols[r] = c
            used_cols[c] = used_regions[reg] = True
            if place(r + 1):
                return True
            used_cols[c] = used_regions[reg] = False
        return False

    place(0)
    return solutions
