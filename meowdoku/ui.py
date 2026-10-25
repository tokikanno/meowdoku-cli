"""curses front end. All game rules live in model.Game; this only draws and maps keys."""

import curses
import os
import time
import unicodedata

from .model import CAT, EMPTY, GUESS, HEARTS_MAX, MARK, WRONG

# Region palette from the web game (index 0 = region A), plus the dimmed
# variants it uses for cells marked "no cat".
REGION_COLORS = [
    '#FF9676', '#B66C00', '#BCDD76', '#009865', '#41D1FF', '#946DC5',
    '#C25A6F', '#FDC35E', '#828900', '#4FEACF', '#3584CD', '#FF9FDB',
]
REGION_COLORS_DIM = [
    '#926253', '#664522', '#778659', '#275841', '#467E93', '#57466D',
    '#6D3D45', '#957A50', '#4E5223', '#4F8C7F', '#2F5071', '#946883',
]

SYMBOLS = {EMPTY: '', MARK: '×', GUESS: '△', CAT: '🐱', WRONG: '╳'}
SYMBOLS_ASCII = {EMPTY: '', MARK: 'x', GUESS: '?', CAT: '@', WRONG: '!'}

# (cell width, cell height), largest first; the first that fits the terminal wins.
CELL_SIZES = [(6, 3), (4, 2), (4, 1)]
LABEL_W = 3  # room for the row letters left of the board

TOAST_SECONDS = 1.0

CTRL_R, CTRL_X, ESC = '\x12', '\x18', '\x1b'
MOVES = {
    curses.KEY_UP: (-1, 0), curses.KEY_DOWN: (1, 0),
    curses.KEY_LEFT: (0, -1), curses.KEY_RIGHT: (0, 1),
    'k': (-1, 0), 'j': (1, 0), 'h': (0, -1), 'l': (0, 1),
}

HELP = [
    ('方向鍵 / hjkl', '移動'),
    ('Space', '標記／取消標記（依模式寫入 {X} 或 {G}）'),
    ('g', '切換標記模式：無貓 {X} ↔ 猜測 {G}'),
    ('Enter', '翻開，翻錯扣一格生命'),
    ('a', '切換翻開後自動標記'),
    ('t', '切換座標提示'),
    ('u / Ctrl-R', 'undo / redo'),
    ('n / r', '下一關 / 重玩（結束後）'),
    ('q', '離開（會先確認）'),
    ('Ctrl-X', '直接離開'),
]


def text_width(s):
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in s)


def clip(s, width):
    out, w = [], 0
    for ch in s:
        cw = text_width(ch)
        if w + cw > width:
            break
        out.append(ch)
        w += cw
    return ''.join(out)


def hex_to_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def xterm256(rgb):
    """Nearest xterm-256 index from the 6×6×6 cube or the gray ramp."""
    levels = [0, 95, 135, 175, 215, 255]

    def nearest_level(v):
        return min(range(6), key=lambda i: abs(levels[i] - v))

    def dist(a, b):
        return sum((x - y) ** 2 for x, y in zip(a, b))

    idx = [nearest_level(v) for v in rgb]
    cube = tuple(levels[i] for i in idx)
    best = (dist(rgb, cube), 16 + 36 * idx[0] + 6 * idx[1] + idx[2])
    for g in range(24):
        v = 8 + 10 * g
        best = min(best, (dist(rgb, (v, v, v)), 232 + g))
    return best[1]


def is_light(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b > 140


class App:
    def __init__(self, stdscr, game, title, next_level=None, restart=None, ascii=False):
        """`next_level` / `restart` return a fresh (Game, title), or None if unavailable."""
        self.scr = stdscr
        self.game = game
        self.title = title
        self.next_level = next_level
        self.restart = restart
        self.symbols = SYMBOLS_ASCII if ascii else SYMBOLS
        self.hearts = ('*', '.') if ascii else ('♥', '♡')
        self.cur = (0, 0)
        self.show_coords = False
        self.toast = None  # (text, expires_at)
        self.modal = None  # None | 'help' | 'quit' | 'over'
        self.running = True
        self._init_colors()

    # ── colors ───────────────────────────────────────────────────────────────

    def _init_colors(self):
        self.rich = False
        self.pairs = {}
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        if curses.COLORS >= 256 and curses.COLOR_PAIRS >= 2 * len(REGION_COLORS) + 1:
            self.rich = True
            for dim, palette in enumerate((REGION_COLORS, REGION_COLORS_DIM)):
                for i, hx in enumerate(palette):
                    rgb = hex_to_rgb(hx)
                    fg = 16 if is_light(rgb) else 231
                    pid = 1 + dim * len(palette) + i
                    curses.init_pair(pid, fg, xterm256(rgb))
                    self.pairs[(i, bool(dim))] = curses.color_pair(pid)
        else:
            # 8/16 colors can't tell 12 regions apart, so cells also show their letter.
            basic = [curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_YELLOW,
                     curses.COLOR_BLUE, curses.COLOR_MAGENTA, curses.COLOR_CYAN]
            for i, bg in enumerate(basic):
                curses.init_pair(1 + i, curses.COLOR_BLACK, bg)
            for i in range(len(REGION_COLORS)):
                attr = curses.color_pair(1 + i % len(basic))
                self.pairs[(i, False)] = attr
                self.pairs[(i, True)] = attr | curses.A_DIM

    def cell_attr(self, r, c):
        st = self.game.board[r][c]
        reg = self.game.region(r, c) % len(REGION_COLORS)
        return self.pairs.get((reg, st == MARK), curses.A_REVERSE if st == MARK else 0)

    # ── drawing ──────────────────────────────────────────────────────────────

    def put(self, y, x, s, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        try:
            self.scr.addstr(y, x, clip(s, w - x), attr)
        except curses.error:
            pass  # writing the bottom-right cell always "fails" after drawing

    def layout(self):
        h, w = self.scr.getmaxyx()
        n = self.game.n
        for cw, ch in CELL_SIZES:
            bw, bh = LABEL_W + n * cw, 2 + n * ch + 3
            if bw <= w and bh <= h:
                return cw, ch, max(0, (w - bw) // 2), max(0, (h - bh) // 2)
        return None

    def draw(self):
        self.scr.erase()
        lay = self.layout()
        if lay is None:
            need = LABEL_W + self.game.n * CELL_SIZES[-1][0]
            self.put(0, 0, f'終端機太小，至少需要 {need}×{self.game.n * CELL_SIZES[-1][1] + 5}')
            self.scr.refresh()
            return
        cw, ch, x0, y0 = lay
        bx, by = x0 + LABEL_W, y0 + 2
        self.draw_header(y0, x0)
        if self.show_coords:
            self.draw_coords(bx, by, cw, ch)
        for r in range(self.game.n):
            for c in range(self.game.n):
                self.draw_cell(r, c, bx + c * cw, by + r * ch, cw, ch)
        self.draw_footer(by + self.game.n * ch + 1, x0)
        if self.modal == 'help':
            marks = {'X': self.symbols[MARK], 'G': self.symbols[GUESS]}
            rows = [k + ' ' * (16 - text_width(k)) + v.format(**marks) for k, v in HELP]
            self.draw_box(['操作說明', ''] + rows + ['', '按任意鍵關閉'], center=False)
        elif self.modal == 'quit':
            self.draw_box(['離開？(y/n)'])
        elif self.modal == 'over':
            self.draw_box(self.over_lines())
        elif self.toast:
            self.draw_box([self.toast[0]])
        self.scr.refresh()

    def draw_header(self, y, x):
        g = self.game
        hearts = self.hearts[0] * g.hearts + self.hearts[1] * (HEARTS_MAX - g.hearts)
        mode = f'猜測 {self.symbols[GUESS]}' if g.guess_mode else f'無貓 {self.symbols[MARK]}'
        self.put(y, x, f'{self.title}  {hearts}  ', curses.A_BOLD)
        x += text_width(f'{self.title}  {hearts}  ')
        self.put(y, x, f' {mode} ', curses.A_REVERSE if g.guess_mode else curses.A_BOLD)
        x += text_width(f' {mode} ')
        self.put(y, x, f'  自動標記:{"開" if g.auto_mark else "關"}')

    def draw_coords(self, bx, by, cw, ch):
        for c in range(self.game.n):
            label = str(c + 1)
            self.put(by - 1, bx + c * cw + (cw - len(label)) // 2, label, curses.A_DIM)
        for r in range(self.game.n):
            self.put(by + r * ch + (ch - 1) // 2, bx - LABEL_W, chr(ord('A') + r), curses.A_DIM)

    def draw_cell(self, r, c, x, y, cw, ch):
        g = self.game
        st = g.board[r][c]
        attr = self.cell_attr(r, c)
        for dy in range(ch):
            self.put(y + dy, x, ' ' * cw, attr)
        sym = self.symbols[st]
        if not sym and not self.rich:
            sym = chr(ord('a') + g.region(r, c))
        mid = (ch - 1) // 2
        if sym:
            sx = x + (cw - text_width(sym)) // 2
            self.put(y + mid, sx, sym, attr | (curses.A_BOLD if st in (CAT, WRONG) else 0))
        if (r, c) == self.cur:
            bold = attr | curses.A_BOLD
            if ch >= 3:
                self.put(y, x, '┌', bold)
                self.put(y, x + cw - 1, '┐', bold)
                self.put(y + ch - 1, x, '└', bold)
                self.put(y + ch - 1, x + cw - 1, '┘', bold)
            else:
                for dy in range(ch):
                    self.put(y + dy, x, '[', bold)
                    self.put(y + dy, x + cw - 1, ']', bold)

    def draw_footer(self, y, x):
        g = self.game
        if g.won:
            self.put(y, x, '過關！  n 下一關 · r 重玩 · q 離開', curses.A_BOLD)
        elif g.lost:
            self.put(y, x, '生命用完了  r 重玩 · n 下一關 · q 離開', curses.A_BOLD)
        else:
            self.put(y, x, f'貓 {g.cats}/{g.n}')
        self.put(y + 1, x, 'Space 標記  Enter 翻開  g 模式  u/^R 復原/重做  ? 說明  ^X 離開',
                 curses.A_DIM)

    def over_lines(self):
        g = self.game
        if g.won:
            return ['過關！' + '★' * g.hearts + '☆' * (HEARTS_MAX - g.hearts), '',
                    'n 下一關 · r 重玩 · q 離開', '其他鍵：關閉這個視窗']
        return ['生命用完了', '', 'r 重玩 · n 下一關 · q 離開', '其他鍵：關閉這個視窗']

    def draw_box(self, lines, center=True):
        h, w = self.scr.getmaxyx()
        inner = max(text_width(s) for s in lines) + 4
        bw, bh = min(inner + 2, w), len(lines) + 2
        x, y = max(0, (w - bw) // 2), max(0, (h - bh) // 2)
        attr = curses.A_REVERSE
        self.put(y, x, '┌' + '─' * (bw - 2) + '┐', attr)
        for i, s in enumerate(lines):
            pad = bw - 2 - text_width(s)
            left = pad // 2 if center else 2
            self.put(y + 1 + i, x, '│' + ' ' * left + s + ' ' * (pad - left) + '│', attr)
        self.put(y + bh - 1, x, '└' + '─' * (bw - 2) + '┘', attr)

    # ── input ────────────────────────────────────────────────────────────────

    def show_toast(self, text):
        self.toast = (text, time.monotonic() + TOAST_SECONDS)

    def read_key(self):
        """A key (str or KEY_* int), or None when a pending toast should expire."""
        if self.toast:
            left = self.toast[1] - time.monotonic()
            if left <= 0:
                self.toast = None
                return None
            self.scr.timeout(max(1, int(left * 1000)))
        else:
            self.scr.timeout(-1)
        try:
            return self.scr.get_wch()
        except curses.error:
            return None

    def run(self):
        curses.curs_set(0)
        while self.running:
            self.draw()
            key = self.read_key()
            if key is None:
                continue
            if key == curses.KEY_RESIZE:
                curses.update_lines_cols()
                continue
            if key == CTRL_X:
                return
            self.toast = None
            if self.modal:
                self.handle_modal(key)
            else:
                self.handle_key(key)

    def handle_modal(self, key):
        modal, self.modal = self.modal, None
        if modal == 'quit' and key in ('y', 'Y'):
            self.running = False
        elif modal == 'over' and key in ('n', 'r', 'q'):
            self.handle_key(key)

    def handle_key(self, key):
        g = self.game
        r, c = self.cur
        if key in MOVES:
            dr, dc = MOVES[key]
            if g.in_bounds(r + dr, c + dc):
                self.cur = (r + dr, c + dc)
        elif key == ' ':
            g.toggle_mark(r, c)
        elif key in ('\n', '\r', curses.KEY_ENTER):
            result = g.reveal(r, c)
            if result == 'wrong' and not g.lost:
                self.show_toast(f'翻錯了！剩 {g.hearts} 格生命')
            if result and g.over:
                self.modal = 'over'
        elif key == 'g':
            g.guess_mode = not g.guess_mode
        elif key == 'a':
            g.auto_mark = not g.auto_mark
        elif key == 't':
            self.show_coords = not self.show_coords
        elif key == 'u':
            if not g.undo():
                self.show_toast('沒有可以復原的步驟')
        elif key == CTRL_R:
            if not g.redo():
                self.show_toast('沒有可以重做的步驟')
        elif key == 'n' and g.over:
            self.load(self.next_level, '已經是最後一關')
        elif key == 'r' and g.over:
            self.load(self.restart, '無法重玩')
        elif key == '?':
            self.modal = 'help'
        elif key == 'q':
            self.modal = 'quit'

    def load(self, loader, failure):
        loaded = loader() if loader else None
        if loaded is None:
            self.show_toast(failure)
            return
        old = self.game
        self.game, self.title = loaded
        # Settings carry over between levels; the board does not.
        self.game.auto_mark, self.game.guess_mode = old.auto_mark, False
        self.cur = (0, 0)


def run(game, title, **kwargs):
    os.environ.setdefault('ESCDELAY', '25')
    curses.wrapper(lambda scr: App(scr, game, title, **kwargs).run())

