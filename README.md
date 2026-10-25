# meowdoku-cli

[Meowdoku](https://github.com/plko/meowdoku/) 的終端機版本。只用 Python 3 標準函式庫（`curses`），不需要安裝任何套件。

## 安裝

```sh
ln -s "$PWD/bin/meowdoku" ~/.local/bin/meowdoku   # 不需要安裝，直接 symlink
# 或
pipx install .
```

## 執行

```sh
meowdoku               # 主選單
meowdoku 10            # pack 10 的選關畫面
meowdoku 10 42         # 直接玩 pack 10 第 42 關
meowdoku --file level.txt
```

第一次執行時會從原 repo 下載關卡，快取在 `~/.cache/meowdoku/`；通關紀錄存在 `~/.local/share/meowdoku/progress.json`。

| 參數 | 說明 |
|---|---|
| `--update` | 重新下載關卡清單 |
| `--levels-dir DIR` | 使用本機的 bundle 目錄（例如原 repo 的 `levels/bundle/`），不下載 |
| `--ascii` | 只用 ASCII 符號 |

遊戲中按 `?` 查看熱鍵。規劃與設計決策見 [PLAN.md](PLAN.md)。

## 測試

```sh
python3 -m unittest
```
