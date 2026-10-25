"""Level parsing and level-bundle loading (download, cache, sha256 check)."""

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from .model import Puzzle
from .solver import solve

BUNDLE_URL = 'https://raw.githubusercontent.com/plko/meowdoku/main/levels/bundle/'
PACK_ORDER = ['6', '7', '8', '9', '10', '11', '12', 'hard', 'bad']


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
        found = solve(regions)
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
    """Packs from a bundle directory: a local one, or the download cache."""

    def __init__(self, levels_dir=None, update=False):
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

    def count(self, pack):
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
        levels = self._pack_levels(pack)
        if not 1 <= idx <= len(levels):
            raise LevelError(f'pack {pack} 只有第 1～{len(levels)} 關')
        return parse_level(levels[idx - 1])
