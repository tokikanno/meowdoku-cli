"""Level generator, ported from plko/meowdoku tools/generate.py.

1. Place n cats: a random permutation with no two consecutive rows in adjacent columns.
2. Grow one region per cat by randomized multi-source flood fill.
3. If the board has another solution, recolor a cell that the alternate needs
   (never a cat's own cell, never disconnecting a region) until it's unique.
4. Keep the board only if pure deduction solves it (see `logic_solvable`), so a
   generated level never asks the player to guess.
"""

import random
from collections import deque
from itertools import combinations

from .model import Puzzle
from .solver import solutions


# ── deduction ────────────────────────────────────────────────────────────────

def logic_solvable(regions):
    """Whether deduction alone (no guessing) places every cat. The rules follow the
    web game's difficulty analyzer (tiers 1–7):
      · a row, column or region with one candidate left holds the cat
      · a cell that every candidate of some unit would rule out is ruled out
      · k units of one kind whose candidates fit inside k units of another kind
        own those; everything else in the latter is ruled out"""
    n = len(regions)
    cand = {(r, c) for r in range(n) for c in range(n)}
    rows = [[(r, c) for c in range(n)] for r in range(n)]
    cols = [[(r, c) for r in range(n)] for c in range(n)]
    regs = [[(r, c) for r in range(n) for c in range(n) if regions[r][c] == k] for k in range(n)]
    kind_of = {'row': lambda cell: cell[0], 'col': lambda cell: cell[1],
               'reg': lambda cell: regions[cell[0]][cell[1]]}
    partitions = {'row': rows, 'col': cols, 'reg': regs}
    cats = set()

    def excluded_by(cell):
        r, c = cell
        reg = regions[r][c]
        return {(i, j) for (i, j) in cand if (i, j) != cell and
                (i == r or j == c or regions[i][j] == reg or (abs(i - r) <= 1 and abs(j - c) <= 1))}

    def place(cell):
        cats.add(cell)
        cand.difference_update(excluded_by(cell))
        cand.discard(cell)

    def open_units():
        for name, units in partitions.items():
            key = kind_of[name]
            for unit in units:
                if not any(key(cat) == key(unit[0]) for cat in cats):
                    yield name, [cell for cell in unit if cell in cand]

    def step():
        units = list(open_units())
        if any(not cells for _, cells in units):
            return False  # contradiction; can't happen on a solvable board
        for _, cells in units:  # tier 1
            if len(cells) == 1:
                place(cells[0])
                return True
        for _, cells in units:  # tier 2
            common = set.intersection(*(excluded_by(cell) for cell in cells))
            if common:
                cand.difference_update(common)
                return True
        for k in range(2, n // 2 + 1):  # tier k
            for a, b in (('row', 'col'), ('col', 'row'), ('row', 'reg'), ('reg', 'row'),
                         ('col', 'reg'), ('reg', 'col')):
                group = [cells for name, cells in units if name == a]
                key = kind_of[b]
                for combo in combinations(group, k):
                    inside = {key(cell) for cells in combo for cell in cells}
                    if len(inside) != k:
                        continue
                    own = {cell for cells in combo for cell in cells}
                    out = {cell for cell in cand if key(cell) in inside and cell not in own}
                    if out:
                        cand.difference_update(out)
                        return True
        return False

    while len(cats) < n:
        if not step():
            return False
    return True


# ── generating ───────────────────────────────────────────────────────────────

def _cat_columns(n, rng):
    perm = list(range(n))
    while True:
        rng.shuffle(perm)
        if all(abs(perm[r] - perm[r - 1]) > 1 for r in range(1, n)):
            return perm


def _grow_regions(n, cat_cols, rng):
    owner = [[None] * n for _ in range(n)]
    frontiers = []
    for row, col in enumerate(cat_cols):
        owner[row][col] = row
        frontiers.append(deque([(row, col)]))
    unclaimed = n * n - n
    active = list(range(n))
    while unclaimed:
        rng.shuffle(active)
        progressed = False
        for idx in active:
            frontier = frontiers[idx]
            if not frontier:
                continue
            r, c = frontier[0]
            neighbors = [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]
            rng.shuffle(neighbors)
            for nr, nc in neighbors:
                if 0 <= nr < n and 0 <= nc < n and owner[nr][nc] is None:
                    owner[nr][nc] = idx
                    frontier.append((nr, nc))
                    unclaimed -= 1
                    progressed = True
                    break
            else:
                frontier.popleft()
        if not progressed:
            return None
    return owner


def _connected(cells, seed):
    seen, todo = {seed}, deque([seed])
    while todo:
        r, c = todo.popleft()
        for nb in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if nb in cells and nb not in seen:
                seen.add(nb)
                todo.append(nb)
    return seen == cells


def _repair(n, owner, cat_cols, rng, max_iters=400):
    """Recolor cells until cat_cols is the only solution; None if that fails."""
    seeds = {k: (k, cat_cols[k]) for k in range(n)}
    seed_cells = set(seeds.values())
    cells_of = {k: set() for k in range(n)}
    for r in range(n):
        for c in range(n):
            cells_of[owner[r][c]].add((r, c))

    def move(cell, new):
        old = owner[cell[0]][cell[1]]
        cells_of[old].discard(cell)
        if not cells_of[old] or not _connected(cells_of[old], seeds[old]):
            cells_of[old].add(cell)
            return False
        cells_of[new].add(cell)
        owner[cell[0]][cell[1]] = new
        return True

    def neighbors(r, c):
        return [(i, j) for i, j in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1))
                if 0 <= i < n and 0 <= j < n]

    for _ in range(max_iters):
        alt = next((s for s in solutions(owner, limit=2) if list(s) != cat_cols), None)
        if alt is None:
            return owner
        diff_rows = [r for r in range(n) if alt[r] != cat_cols[r]]
        rng.shuffle(diff_rows)
        moved = False
        for row in diff_rows:
            cell = (row, alt[row])
            if cell in seed_cells:
                continue
            old = owner[row][alt[row]]
            options = [owner[i][j] for i, j in neighbors(*cell) if owner[i][j] != old]
            if not options:
                continue
            # Prefer a region the alternate already used above: it then breaks there.
            used_above = {owner[r2][alt[r2]] for r2 in range(row)}
            preferred = [k for k in options if k in used_above]
            if move(cell, rng.choice(preferred or options)):
                moved = True
                break
        if not moved:
            # Stuck: nudge a random cell into a neighboring region.
            cell = (rng.randrange(n), rng.randrange(n))
            if cell in seed_cells:
                continue
            nbs = neighbors(*cell)
            rng.shuffle(nbs)
            for i, j in nbs:
                if owner[i][j] != owner[cell[0]][cell[1]] and move(cell, owner[i][j]):
                    break
    return owner if len(solutions(owner, limit=2)) == 1 else None


def generate(n, rng=None, require_logic=True, max_attempts=1000):
    """A Puzzle with a unique solution (and, by default, solvable without guessing)."""
    rng = rng or random.Random()
    for _ in range(max_attempts):
        cat_cols = _cat_columns(n, rng)
        owner = _grow_regions(n, cat_cols, rng)
        if owner is None:
            continue
        if len(solutions(owner, limit=2)) != 1:
            owner = _repair(n, owner, cat_cols, rng)
            if owner is None:
                continue
        regions = tuple(tuple(row) for row in owner)
        if require_logic and not logic_solvable(regions):
            continue
        return Puzzle(n, regions, tuple(cat_cols))
    raise RuntimeError(f'無法產生 {n}×{n} 的關卡')


def format_level(puzzle, notes=()):
    """The `level_*.txt` format, readable by levels.parse_level; `notes` become
    extra `# ` comment lines."""
    rows = [''.join(chr(ord('A') + k) for k in row) for row in puzzle.regions]
    lines = [str(puzzle.n), *rows, '# solution: ' + ' '.join(map(str, puzzle.solution))]
    return '\n'.join(lines + [f'# {note}' for note in notes]) + '\n'


# ── seeds ────────────────────────────────────────────────────────────────────
# A level code like `12-5f3a9c21` (size, then a 32-bit seed in hex) regenerates the
# same board, as long as the generator above stays the same: any change to it may
# turn old codes into different boards. Favorites keep the full board for that reason.

def new_seed():
    return random.getrandbits(32)


def seed_code(n, seed):
    return f'{n}-{seed:08x}'


def parse_seed_code(code):
    """(n, seed) from a level code; ValueError if it isn't one."""
    size, sep, seed = code.strip().lower().partition('-')
    if not sep or not size.isdigit() or not 6 <= int(size) <= 12:
        raise ValueError(f'關卡代碼要像 12-5f3a9c21（大小 6～12、連字號、十六進位），不是「{code}」')
    try:
        value = int(seed, 16)
    except ValueError:
        raise ValueError(f'關卡代碼要像 12-5f3a9c21（大小 6～12、連字號、十六進位），不是「{code}」')
    if not 0 <= value < 1 << 32:
        raise ValueError(f'關卡代碼的 seed 最多 8 位十六進位：「{code}」')
    return int(size), value


def generate_from_seed(n, seed):
    return generate(n, random.Random(seed))
