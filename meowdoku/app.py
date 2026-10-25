"""Session loop: menus → game → menus, all inside one curses session."""

import curses
import os
import random

from .game_view import GameView
from .generator import format_level, generate
from .levels import LevelError, parse_level
from .menu import LevelMenu, MainMenu, pack_label
from .model import Game
from .term import Colors, Screen


class _Blank(Screen):
    def draw(self):
        self.scr.erase()


class App:
    def __init__(self, scr, store, progress, ascii=False):
        self.scr = scr
        self.store = store
        self.progress = progress
        self.ascii = ascii
        self.colors = Colors()
        self.settings = {'auto_mark': True, 'show_coords': False}
        self.gen_size = 8
        self.blank = _Blank(scr, self.colors)

    # ── screens ──────────────────────────────────────────────────────────────

    def main_menu(self, initial=None):
        while True:
            menu = MainMenu(self.scr, self.colors, self.store, self.progress, initial, self.gen_size)
            choice = menu.run()
            self.gen_size = menu.gen_size
            if choice == 'quit':
                return
            kind, pack = choice
            if kind == 'continue':
                pack, idx = pack
                initial = 'generate' if pack == 'gen' else pack
                play = self.play_generated if pack == 'gen' else self.play_pack
                if play(*([idx] if pack == 'gen' else [pack, idx])) == 'quit':
                    return
                continue
            if kind == 'generate':
                initial = 'generate'
                if self.play_generated(pack) == 'quit':
                    return
                continue
            initial = pack
            if kind == 'random':
                result = self.play_random(pack)
            else:
                result = self.level_menu(pack)
            if result == 'quit':
                return

    def level_menu(self, pack):
        while True:
            choice = LevelMenu(self.scr, self.colors, self.store, self.progress, pack).run()
            if choice in ('back', 'quit'):
                return choice
            if choice == 'random':
                result = self.play_random(pack)
            else:
                result = self.play_pack(pack, choice[1])
            if result == 'quit':
                return 'quit'

    # ── playing ──────────────────────────────────────────────────────────────

    def load(self, pack, idx):
        self.blank.busy(f'載入 {pack_label(pack)} 第 {idx} 關…')
        return self.store.load(pack, idx)

    def play_pack(self, pack, idx):
        """Play levels of `pack` in order from `idx`. Returns 'menu' or 'quit'."""
        while True:
            result = self.play_one(pack, idx, f'{pack_label(pack)} #{idx}',
                                   has_next=idx < self.store.count(pack))
            if result == 'next':
                idx += 1
            elif result != 'restart':
                return result

    def play_random(self, pack):
        """Random unsolved levels from `pack`."""
        idx = None
        while True:
            if idx is None:
                count = self.store.count(pack)
                idx = random.choice(self.progress.unsolved(pack, count) or range(1, count + 1))
            result = self.play_one(pack, idx, f'隨機 · {pack_label(pack)} #{idx}', has_next=True)
            if result == 'next':
                idx = None
            elif result != 'restart':
                return result

    def play_one(self, pack, idx, title, has_next):
        try:
            puzzle = self.load(pack, idx)
        except LevelError as e:
            self.error(str(e))
            return 'menu'
        return self.run_level(puzzle, pack, idx, title, has_next,
                              on_win=lambda stars, secs: self.progress.record(pack, idx, stars, secs),
                              best_time=self.progress.best_time(pack, idx))

    def play_generated(self, n):
        """Freshly generated n×n levels; an unfinished one is resumed first."""
        saved = self.progress.get_save('gen', n)
        puzzle = None
        if saved:
            try:
                puzzle = parse_level(saved['level'])
            except (LevelError, KeyError, TypeError):
                self.progress.drop_save('gen', n)
        while True:
            if puzzle is None:
                self.blank.busy(f'產生 {n}×{n} 關卡中…')
                puzzle = generate(n)
            result = self.run_level(puzzle, 'gen', n, f'隨機出題 {n}×{n}', has_next=True,
                                    extra={'level': format_level(puzzle)})
            if result == 'next':
                puzzle = None
            elif result != 'restart':
                return result

    def run_level(self, puzzle, pack, idx, title, has_next, on_win=None, best_time=None, extra=None):
        """Play one puzzle, resuming and saving it under (pack, idx)."""
        self.progress.set_last(pack, idx)
        saved = self.progress.get_save(pack, idx)
        game = Game.from_dict(puzzle, saved) if saved else None
        elapsed = float(saved.get('elapsed', 0)) if game else 0.0
        view = GameView(self.scr, self.colors, game or Game(puzzle), title, self.settings,
                        on_win=on_win, has_next=has_next, ascii=self.ascii, elapsed=elapsed,
                        best_time=best_time, resumed=game is not None)
        result = view.run()
        # Keep an unfinished level for next time; a restart or an ending clears it.
        if view.game.over or result == 'restart' or not view.game.undo_stack:
            self.progress.drop_save(pack, idx)
        else:
            state = dict(view.game.to_dict(), elapsed=view.elapsed(), **(extra or {}))
            self.progress.put_save(pack, idx, state)
        return result

    def play_file(self, puzzle, title):
        while True:
            view = GameView(self.scr, self.colors, Game(puzzle), title, self.settings,
                            has_menu=False, has_next=False, ascii=self.ascii)
            if view.run() != 'restart':
                return

    def error(self, text):
        self.blank.draw()
        self.blank.draw_box([text, '', '按任意鍵繼續'])
        self.scr.refresh()
        self.scr.timeout(-1)
        self.scr.get_wch()


def run(start, store=None, progress=None, ascii=False):
    """start: ('menu',), ('pack', pack), ('play', pack, idx), ('generate', n)
    or ('file', puzzle, title)."""
    os.environ.setdefault('ESCDELAY', '25')

    def main(scr):
        curses.curs_set(0)
        app = App(scr, store, progress, ascii)
        kind = start[0]
        if kind == 'file':
            app.play_file(*start[1:])
            return
        initial = None
        if kind == 'pack':
            initial = start[1]
            if app.level_menu(start[1]) == 'quit':
                return
        elif kind == 'play':
            initial = start[1]
            if app.play_pack(start[1], start[2]) == 'quit':
                return
        elif kind == 'generate':
            initial = 'generate'
            app.gen_size = start[1]
            if app.play_generated(start[1]) == 'quit':
                return
        else:
            initial = progress.last.get('pack')
        app.main_menu(initial)

    curses.wrapper(main)
