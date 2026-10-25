"""Game state: cell marks, reveals, hearts and undo/redo. No I/O here."""

from dataclasses import dataclass, field

EMPTY, MARK, GUESS, CAT, WRONG = range(5)

HEARTS_MAX = 3


@dataclass(frozen=True)
class Puzzle:
    n: int
    regions: tuple  # regions[r][c] -> region id 0..n-1
    solution: tuple  # solution[r] -> column of the cat in row r


@dataclass
class Move:
    """One undo step: cell diffs (r, c, prev, cur) plus the hearts it cost."""
    diffs: list = field(default_factory=list)
    cost: int = 0


class Game:
    def __init__(self, puzzle, hearts=HEARTS_MAX):
        self.puzzle = puzzle
        self.n = puzzle.n
        self.board = [[EMPTY] * self.n for _ in range(self.n)]
        self.hearts = hearts
        self.auto_mark = True
        self.guess_mode = False
        self.undo_stack = []
        self.redo_stack = []
        self._move = None  # open transaction: {(r, c): prev}, hearts before

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def cats(self):
        return sum(row.count(CAT) for row in self.board)

    @property
    def won(self):
        return self.cats == self.n

    @property
    def lost(self):
        return self.hearts <= 0

    @property
    def over(self):
        return self.won or self.lost

    def region(self, r, c):
        return self.puzzle.regions[r][c]

    def in_bounds(self, r, c):
        return 0 <= r < self.n and 0 <= c < self.n

    # ── transactions ─────────────────────────────────────────────────────────
    # Every change goes through _set inside a begin/end pair, so a whole
    # gesture (a keypress, a batch, a drag) becomes exactly one undo step.

    def begin(self):
        if self._move is None:
            self._move = ({}, self.hearts)

    def end(self):
        if self._move is None:
            return
        prevs, hearts_before = self._move
        self._move = None
        diffs = [(r, c, prev, self.board[r][c])
                 for (r, c), prev in prevs.items() if prev != self.board[r][c]]
        cost = hearts_before - self.hearts
        if diffs or cost:
            self.undo_stack.append(Move(diffs, cost))
            self.redo_stack.clear()

    @property
    def in_move(self):
        return self._move is not None

    def _set(self, r, c, val):
        prevs = self._move[0]
        prevs.setdefault((r, c), self.board[r][c])
        self.board[r][c] = val

    # ── actions ──────────────────────────────────────────────────────────────

    def mark_target(self, r, c):
        """What Space would turn this cell into, or None if it can't be marked."""
        cur = self.board[r][c]
        if cur in (CAT, WRONG):
            return None
        if self.guess_mode:
            if cur == MARK:
                return None
            return EMPTY if cur == GUESS else GUESS
        return EMPTY if cur == MARK else MARK

    def toggle_mark(self, r, c):
        if self.over:
            return False
        target = self.mark_target(r, c)
        if target is None:
            return False
        self.begin()
        self._set(r, c, target)
        self.end()
        return True

    def reveal(self, r, c):
        """Return 'cat', 'wrong' or None (nothing happened)."""
        if self.over or self.board[r][c] in (CAT, WRONG):
            return None
        self.begin()
        if self.puzzle.solution[r] == c:
            self._set(r, c, CAT)
            if self.auto_mark:
                self._auto_mark(r, c)
            result = 'cat'
        else:
            self.hearts -= 1
            self._set(r, c, WRONG)
            result = 'wrong'
        self.end()
        return result

    def forced_cell(self):
        """First cell, in reading order, that is the only one left unmarked in its row,
        column or region (which has no cat yet). Guesses count as unmarked: they're
        tentative, and revealing on a guess could cost a heart."""
        n = self.n
        units = ([[(r, c) for c in range(n)] for r in range(n)]
                 + [[(r, c) for r in range(n)] for c in range(n)])
        regions = {}
        for r in range(n):
            for c in range(n):
                regions.setdefault(self.region(r, c), []).append((r, c))
        units += regions.values()
        forced = set()
        for unit in units:
            states = [self.board[r][c] for r, c in unit]
            if CAT in states:
                continue
            open_cells = [cell for cell, st in zip(unit, states) if st in (EMPTY, GUESS)]
            if len(open_cells) == 1:
                forced.add(open_cells[0])
        return min(forced) if forced else None

    def _auto_mark(self, r, c):
        n, reg = self.n, self.region(r, c)
        for i in range(n):
            for j in range(n):
                if (i == r or j == c or self.region(i, j) == reg
                        or (abs(i - r) <= 1 and abs(j - c) <= 1)):
                    if self.board[i][j] in (EMPTY, GUESS):
                        self._set(i, j, MARK)

    # ── undo / redo ──────────────────────────────────────────────────────────
    # Undo never refunds a heart and redo charges it again, as in the web game.

    def undo(self):
        self.end()
        if self.over or not self.undo_stack:
            return False
        move = self.undo_stack.pop()
        for r, c, prev, _ in move.diffs:
            self.board[r][c] = prev
        self.redo_stack.append(move)
        return True

    def redo(self):
        self.end()
        if self.over or not self.redo_stack:
            return False
        move = self.redo_stack.pop()
        for r, c, _, cur in move.diffs:
            self.board[r][c] = cur
        self.hearts -= move.cost
        self.undo_stack.append(move)
        return True
