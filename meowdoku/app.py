"""Session loop: menus → game → menus, all inside one curses session."""

import curses
import os
import random

from .game_view import GameView
from .generator import format_level, generate_from_seed, new_seed, seed_code
from .levels import FAV, Favorites, LevelError, parse_level
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
        self.favorites = store.favorites if store else Favorites()
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
            if not self.store.count(pack):
                return 'back'  # the last favorite was just removed
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
            later = [i for i in self.store.ids(pack) if i > idx]
            result = self.play_one(pack, idx, f'{pack_label(pack)} #{idx}', has_next=bool(later))
            if result == 'next':
                # Re-read: a favorite removed during the level is no longer there.
                later = [i for i in self.store.ids(pack) if i > idx]
                if not later:
                    return 'menu'
                idx = later[0]
            elif result != 'restart':
                return result

    def play_random(self, pack):
        """Random unsolved levels from `pack`."""
        idx = None
        while True:
            if idx is None:
                ids = self.store.ids(pack)
                if not ids:
                    return 'menu'
                idx = random.choice(self.progress.unsolved(pack, ids) or ids)
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
        notes = [] if pack == FAV else [f'source: {pack_label(pack)} #{idx}']

        def on_win(stars, secs):
            if self.exists(pack, idx):
                self.progress.record(pack, idx, stars, secs)
        return self.run_level(puzzle, pack, idx, title, has_next, on_win=on_win,
                              best_time=self.progress.best_time(pack, idx),
                              on_favorite=self.favorite(puzzle, notes))

    def play_generated(self, n, seed=None):
        """Freshly generated n×n levels; an unfinished one is resumed first, unless
        `seed` asks for a particular board."""
        saved = self.progress.get_save('gen', n)
        puzzle = code = None
        if saved and (seed is None or saved.get('seed') == seed_code(n, seed)):
            try:
                puzzle = parse_level(saved['level'])
                code = saved.get('seed')
            except (LevelError, KeyError, TypeError):
                self.progress.drop_save('gen', n)
        elif saved:
            self.progress.drop_save('gen', n)
        while True:
            if puzzle is None:
                seed = new_seed() if seed is None else seed
                code = seed_code(n, seed)
                self.blank.busy(f'產生 {n}×{n} 關卡中…（{code}）')
                puzzle = generate_from_seed(n, seed)
                seed = None  # the next level gets a fresh seed
            title = f'隨機出題 {code}' if code else f'隨機出題 {n}×{n}'
            notes = [f'seed: {code}'] if code else []
            result = self.run_level(puzzle, 'gen', n, title, has_next=True,
                                    extra={'level': format_level(puzzle), 'seed': code},
                                    on_favorite=self.favorite(puzzle, notes))
            if result == 'next':
                puzzle = None
            elif result != 'restart':
                return result

    def favorite(self, puzzle, notes=()):
        """The `f` key's action for `puzzle`: add it to the favorites, or remove it
        (with its stars, time and save) if it's there already."""
        def toggle():
            favorites = self.favorites
            found = favorites.find(puzzle)
            try:
                if found:
                    favorites.remove(found)
                    if self.progress:
                        self.progress.forget(FAV, found)
                    return f'已從收藏移除（第 {found} 關），再按 f 可以加回來'
                idx, path = favorites.add(format_level(puzzle, notes))
            except OSError as e:
                return f'收藏失敗：{e}'
            return f'已收藏為第 {idx} 關（{path.parent.name}/{path.name}）'
        return toggle

    def exists(self, pack, idx):
        """False for a favorite removed while it was being played."""
        return pack != FAV or idx in self.favorites.ids()

    def run_level(self, puzzle, pack, idx, title, has_next, on_win=None, best_time=None, extra=None,
                  on_favorite=None):
        """Play one puzzle, resuming and saving it under (pack, idx)."""
        self.progress.set_last(pack, idx)
        saved = self.progress.get_save(pack, idx)
        game = Game.from_dict(puzzle, saved) if saved else None
        elapsed = float(saved.get('elapsed', 0)) if game else 0.0
        view = GameView(self.scr, self.colors, game or Game(puzzle), title, self.settings,
                        on_win=on_win, has_next=has_next, ascii=self.ascii, elapsed=elapsed,
                        best_time=best_time, resumed=game is not None, on_favorite=on_favorite)
        result = view.run()
        # Keep an unfinished level for next time; a restart or an ending clears it.
        if not self.exists(pack, idx):
            pass  # removed from the favorites while playing: nothing to keep
        elif view.game.over or result == 'restart' or not view.game.undo_stack:
            self.progress.drop_save(pack, idx)
        else:
            state = dict(view.game.to_dict(), elapsed=view.elapsed(), **(extra or {}))
            self.progress.put_save(pack, idx, state)
        return result

    def play_file(self, puzzle, title):
        on_favorite = self.favorite(puzzle, [f'source: {title}'])
        while True:
            view = GameView(self.scr, self.colors, Game(puzzle), title, self.settings,
                            has_menu=False, has_next=False, ascii=self.ascii,
                            on_favorite=on_favorite)
            if view.run() != 'restart':
                return

    def error(self, text):
        self.blank.draw()
        self.blank.draw_box([text, '', '按任意鍵繼續'])
        self.scr.refresh()
        self.scr.timeout(-1)
        self.scr.get_wch()


def run(start, store=None, progress=None, ascii=False):
    """start: ('menu',), ('pack', pack), ('play', pack, idx), ('generate', n, seed)
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
            if app.play_generated(*start[1:]) == 'quit':
                return
        else:
            initial = progress.last.get('pack')
        app.main_menu(initial)

    curses.wrapper(main)
