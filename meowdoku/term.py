"""Shared curses helpers: text width, colors, box drawing, toasts, key reading."""

import curses
import os
import time
import unicodedata

# Region palette from the web game (index 0 = region A).
REGION_COLORS = [
    '#FF9676', '#B66C00', '#BCDD76', '#009865', '#41D1FF', '#946DC5',
    '#C25A6F', '#FDC35E', '#828900', '#4FEACF', '#3584CD', '#FF9FDB',
]
# The web game's dimmed variants, for cells marked "no cat".
REGION_COLORS_DIM = [
    '#926253', '#664522', '#778659', '#275841', '#467E93', '#57466D',
    '#6D3D45', '#957A50', '#4E5223', '#4F8C7F', '#2F5071', '#946883',
]

TOAST_SECONDS = 1.0
KEYLOG = os.environ.get('MEOWDOKU_KEYLOG')  # debugging: append every key read to this file


def log_key(*parts):
    if KEYLOG:
        with open(KEYLOG, 'a', encoding='utf-8') as f:
            f.write(f'{time.monotonic():.3f} ' + ' '.join(parts) + '\n')
CTRL_G, CTRL_R, CTRL_X, ESC = '\x07', '\x12', '\x18', '\x1b'
ENTER = ('\n', '\r', curses.KEY_ENTER)
SHIFT_MOVES = {
    curses.KEY_SR: (-1, 0), curses.KEY_SF: (1, 0),
    curses.KEY_SLEFT: (0, -1), curses.KEY_SRIGHT: (0, 1),
}
# Shift+arrow as xterm sends it, for terminals whose terminfo entry lacks these keys.
SHIFT_ARROW_SEQS = {'[1;2A': curses.KEY_SR, '[1;2B': curses.KEY_SF,
                    '[1;2C': curses.KEY_SRIGHT, '[1;2D': curses.KEY_SLEFT}
MOVES = {
    curses.KEY_UP: (-1, 0), curses.KEY_DOWN: (1, 0),
    curses.KEY_LEFT: (0, -1), curses.KEY_RIGHT: (0, 1),
}


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


def fmt_time(seconds):
    s = int(seconds)
    return f'{s // 3600}:{s // 60 % 60:02}:{s % 60:02}' if s >= 3600 else f'{s // 60:02}:{s % 60:02}'


def hex_to_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _lab(rgb):
    """sRGB → CIELAB, so "nearest color" means nearest to the eye."""
    def linear(v):
        v /= 255
        return ((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    r, g, b = (linear(v) for v in rgb)
    x = f((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047)
    y = f(0.2126 * r + 0.7152 * g + 0.0722 * b)
    z = f((0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883)
    return 116 * y - 16, 500 * (x - y), 200 * (y - z)


def _xterm_rgb(i):
    if i >= 232:
        v = 8 + 10 * (i - 232)
        return v, v, v
    levels = [0, 95, 135, 175, 215, 255]
    i -= 16
    return levels[i // 36], levels[i // 6 % 6], levels[i % 6]


_XTERM_LAB = {i: _lab(_xterm_rgb(i)) for i in range(16, 256)}


def xterm256(rgb):
    """Nearest xterm-256 index (16..255) in CIELAB. Plain RGB distance turns the
    dimmed region colors into grays, which loses their link to the region."""
    target = _lab(rgb)
    return min(_XTERM_LAB, key=lambda i: sum((a - b) ** 2 for a, b in zip(target, _XTERM_LAB[i])))


def assign_slots(rgbs, reserved=()):
    """A distinct xterm-256 slot per color, each the nearest one still free, so two
    similar colors never collapse into one. Colors are placed hardest-first: the one
    whose nearest slot is farthest away picks before the easy ones take its options."""
    labs = [_lab(rgb) for rgb in rgbs]

    def dist(lab, i):
        return sum((a - b) ** 2 for a, b in zip(lab, _XTERM_LAB[i]))

    free = set(_XTERM_LAB) - set(reserved)
    order = sorted(range(len(rgbs)), key=lambda k: -min(dist(labs[k], i) for i in free))
    slots = [None] * len(rgbs)
    for k in order:
        slots[k] = min(free, key=lambda i: dist(labs[k], i))
        free.discard(slots[k])
    return slots


def is_light(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b > 140


# Python's curses packs the pair number into 8 attribute bits: pair 256 and up
# silently wraps onto other pairs (wrong colors, flicker). Stay below it.
MAX_PAIRS = 256


class PairPool:
    """Pair numbers [first, limit) handed out per (fg, bg). When they run out, a
    number not used in the current frame is reused: one frame never needs more
    pairs than it has, but a whole game's worth of combinations would."""

    def __init__(self, first, limit, init_pair):
        self.free = list(range(first, limit))
        self.init_pair = init_pair
        self.by_colors = {}  # (fg, bg) -> pair number
        self.last_used = {}  # pair number -> frame
        self.frame = 0

    def new_frame(self):
        self.frame += 1

    def get(self, fg, bg):
        """A pair number for these colors, or 0 if this frame has used them all."""
        pid = self.by_colors.get((fg, bg))
        if pid is None:
            if self.free:
                pid = self.free.pop(0)
            else:
                pid = min(self.last_used, key=self.last_used.get, default=None)
                if pid is None or self.last_used[pid] == self.frame:
                    return 0
                stale = next(k for k, v in self.by_colors.items() if v == pid)
                del self.by_colors[stale]
            self.init_pair(pid, fg, bg)
            self.by_colors[fg, bg] = pid
        self.last_used[pid] = self.frame
        return pid


class Colors:
    """Color pairs, set up once per curses session."""

    def __init__(self):
        self.rich = False  # 256 colors: regions are told apart by color alone
        self.regions = []
        self.dim_regions = []
        self.cursor = curses.A_REVERSE
        self.stars = {1: 0, 2: 0, 3: 0}
        self.slots = {}  # (region, dim) -> color number, for pairs built on demand
        self.default_bg = -1
        self.cursor_fg = None
        self.pool = None  # PairPool for pairs built on demand, once colors are set up
        if not curses.has_colors():
            self.regions = [0] * len(REGION_COLORS)
            self.dim_regions = [curses.A_DIM] * len(REGION_COLORS)
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            default_bg = -1
        except curses.error:
            default_bg = curses.COLOR_BLACK
        self.default_bg = default_bg
        pid = 1
        if curses.COLORS >= 256 and curses.COLOR_PAIRS >= 2 * len(REGION_COLORS) + 8:
            self.rich = True
            extra = {'cursor': 226, 1: 203, 2: 221, 3: 114}
            rgbs = [hex_to_rgb(hx) for hx in REGION_COLORS + REGION_COLORS_DIM]
            slots = assign_slots(rgbs, reserved=[16, 231, *extra.values()])
            # Where the terminal lets us, repaint those slots with the exact colors;
            # ncurses restores the palette on exit. Elsewhere the nearest slots stand.
            exact = curses.can_change_color() and os.environ.get('MEOWDOKU_PALETTE') != '0'
            for i, (rgb, slot) in enumerate(zip(rgbs, slots)):
                if exact:
                    try:
                        # ncurses scales 0..1000 and truncates on the way back; round up to land exact.
                        curses.init_color(slot, *(-(-v * 1000 // 255) for v in rgb))
                    except curses.error:
                        exact = False
                curses.init_pair(pid, 16 if is_light(rgb) else 231, slot)
                self.slots[i % len(REGION_COLORS), i >= len(REGION_COLORS)] = slot
                attrs = self.regions if i < len(REGION_COLORS) else self.dim_regions
                attrs.append(curses.color_pair(pid))
                pid += 1
        else:
            # 8/16 colors can't tell 12 regions apart, so cells also show their letter.
            basic = [curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_YELLOW,
                     curses.COLOR_BLUE, curses.COLOR_MAGENTA, curses.COLOR_CYAN]
            for bg in basic:
                curses.init_pair(pid, curses.COLOR_BLACK, bg)
                pid += 1
            self.regions = [curses.color_pair(1 + i % len(basic)) for i in range(len(REGION_COLORS))]
            self.dim_regions = [attr | curses.A_DIM for attr in self.regions]
            extra = {'cursor': curses.COLOR_YELLOW, 1: curses.COLOR_RED,
                     2: curses.COLOR_YELLOW, 3: curses.COLOR_GREEN}
        for name, fg in extra.items():
            curses.init_pair(pid, fg, default_bg)
            attr = curses.color_pair(pid) | curses.A_BOLD
            if name == 'cursor':
                self.cursor = attr
                self.cursor_fg = fg
            else:
                self.stars[name] = attr
            pid += 1

        self.pool = PairPool(pid, min(curses.COLOR_PAIRS, MAX_PAIRS), curses.init_pair)

    def slot(self, reg, dim=False):
        return self.slots[reg % len(REGION_COLORS), dim]

    def new_frame(self):
        if self.pool:
            self.pool.new_frame()

    def pair(self, fg, bg):
        """Attribute for any fg/bg color pair, set up the first time it's asked for."""
        if not self.pool:
            return 0
        pid = self.pool.get(fg, bg)
        return curses.color_pair(pid) if pid else 0

    def region(self, reg, dim=False):
        attrs = self.dim_regions if dim else self.regions
        return attrs[reg % len(attrs)]


class Screen:
    """Base for one full-screen view: drawing helpers, centered boxes, toasts."""

    def __init__(self, scr, colors):
        self.scr = scr
        self.colors = colors
        self.toast = None  # (text, expires_at)
        self.tick = None  # seconds between redraws while idle (e.g. a running clock)

    def put(self, y, x, s, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        try:
            self.scr.addstr(y, x, clip(s, w - x), attr)
        except curses.error:
            pass  # writing the bottom-right cell always "fails" after drawing

    def draw_box(self, lines, center=True):
        h, w = self.scr.getmaxyx()
        bw = min(max(text_width(s) for s in lines) + 6, w)
        bh = len(lines) + 2
        x, y = max(0, (w - bw) // 2), max(0, (h - bh) // 2)
        attr = curses.A_REVERSE
        self.put(y, x, '┌' + '─' * (bw - 2) + '┐', attr)
        for i, s in enumerate(lines):
            pad = bw - 2 - text_width(s)
            left = pad // 2 if center else 2
            self.put(y + 1 + i, x, '│' + ' ' * left + s + ' ' * (pad - left) + '│', attr)
        self.put(y + bh - 1, x, '└' + '─' * (bw - 2) + '┘', attr)

    def show_toast(self, text, seconds=TOAST_SECONDS):
        self.toast = (text, time.monotonic() + seconds)

    def busy(self, text):
        """Draw a box right away, for work that blocks (e.g. a download)."""
        self.draw()
        self.draw_box([text])
        self.scr.refresh()

    def read_key(self):
        """A key (str or KEY_* int), or None when a pending toast should expire."""
        if self.toast:
            left = self.toast[1] - time.monotonic()
            if left <= 0:
                self.toast = None
                return None
            self.scr.timeout(max(1, int(left * 1000)))
        elif self.tick:
            self.scr.timeout(int(self.tick * 1000))
        else:
            self.scr.timeout(-1)
        try:
            key = self.scr.get_wch()
        except curses.error:
            return None
        if KEYLOG:
            name = curses.keyname(key).decode() if isinstance(key, int) else ''
            log_key('key', repr(key), name)
        if key == curses.KEY_RESIZE:
            curses.update_lines_cols()
            return None
        if key == ESC:
            key = self.read_escape()
            if key is None:
                return None
        self.toast = None
        return key

    def read_escape(self):
        """After a bare ESC: ESC itself, a Shift+arrow key, or None for a sequence we ignore.
        curses already decodes the sequences it knows (arrows etc.), so what's left is
        either a real Esc press or a sequence terminfo doesn't describe."""
        self.scr.timeout(0)
        seq = ''
        try:
            while True:
                ch = self.scr.get_wch()
                if not isinstance(ch, str):
                    break
                seq += ch
                # A CSI sequence ends at its final byte; anything else is one char.
                if not seq.startswith('[') or (len(seq) > 1 and '@' <= ch <= '~'):
                    break
        except curses.error:
            pass  # nothing more queued
        log_key('  after ESC:', repr(seq))
        if not seq:
            return ESC
        return SHIFT_ARROW_SEQS.get(seq)

    def draw(self):
        raise NotImplementedError
