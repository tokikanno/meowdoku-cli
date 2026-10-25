"""The game screen: board drawing and key handling. Rules live in model.Game."""

import curses
import itertools
import re
import time
import unicodedata

from .model import CAT, EMPTY, GUESS, HEARTS_MAX, MARK, WRONG
from .term import (CTRL_G, CTRL_R, CTRL_X, ENTER, ESC, MOVES, SHIFT_MOVES, Screen,
                   fmt_time, log_key, text_width)

# Every symbol is 2 columns wide and every cell an even width, so all of them center.
SYMBOLS = {EMPTY: '', MARK: '❌', GUESS: '❓', CAT: '🐱', WRONG: '💥'}
SYMBOLS_ASCII = {EMPTY: '', MARK: 'x', GUESS: '?', CAT: '@', WRONG: '!'}

# Board styles, switched with `b`:
#   grid    every cell an equal block; region edges drawn as lines in the gaps
#   lines   like grid, but every cell is boxed in, region edge or not
#   blocks  no gaps; the row between two cells is split by half blocks (▀), so each
#           cell is exactly one pitch tall and regions meet edge to edge
#   plain   fallback for small terminals: cells side by side, no gap rows
# Cell sizes per style, largest first; the first that fits wins.
CELL_SIZES = {'grid': [(8, 3), (4, 1)], 'lines': [(8, 3), (4, 1)],
              'blocks': [(8, 3), (4, 1)], 'plain': [(4, 1)]}
STYLES = ['grid', 'lines', 'blocks']  # the order `b` cycles through
DEFAULT_STYLE = 'blocks'  # falls back to grid without 256 colors
STYLE_NAMES = {'grid': '框線版', 'lines': '全格線版', 'blocks': '色塊版'}
LABEL_W = 3  # room for the row letters left of the board
NBSP = '\u00a0'
CHROME_H = 7  # header + column labels above the board, gap + 4 footer lines below

HELP = [
    ('方向鍵 / hjkl', '移動，到邊界從另一邊出現'),
    ('Space', '標記／取消標記（依模式寫入 {X} 或 {G}）'),
    ('g', '切換標記模式：無貓 {X} ↔ 猜測 {G}'),
    ('Enter', '翻開，翻錯扣一格生命'),
    ('Enter（貓上）', '跳到下一個唯一沒標記的格子並翻開'),
    ('Shift+方向鍵', '拖拽標記：起點沒標記就一路標記，有標記就一路清除'),
    ('v', '連續標記模式：移動時一路標記，再按 v 或 Esc 結束'),
    ('H / L', '同色格子中，目前格子左／右側的全部標記'),
    ('K / J', '同色格子中，目前格子上／下方的全部標記'),
    ('s', '同色剩下的格子都在同一行／列時，標記推得出的格子'),
    ('Ctrl-G', '清除所有猜測 {G}'),
    (':', '跳到指定格子：字母 + 數字，例如 c5'),
    ('a', '切換翻開後自動標記'),
    ('t', '切換座標提示'),
    ('b', '切換盤面畫法：框線版 → 全格線版 → 色塊版'),
    ('u / Ctrl-R', 'undo / redo'),
    ('r', '重新開始這一關'),
    ('n', '下一關（結束後；過關後也可以按 Enter）'),
    ('Esc / m', '回到選單'),
    ('q', '離開（會先確認）'),
    ('Ctrl-X', '直接離開'),
]


def _box_chars():
    """Box-drawing char for each (up, down, left, right) weight: 0 none, 1 light, 2 heavy."""
    dirs = {'UP': 'u', 'DOWN': 'd', 'LEFT': 'l', 'RIGHT': 'r',
            'VERTICAL': 'ud', 'HORIZONTAL': 'lr'}
    table = {}
    for cp in range(0x2500, 0x2580):
        name = unicodedata.name(chr(cp))[len('BOX DRAWINGS '):]
        if any(w in name for w in ('DOUBLE', 'DASH', 'ARC', 'DIAGONAL')):
            continue
        groups = [g.split() for g in name.split(' AND ')]
        weights = [next((w for w in g if w in ('LIGHT', 'HEAVY')), None) for g in groups]
        default = next(w for w in weights if w)
        key = dict.fromkeys('udlr', 0)
        for g, w in zip(groups, weights):
            for word in g:
                for d in dirs.get(word, ''):
                    key[d] = 1 if (w or default) == 'LIGHT' else 2
        table[key['u'], key['d'], key['l'], key['r']] = chr(cp)
    return table


BOX = _box_chars()


def line_code(y, n):
    """n bits for screen row y: y's low n-1 bits in bits 1.., their parity in bit 0.
    Any two rows less than 2**(n-1) apart differ in at least two bits, so they still
    differ when the cursor's brackets hide one cell's bit."""
    v = y % (1 << (n - 1))
    return v << 1 | bin(v).count('1') & 1

BATCH_SIDES = {'H': (0, -1), 'L': (0, 1), 'K': (-1, 0), 'J': (1, 0)}


class GameView(Screen):
    """Plays one level. run() returns 'quit', 'menu', 'next' or 'restart'."""

    def __init__(self, scr, colors, game, title, settings, on_win=None, has_menu=True,
                 has_next=True, ascii=False, elapsed=0.0, best_time=None, resumed=False):
        super().__init__(scr, colors)
        self.game = game
        self.title = title
        self.settings = settings  # shared across levels: auto_mark, show_coords
        game.auto_mark = settings.get('auto_mark', True)
        self.on_win = on_win
        self.has_menu = has_menu
        self.has_next = has_next
        self.symbols = SYMBOLS_ASCII if ascii else SYMBOLS
        self.hearts = ('*', '.') if ascii else ('♥', '♡')
        self.cur = (0, 0)
        self.modal = None  # None | 'help' | 'quit' | 'menu' | 'restart' | 'over'
        self.drag = None  # {'target': state painted, 'sticky': True for v mode}
        self.prompt = None  # text typed after ':', while jumping
        self.result = None
        self.best_time = best_time
        self.clock_base = elapsed  # seconds played before this screen opened
        # A fresh level's clock waits for the first mark, guess or reveal; a resumed
        # one was already running, so it carries on at once.
        self.clock_start = None
        self.clock_stop = None  # frozen total once the level is over
        if resumed:
            self.start_clock()
        if resumed:
            self.show_toast('接續上次的進度')

    def start_clock(self):
        if self.clock_start is None:
            self.clock_start = time.monotonic()
            self.tick = 1.0

    def elapsed(self):
        if self.clock_stop is not None:
            return self.clock_stop
        if self.clock_start is None:
            return self.clock_base
        return self.clock_base + time.monotonic() - self.clock_start

    # ── drawing ──────────────────────────────────────────────────────────────

    def layout(self):
        """(style, cw, ch, x0, y0, geometry) for the largest board that fits, or None.
        A cell (r, c) sits at (by + oy + r * py, bx + ox + c * px)."""
        h, w = self.scr.getmaxyx()
        n = self.game.n
        style = self.settings.get('style', DEFAULT_STYLE)
        if style == 'blocks' and not self.colors.rich:
            style = 'grid'
        for st in (style, 'plain'):
            for cw, ch in CELL_SIZES[st]:
                if st in ('grid', 'lines'):
                    geo = (cw + 1, ch + 1, 1, 1, n * (cw + 1) + 1, n * (ch + 1) + 1)
                elif st == 'blocks':
                    geo = (cw, ch + 1, 0, 1, n * cw, n * (ch + 1) + 1)
                else:
                    geo = (cw, ch, 0, 0, n * cw, n * ch)
                bw, bh = LABEL_W + geo[4], CHROME_H + geo[5]
                if bw <= w and bh <= h:
                    return st, cw, ch, max(0, (w - bw) // 2), max(0, (h - bh) // 2), geo
        return None

    def draw(self):
        self.scr.erase()
        self.colors.new_frame()
        lay = self.layout()
        if lay is None:
            n = self.game.n
            cw, ch = CELL_SIZES['plain'][-1]
            self.put(0, 0, f'終端機太小，至少需要 {LABEL_W + n * cw}×{CHROME_H + n * ch}')
            return
        style, cw, ch, x0, y0, (px, py, ox, oy, board_w, board_h) = lay
        bx, by = x0 + LABEL_W, y0 + 2  # top-left of the board, frame included
        n = self.game.n

        def cell_xy(r, c):
            return bx + ox + c * px, by + oy + r * py

        self.draw_header(y0, x0)
        if self.settings.get('show_coords') or self.prompt is not None:
            for c in range(n):
                label = str(c + 1)
                self.put(by - 1, cell_xy(0, c)[0] + (cw - len(label)) // 2, label, curses.A_DIM)
            for r in range(n):
                self.put(cell_xy(r, 0)[1] + (ch - 1) // 2, x0, chr(ord('A') + r), curses.A_DIM)
        if style == 'blocks':
            self.draw_blocks(bx, by, cw, ch)
        for r in range(n):
            for c in range(n):
                self.draw_cell(r, c, *cell_xy(r, c), cw, ch,
                               brackets=style not in ('grid', 'lines'))
        if style in ('grid', 'lines'):
            self.draw_grid(bx, by, cw, ch, every_cell=style == 'lines')
        self.draw_footer(by + board_h + 1, x0)
        self.draw_overlay()

    def draw_overlay(self):
        if self.modal == 'help':
            marks = {'X': self.symbols[MARK], 'G': self.symbols[GUESS]}
            rows = [k + ' ' * (16 - text_width(k)) + v.format(**marks) for k, v in HELP]
            if len(rows) + 6 > self.scr.getmaxyx()[0]:
                # Too short for one column: put the second half beside the first.
                half = (len(rows) + 1) // 2
                left, right = rows[:half], rows[half:] + ['']
                width = max(text_width(t) for t in left) + 3
                rows = [a + ' ' * (width - text_width(a)) + b for a, b in zip(left, right)]
            self.draw_box(['操作說明', ''] + rows + ['', '按任意鍵關閉'], center=False)
        elif self.modal == 'quit':
            self.draw_box(['離開？(y/n)'])
        elif self.modal == 'menu':
            self.draw_box(['放棄這一關，回到選單？(y/n)'])
        elif self.modal == 'restart':
            self.draw_box(['放棄目前的進度，重新開始？(y/n)'])
        elif self.modal == 'over':
            self.draw_box(self.over_lines())
        elif self.toast:
            self.draw_box([self.toast[0]])

    def draw_header(self, y, x):
        g = self.game
        hearts = self.hearts[0] * g.hearts + self.hearts[1] * (HEARTS_MAX - g.hearts)
        mode = f'猜測 {self.symbols[GUESS]}' if g.guess_mode else f'無貓 {self.symbols[MARK]}'
        for text, attr in ((f'{self.title}  {hearts}  {fmt_time(self.elapsed())}  ', curses.A_BOLD),
                           (f' {mode} ', curses.A_REVERSE if g.guess_mode else curses.A_BOLD),
                           (f'  自動標記:{"開" if g.auto_mark else "關"}', 0),
                           (self.drag_label(), curses.A_REVERSE)):
            self.put(y, x, text, attr)
            x += text_width(text)

    def draw_cell(self, r, c, x, y, cw, ch, brackets):
        g = self.game
        st = g.board[r][c]
        attr = self.colors.region(g.region(r, c), dim=st == MARK)
        on_cursor = brackets and (r, c) == self.cur
        for dy in range(ch):
            # A cell's first column is ' ' or a look-alike no-break space, chosen so no
            # two board lines hold the same characters (see line_code). ncurses compares
            # lines by characters only; equal ones made it "scroll" the board on a move
            # and repaint it, which flickers.
            mark = NBSP if line_code(y + dy, g.n) >> c & 1 and not on_cursor else ' '
            self.put(y + dy, x, mark + ' ' * (cw - 1), attr)
        sym = self.symbols[st]
        if not sym and not self.colors.rich:
            sym = chr(ord('a') + g.region(r, c))
        if sym:
            self.put(y + (ch - 1) // 2, x + (cw - text_width(sym)) // 2, sym, attr | curses.A_BOLD)
        if on_cursor:
            cursor = self.colors.cursor
            if self.colors.rich:  # yellow brackets on the cell's own background
                cursor = self.colors.pair(self.colors.cursor_fg, self.cell_slot(r, c)) | curses.A_BOLD
            for dy in range(ch):
                self.put(y + dy, x, '[', cursor)
                self.put(y + dy, x + cw - 1, ']', cursor)

    def cell_slot(self, r, c):
        return self.colors.slot(self.game.region(r, c), dim=self.game.board[r][c] == MARK)

    def draw_blocks(self, bx, by, cw, ch):
        """The rows between cells: upper half takes the cell above, lower half the cell
        below, so every cell grows by half a row each way and all stay the same size."""
        n, default = self.game.n, self.colors.default_bg
        for lr in range(n + 1):
            for c in range(n):
                upper = self.cell_slot(lr - 1, c) if lr > 0 else None
                lower = self.cell_slot(lr, c) if lr < n else None
                if upper is None:
                    char, attr = '▄', self.colors.pair(lower, default)
                elif lower is None:
                    char, attr = '▀', self.colors.pair(upper, default)
                else:
                    char, attr = '▀', self.colors.pair(upper, lower)
                self.put(by + lr * (ch + 1), bx + c * cw, char * cw, attr)

    def draw_grid(self, bx, by, cw, ch, every_cell=False):
        """Lines in the gaps between cells: around each region, or around every cell."""
        n, reg = self.game.n, self.game.region

        def h_edge(lr, c):  # horizontal line lr (0..n) above row lr, over column c
            return every_cell or lr in (0, n) or reg(lr - 1, c) != reg(lr, c)

        def v_edge(r, lc):  # vertical line lc (0..n) left of column lc, beside row r
            return every_cell or lc in (0, n) or reg(r, lc - 1) != reg(r, lc)

        def junction(lr, lc):  # (up, down, left, right) edges meeting at a grid point
            return (int(lr > 0 and v_edge(lr - 1, lc)), int(lr < n and v_edge(lr, lc)),
                    int(lc > 0 and h_edge(lr, lc - 1)), int(lc < n and h_edge(lr, lc)))

        def h_line(lr, c, attr, edge):
            self.put(by + lr * (ch + 1), bx + 1 + c * (cw + 1), ('─' if edge else ' ') * cw, attr)

        def v_line(r, lc, attr, edge):
            for dy in range(ch):
                self.put(by + 1 + r * (ch + 1) + dy, bx + lc * (cw + 1), '│' if edge else ' ', attr)

        for lr, c in itertools.product(range(n + 1), range(n)):
            edge = h_edge(lr, c)
            h_line(lr, c, 0, edge)
        for r, lc in itertools.product(range(n), range(n + 1)):
            edge = v_edge(r, lc)
            v_line(r, lc, 0, edge)
        for lr, lc in itertools.product(range(n + 1), range(n + 1)):
            key = junction(lr, lc)
            self.put(by + lr * (ch + 1), bx + lc * (cw + 1), BOX[key] if any(key) else ' ', 0)

        # The cursor is a full frame around its cell, merged into the region edges it meets.
        r0, c0 = self.cur
        cursor = self.colors.cursor
        h_line(r0, c0, cursor, True)
        h_line(r0 + 1, c0, cursor, True)
        v_line(r0, c0, cursor, True)
        v_line(r0, c0 + 1, cursor, True)
        for dr, dc, extra in ((0, 0, (0, 1, 0, 1)), (0, 1, (0, 1, 1, 0)),
                              (1, 0, (1, 0, 0, 1)), (1, 1, (1, 0, 1, 0))):
            lr, lc = r0 + dr, c0 + dc
            key = tuple(max(a, b) for a, b in zip(junction(lr, lc), extra))
            self.put(by + lr * (ch + 1), bx + lc * (cw + 1), BOX[key], cursor)

    def draw_footer(self, y, x):
        g = self.game
        if g.won:
            self.put(y, x, '過關！  ' + self.over_keys(), curses.A_BOLD)
        elif g.lost:
            self.put(y, x, '生命用完了  ' + self.over_keys(), curses.A_BOLD)
        elif self.prompt is not None:
            self.put(y, x, f'跳到：{self.prompt}_   字母選行、數字選列，例如 c5 · Enter 確定 · Esc 取消',
                     curses.A_BOLD)
        else:
            self.put(y, x, f'貓 {g.cats}/{g.n}')
        self.put(y + 1, x, 'Space 標記  Enter 翻開  g 模式  u/^R 復原/重做', curses.A_DIM)
        self.put(y + 2, x, 'Shift+方向 拖拽  v 連續標記  HJKL 同色批次  s 輔助  : 跳轉',
                 curses.A_DIM)
        self.put(y + 3, x, f'r 重來  b 畫法  {"Esc 選單  " if self.has_menu else ""}? 說明  ^X 離開',
                 curses.A_DIM)

    def drag_label(self):
        if not self.drag:
            return ''
        what = '清除' if self.drag['target'] == EMPTY else '標記'
        return f'  {"連續" if self.drag["sticky"] else "拖拽"}{what} '

    def over_keys(self):
        nxt = 'Enter/n 下一關' if self.game.won else 'n 下一關'
        keys = [nxt] if self.has_next else []
        keys += ['r 重玩'] + (['m 選單'] if self.has_menu else []) + ['q 離開']
        return ' · '.join(keys)

    def over_lines(self):
        g = self.game
        if not g.won:
            return ['生命用完了', '', self.over_keys(), '其他鍵：關閉這個視窗']
        took = self.elapsed()
        if self.best_time is None or took < self.best_time:
            timing = f'用時 {fmt_time(took)} · 新紀錄！'
        else:
            timing = f'用時 {fmt_time(took)}（最佳 {fmt_time(self.best_time)}）'
        head = '過關！' + '★' * g.hearts + '☆' * (HEARTS_MAX - g.hearts)
        return [head, timing, '', self.over_keys(), '其他鍵：關閉這個視窗']

    # ── input ────────────────────────────────────────────────────────────────

    def run(self):
        try:
            return self._run()
        except KeyboardInterrupt:
            return 'quit'  # Ctrl-C: leave like Ctrl-X, so the level still gets saved

    def _run(self):
        while self.result is None:
            self.draw()
            self.scr.refresh()
            key = self.read_key()
            if key is None:
                continue
            if key == CTRL_X:
                return 'quit'
            if self.modal:
                self.handle_modal(key)
            else:
                self.handle_key(key)
            if self.game.undo_stack or self.game.in_move:
                self.start_clock()  # the first change to the board starts the clock
        return self.result

    def handle_modal(self, key):
        modal, self.modal = self.modal, None
        yes = key in ('y', 'Y')
        if modal == 'quit' and yes:
            self.result = 'quit'
        elif modal in ('menu', 'restart') and yes:
            self.result = modal
        elif modal == 'over' and (key in ('n', 'r', 'm', 'q') or key in ENTER):
            self.handle_key(key)

    def handle_key(self, key):
        g = self.game
        before = [row[:] for row in g.board]
        try:
            self._handle_key(key)
        finally:
            if g.board != before:
                log_key('  board changed by', repr(key), 'at', repr(self.cur))

    def _handle_key(self, key):
        g = self.game
        if self.prompt is not None:
            self.handle_prompt(key)
            return
        if key in SHIFT_MOVES:
            self.drag_move(SHIFT_MOVES[key], sticky=False)
            return
        if self.drag and self.drag['sticky']:
            if key in MOVES:
                self.drag_move(MOVES[key], sticky=True)
                return
            if key in ('v', ESC):
                self.end_drag()
                return
        self.end_drag()  # any other key ends a drag, closing its undo step
        r, c = self.cur
        if key in MOVES:
            dr, dc = MOVES[key]
            self.cur = ((r + dr) % g.n, (c + dc) % g.n)  # wraps around at the edges
        elif key == ' ':
            g.toggle_mark(r, c)
        elif key in ENTER and g.won:
            # Enter is likely still being pressed from chaining reveals: go on.
            if self.has_next:
                self.result = 'next'
        elif key in ENTER:
            if g.board[r][c] == CAT:
                # On a cat, Enter moves on to the next cell that must be a cat.
                cell = g.forced_cell()
                if cell and not g.over:  # nothing to do is silent, by design
                    self.cur = cell
                    self.reveal(*cell)
            else:
                self.reveal(r, c)
        elif key == 'v':
            self.start_drag(sticky=True)
        elif key in BATCH_SIDES:
            g.mark_side(r, c, *BATCH_SIDES[key])
        elif key == 's':
            if not g.over and not g.assist(r, c):
                self.show_toast('s 不適用：這個顏色剩下的格子不在同一行或同一列')
        elif key == CTRL_G:
            g.clear_guesses()
        elif key == ':':
            self.prompt = ''
        elif key == 'g':
            g.guess_mode = not g.guess_mode
        elif key == 'a':
            g.auto_mark = self.settings['auto_mark'] = not g.auto_mark
        elif key == 't':
            self.settings['show_coords'] = not self.settings.get('show_coords')
        elif key == 'b':
            current = self.settings.get('style', DEFAULT_STYLE)
            style = STYLES[(STYLES.index(current) + 1) % len(STYLES)]
            if style == 'blocks' and not self.colors.rich:
                style = STYLES[0]  # blocks need 256 colors; skip it
            self.settings['style'] = style
            self.show_toast(STYLE_NAMES[style])
        elif key == 'u':
            if not g.undo():
                self.show_toast('沒有可以復原的步驟')
        elif key == CTRL_R:
            if not g.redo():
                self.show_toast('沒有可以重做的步驟')
        elif key == 'n' and g.over:
            if self.has_next:
                self.result = 'next'
            else:
                self.show_toast('沒有下一關')
        elif key == 'r':
            self.leave('restart')
        elif key in ('m', ESC) and self.has_menu:
            self.leave('menu')
        elif key == '?':
            self.modal = 'help'
        elif key == 'q':
            self.modal = 'quit'

    # ── drag ─────────────────────────────────────────────────────────────────
    # A drag is one open transaction in the model, so the whole stroke undoes at once.

    def start_drag(self, sticky):
        g = self.game
        if g.over:
            return
        r, c = self.cur
        g.begin()
        self.drag = {'target': g.drag_target(r, c), 'sticky': sticky}
        g.drag_paint(r, c, self.drag['target'])

    def drag_move(self, delta, sticky):
        if self.drag is None or self.drag['sticky'] != sticky:
            self.end_drag()
            self.start_drag(sticky)
        n = self.game.n
        self.cur = ((self.cur[0] + delta[0]) % n, (self.cur[1] + delta[1]) % n)
        if self.drag:
            self.game.drag_paint(*self.cur, self.drag['target'])

    def end_drag(self):
        if self.drag:
            self.game.end()
            self.drag = None

    # ── jump ─────────────────────────────────────────────────────────────────

    def handle_prompt(self, key):
        if key in ENTER:
            text, self.prompt = self.prompt, None
            m = re.fullmatch(r'\s*([a-zA-Z])\s*(\d+)\s*', text)
            n = self.game.n
            if m and ord(m[1].upper()) - ord('A') < n and 1 <= int(m[2]) <= n:
                self.cur = (ord(m[1].upper()) - ord('A'), int(m[2]) - 1)
            elif text.strip():
                last = chr(ord('A') + n - 1)
                self.show_toast(f'座標要是 A～{last} 加上 1～{n}，例如 c5')
        elif key == ESC:
            self.prompt = None
        elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
            self.prompt = self.prompt[:-1]
        elif isinstance(key, str) and (key.isalnum() or key == ' ') and len(self.prompt) < 4:
            self.prompt += key

    def leave(self, result):
        """Leave the level, asking first if that would throw away progress."""
        if self.game.over or not self.game.undo_stack:
            self.result = result
        else:
            self.modal = result

    def reveal(self, r, c):
        g = self.game
        result = g.reveal(r, c)
        if result == 'wrong' and not g.lost:
            self.show_toast(f'翻錯了！剩 {g.hearts} 格生命')
        if result and g.over:
            self.modal = 'over'
            self.clock_stop = self.elapsed()
            self.tick = None
            if g.won and self.on_win:
                self.on_win(g.hearts, self.clock_stop)
