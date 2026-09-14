# Hyperframes Composition Brief: cc-sidecar-waitwhat

> 順序備註：這份 brief 是在 composition 寫完之後才補的，不是照 step-3 的規定先寫。內容與實際 `composition/index.html` 對帳過，不是事後美化的版本。

## Objective

做一支 deadpan 風格的發布短片，主張是「這個工具的價值在於不被察覺」。

## Output

- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 22 秒

## Source Material

- Project root: `/Users/linhancheng/Desktop/projects/cc-sidecar-waitwhat`
- Primary files read: `README.md`、`sidecar/render.py`、`sidecar/cli.py`
- Product name: cc-sidecar-waitwhat
- Tagline: 在 Claude Code 之外重講它剛剛說的話。CC 不知道你用過這個工具。
- Key UI moment to recreate: 下拉終端機蓋在 CC 終端機上，兩行 `──` 開頭的收據行
- Copy that must appear verbatim:
  - `── 白話：cc-sidecar-waitwhat　(de0e89f8，送出 2,315 字 → cmd:claude -p)`
  - `── 10.9s　來源 cmd:claude -p`
  - CC 那側捲動的文字取自 README〈為什麼要跑在 CC 外面〉最後一段

## Creative Direction

- Tone preset: `deadpan`
- Creative direction: 一個為了不驚動 AI 而存在的工具，用做企業產品的嚴肅度拍
- Interpretation: 長停頓、大留白、一次一行字。節奏來自沉默不是剪接。
- Angle: 多數工具賣「更快」，這個賣「不被察覺」。笑點是動機的偏執程度被當成理所當然講出來。
- Hook: 一段看不懂的技術文字停住，游標閃，壓字「看不懂。」
- Outro: 「它不知道你剛剛沒聽懂。」
- Avoid: 泛用 SaaS 語言、抽象動態背景、任何跟這個專案無關的畫面

## Visual identity

取自 `sidecar/render.py` 的 ANSI 常數，不是另外設計的配色：

| 角色 | 值 | 來源 |
|---|---|---|
| 背景 | `#05070a` / 終端機 `#0d1117` | Ghostty 暗色 |
| Accent | `#2aa198` | `CYAN = "\033[36m"` |
| Secondary | `#b58900` | `YELLOW = "\033[33m"` |
| Dim | `#7a828d` | `DIM = "\033[2m"`，經 contrast 檢查上調 |
| Text | `#c9d1d9` | 終端機前景 |

全片等寬字。`.dim` 與 `.term-title` 的原始值（`#5b6672` / `#55606e`）在 contrast 檢查時是 2.74:1，低於 4.5:1，依 CLI 建議上調到 `#7a828d`。

## 實作決定（偏離 plan 之處）

- **全片靜音**。plan 原本寫低頻 bed，但 brag 內建音樂是 "happy beats business moves"，跟 deadpan 直接衝突。既然 plan 的 restraint rule 已經寫「退格那四下是全片音量最高的地方」，做成完全無聲更徹底。這是刻意的偏離，不是漏做。
- **疊放標記**。下拉終端機蓋住底下 CC 終端機是核心畫面，但 layout 檢查把它報成 29 個 `text_occluded` / `content_overlap` 錯誤。依 CLI 指示加 `data-layout-allow-occlusion` 與 `data-layout-allow-overlap`（26 處）標記為刻意疊放。
- **`#quick` 初始位置**。原本寫在 CSS `transform`，lint 報 `gsap_css_transform_conflict`（GSAP 的 `y` 會覆蓋整個 transform）。改成 `tl.set("#quick", { y: -560 }, 0)`。

## Gate 結果

```
npx hyperframes lint   → 0 errors, 0 warnings
npx hyperframes check  → Check passed（Lint / Runtime / Layout / Motion / Contrast 全過）
```
