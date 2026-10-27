"""Level parsing and level-bundle loading (download, cache, sha256 check)."""

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from .model import Puzzle
from .solver import solutions

BUNDLE_URL = 'https://raw.githubusercontent.com/plko/meowdoku/main/levels/bundle/'
PACK_ORDER = ['6', '7', '8', '9', '10', '11', '12', 'hard', 'bad']
FAV = 'fav'  # the favorites, played like a pack


class LevelError(Exception):
    pass


def parse_level(text):
    """Parse the `level_*.txt` format; solve the board if it has no solution line."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    try:
        n = int(lines[0])
    except (IndexError, ValueError):
        raise LevelError('第一行必須是盤面大小 n')
    rows = lines[1:1 + n]
    if len(rows) != n or any(len(row) != n for row in rows):
        raise LevelError(f'盤面必須是 {n}×{n}')
    letters = sorted({ch for row in rows for ch in row})
    if len(letters) != n:
        raise LevelError(f'需要 {n} 個顏色區塊，實際有 {len(letters)} 個')
    # Region ids follow the letter (A = 0), as in the web game's palette.
    regions = tuple(tuple(ord(ch.upper()) - ord('A') for ch in row) for row in rows)
    if any(not 0 <= reg < n for row in regions for reg in row):
        regions = tuple(tuple(letters.index(ch) for ch in row) for row in rows)

    solution = None
    for ln in lines[1 + n:]:
        if ln.startswith('#') and 'solution:' in ln:
            solution = tuple(int(x) for x in ln.split('solution:', 1)[1].split())
    if solution is None:
        found = solutions(regions, limit=1)
        if not found:
            raise LevelError('這個盤面無解')
        solution = found[0]
    if len(solution) != n:
        raise LevelError('solution 長度不符')
    return Puzzle(n, regions, solution)


def cache_dir():
    base = os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache'
    return Path(base) / 'meowdoku'


def _download(url):
    with urllib.request.urlopen(url, timeout=20) as resp:
        return resp.read()


class LevelStore:
    """Packs from a bundle directory: a local one, or the download cache; plus the
    favorites as pack `fav`."""

    def __init__(self, levels_dir=None, update=False, favorites=None):
        self.favorites = favorites or Favorites()
        self.local = levels_dir is not None
        self.dir = Path(levels_dir) if self.local else cache_dir()
        if self.local:
            for cand in (self.dir, self.dir / 'bundle', self.dir / 'levels' / 'bundle'):
                if (cand / 'manifest.json').exists():
                    self.dir = cand
                    break
            else:
                raise LevelError(f'在 {levels_dir} 找不到 manifest.json')
        self.manifest = self._load_manifest(update)
        self._packs = {}

    def _load_manifest(self, update):
        path = self.dir / 'manifest.json'
        if not self.local and (update or not path.exists()):
            self._fetch(path, 'manifest.json')
        try:
            return json.loads(path.read_text(encoding='utf-8'))['packs']
        except (OSError, ValueError, KeyError) as e:
            raise LevelError(f'無法讀取 {path}：{e}')

    def _fetch(self, path, name):
        try:
            data = _download(BUNDLE_URL + name)
        except OSError as e:
            raise LevelError(f'下載 {name} 失敗：{e}')
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)
        return data

    @property
    def packs(self):
        known = [p for p in PACK_ORDER if p in self.manifest]
        return known + sorted(set(self.manifest) - set(known))

    def ids(self, pack):
        """Level numbers of `pack`, ascending: 1…count, or the favorites' ids."""
        if pack == FAV:
            return self.favorites.ids()
        return list(range(1, self.count(pack) + 1))

    def has(self, pack):
        return pack in self.manifest or pack == FAV

    def count(self, pack):
        if pack == FAV:
            return self.favorites.count()
        return self.manifest[pack]['count']

    def _pack_levels(self, pack):
        if pack in self._packs:
            return self._packs[pack]
        if pack not in self.manifest:
            raise LevelError(f'沒有 pack「{pack}」，可用的有：{", ".join(self.packs)}')
        info = self.manifest[pack]
        path = self.dir / info['file']
        data = path.read_bytes() if path.exists() else None
        if data is None or hashlib.sha256(data).hexdigest() != info['sha256']:
            if self.local:
                raise LevelError(f'{path} 不存在或 sha256 不符')
            data = self._fetch(path, info['file'])
            if hashlib.sha256(data).hexdigest() != info['sha256']:
                raise LevelError(f'{info["file"]} 的 sha256 不符，請用 --update 重新下載')
        levels = json.loads(data.decode('utf-8'))['levels']
        self._packs[pack] = levels
        return levels

    def load(self, pack, idx):
        """Level `idx` (1-based) of `pack`."""
        if pack == FAV:
            return self.favorites.load(idx)
        levels = self._pack_levels(pack)
        if not 1 <= idx <= len(levels):
            raise LevelError(f'pack {pack} 只有第 1～{len(levels)} 關')
        return parse_level(levels[idx - 1])


def data_dir():
    base = os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share'
    return Path(base) / 'meowdoku'


class Favorites:
    """Saved levels, one `level_*.txt`-format file each in `favorites/`, named by a
    number given when it was added (0001.txt, 0002.txt, …). That number is the
    level's id for good: removing a level leaves a gap rather than renumbering the
    rest, so stars and saves stay with their level."""

    def __init__(self, path=None):
        self.dir = Path(path) if path else data_dir() / 'favorites'

    def _paths(self):
        try:
            return {int(p.stem): p for p in self.dir.glob('*.txt') if p.stem.isdigit() and p.is_file()}
        except OSError:
            return {}

    def ids(self):
        return sorted(self._paths())

    def count(self):
        return len(self._paths())

    def load(self, idx):
        path = self._paths().get(idx)
        if path is None:
            raise LevelError(f'收藏裡沒有第 {idx} 關')
        try:
            return parse_level(path.read_text(encoding='utf-8'))
        except OSError as e:
            raise LevelError(f'無法讀取 {path}：{e}')

    def find(self, puzzle):
        """Id of the favorite with this board, or None."""
        for idx, path in sorted(self._paths().items()):
            try:
                if parse_level(path.read_text(encoding='utf-8')).regions == puzzle.regions:
                    return idx
            except (OSError, LevelError):
                continue
        return None

    def add(self, text):
        """Save a level (its `level_*.txt` text); returns (id, path)."""
        idx = max(self._paths(), default=0) + 1
        path = self.dir / f'{idx:04d}.txt'
        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return idx, path

    def remove(self, idx):
        path = self._paths().get(idx)
        if path is not None:
            path.unlink()


class Progress:
    """Per-player state in one JSON file:
    stars  {"pack:idx": 1..3}, the web game's `meowdoku_done` format (best kept)
    times  {"pack:idx": seconds}, best clear time
    saves  {"pack:idx": Game.to_dict() + elapsed}, levels left unfinished
    last   {"pack": ..., "level": ...}, the level played most recently"""

    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / 'progress.json'
        self.stars, self.times, self.saves, self.last = {}, {}, {}, {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.stars = {k: int(v) for k, v in data.get('stars', {}).items()}
            self.times = {k: float(v) for k, v in data.get('times', {}).items()}
            self.saves = dict(data.get('saves', {}))
            self.last = data.get('last', {})
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def get(self, pack, idx):
        return self.stars.get(f'{pack}:{idx}', 0)

    def best_time(self, pack, idx):
        return self.times.get(f'{pack}:{idx}')

    def record(self, pack, idx, stars, seconds=None):
        key = f'{pack}:{idx}'
        self.stars[key] = max(self.stars.get(key, 0), stars)
        if seconds is not None:
            self.times[key] = min(self.times.get(key, seconds), seconds)
        self.saves.pop(key, None)
        self.save()

    def set_last(self, pack, idx):
        self.last = {'pack': pack, 'level': idx}
        self.save()

    def get_save(self, pack, idx):
        return self.saves.get(f'{pack}:{idx}')

    def put_save(self, pack, idx, state):
        self.saves[f'{pack}:{idx}'] = state
        self.save()

    def drop_save(self, pack, idx):
        if self.saves.pop(f'{pack}:{idx}', None) is not None:
            self.save()

    def cleared(self, pack, ids):
        return sum(1 for i in ids if f'{pack}:{i}' in self.stars)

    def unsolved(self, pack, ids):
        return [i for i in ids if f'{pack}:{i}' not in self.stars]

    def forget(self, pack, idx):
        """Drop everything kept for a level that no longer exists."""
        key = f'{pack}:{idx}'
        for table in (self.stars, self.times, self.saves):
            table.pop(key, None)
        if self.last == {'pack': pack, 'level': idx}:
            self.last = {}
        self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            data = {'v': 1, 'stars': self.stars, 'times': self.times, 'saves': self.saves,
                    'last': self.last}
            tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding='utf-8')
            tmp.replace(self.path)
        except OSError:
            pass  # progress is a convenience; never crash the game over it
