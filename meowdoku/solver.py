"""Solver: one cat per row, column and region, no two cats touching."""


def solutions(regions, limit=2):
    """Up to `limit` solutions (cat column per row), sorted.

    The board is a bitboard (bit r*n+c). Each step takes the region with the fewest
    open cells, and a cat rules out its row, column, region and neighbors at once."""
    n = len(regions)
    bit = [[1 << (r * n + c) for c in range(n)] for r in range(n)]
    region_mask = [0] * n
    for r in range(n):
        for c in range(n):
            region_mask[regions[r][c]] |= bit[r][c]
    # Everything a cat on (r, c) rules out, itself included.
    blocks = [[0] * n for _ in range(n)]
    for r in range(n):
        for c in range(n):
            m = region_mask[regions[r][c]]
            for i in range(n):
                m |= bit[r][i] | bit[i][c]
            for i in range(max(0, r - 1), min(n, r + 2)):
                for j in range(max(0, c - 1), min(n, c + 2)):
                    m |= bit[i][j]
            blocks[r][c] = m
    cells = {bit[r][c]: (r, c) for r in range(n) for c in range(n)}
    found = []
    cols = [0] * n

    def search(open_mask, todo):
        if not todo:
            found.append(tuple(cols))
            return len(found) >= limit
        best, best_count = None, n * n + 1
        for reg in todo:
            count = bin(region_mask[reg] & open_mask).count('1')
            if count < best_count:
                best, best_count = reg, count
                if count <= 1:
                    break
        if best_count == 0:
            return False
        rest = [reg for reg in todo if reg != best]
        avail = region_mask[best] & open_mask
        while avail:
            low = avail & -avail
            avail ^= low
            r, c = cells[low]
            cols[r] = c
            if search(open_mask & ~blocks[r][c], rest):
                return True
        return False

    search((1 << n * n) - 1, list(range(n)))
    return sorted(found)
