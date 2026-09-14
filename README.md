# cc-sidecar-waitwhat

在 Claude Code 之外重講它剛剛說的話。CC 不知道你用過這個工具。

| 指令 | 做什麼 | 送什麼給模型 | 實測 |
|---|---|---|---|
| `ww` | 跟丟了，重講整段脈絡 | 整個 session 的對話與工具紀錄 | 68,171 字 → 864 token，19.6 秒 |
| `ww 1` | 看不懂這一輪，白話重講 | 你的問題 + 那一輪的完整回應 | 2,315 字 → 184 token，10.9 秒 |
| `ww 3` | 往回三輪都重講 | 最後三個 turn | 7,290 字 → 301 token，11.0 秒 |

數字是**往回幾個 turn**，不是選 session——選 session 用 `-s`。一個 turn 是「你問一次 + CC 那一輪的所有回應」，中間穿插的工具呼叫不會把它切開。

`ww` = wait what。兩套 system prompt：不帶數字用「跟丟了」那套（補前提、從頭敘事），帶數字用「白話」那套（大幅砍、給一個建議而不是列選項）。**兩套都可以換成你自己的**，見下面〈換掉 prompt〉。

## 為什麼要跑在 CC 外面

在 CC 裡打 `/wait-what` 有四個代價。sidecar **不是四個都消掉**：

| 代價 | 預設路徑（`claude -p`）| 換一家模型 |
|---|---|---|
| 1. 重講輸出留在 context 裡，之後每輪都帶著 | **消掉** | 消掉 |
| 2. 重講的人是 CC 自己，帶著同一個盲點 | **消一半**——另一個 process、乾淨的對話脈絡，但同一家模型、同一份 CLAUDE.md 與 memory | 消掉 |
| 3. 「請你重講」這個動作本身扭曲後續推理 | **消掉** | 消掉 |
| 4. 花 token | **沒消**，只是從 CC 的 context 搬到另一次呼叫，還多付一整套 harness 的 system context | 消掉（改花別家的）|

真正的保證只有一條：**目標 session 的 JSONL 裡不會出現任何這次重講的痕跡**。那個檔是 CC 單向寫出去的，外面讀不留痕。所以在 CC 裡打的東西一律不合格，`!` 前綴跑 shell 也一樣。

**但「不留痕」只對目標 session 成立，不對整棵 `~/.claude` 樹成立**：`claude -p` 預設會在 cwd 對應的目錄下另開一支 session 檔（實測 300–500KB，大部分是 hook 與 MCP 的載入紀錄），所以預設指令帶了 `--no-session-persistence`。那些檔即使產生了也不會混進 `ww -l`——headless 模式寫的 user 紀錄沒有 `origin.kind`，`is_human` 會排除。

## 怎麼知道要重講哪一支

`herdr agent list` 直接給答案：

```
agent_session.value      → session id，對到 ~/.claude/projects/**/<id>.jsonl
focused                  → 你正在看哪個 pane
agent_status             → idle = 球在你這邊，working = agent 還在跑
terminal_title_stripped  → 人類可讀的名字
```

不帶參數就取 `focused: true` 那支。`-l` 列清單，`-s 3` 選第三支。

**沒有 herdr 的話，下面這條就是你的主路徑**（不是備胎）：掃 `~/.claude/projects/`，每支取「最後一則真人打字訊息」排序，標題用 **CC 最後說的話**（不是你說的——你常常只回「a」「1」，認不出是哪個對話），cwd 從 session 檔的 `cwd` 欄位讀（不是從目錄名逆推，那個 slug 把 `/` 和 `.` 都換成 `-`，本來就不可逆），再用 `ps` + `lsof` 拿到還活著的 claude 進程 cwd，把已經關掉的 session 濾掉。

比 herdr 少的是：沒有 `focused`（不知道你正在看哪個）、沒有 `idle`/`working` 狀態、換 session 時慢一拍（新 session 還沒有真人訊息前，舊的仍在清單上）。

**不要用 mtime 排序**。背景 agent 一直寫檔，你正在讀的那支反而最久沒動，排序方向是反的。

## 裝

```bash
ln -sf "$PWD/bin/ww" ~/.local/bin/ww
```

### 在哪叫出來

**唯一的要求是：那個 shell 不能是你跑 CC 的那個。** 在 CC 裡打 `!` 跑 shell 一樣不合格，那個輸出會進它的 context。

| 你的終端機 | 做法 |
|---|---|
| Ghostty | 下拉終端機，見下 |
| iTerm2 | Preferences → Profiles → Keys → 設一個 Hotkey Window |
| tmux | `bind-key w split-window -h 'ww 1; read'` 或直接開一個常駐 pane |
| kitty | `map cmd+shift+w launch --type=os-window ww 1` |
| 任何 | 就開第二個終端機視窗，切過去打 `ww 1` |

Ghostty 的設定（`~/.config/ghostty/config`）：

```
keybind = global:cmd+shift+w=toggle_quick_terminal
quick-terminal-position = top
quick-terminal-screen = macos-menu-bar
```

按 cmd+shift+W 從螢幕邊緣滑出一個獨立 shell，打 `ww 1`，再按一次收回。`global:` 前綴在 macOS 需要授權輔助使用給 Ghostty（系統設定 → 隱私權與安全性 → 輔助使用），沒授權的話快捷鍵只在 Ghostty 有 focus 時有效。

**如果你用 herdr，不要用它開 pane 來顯示**——那會把 herdr 的 `focused` 挪到新 pane，選 session 的訊號當場失效。

## 誰提供這次的重講

重講只需要「一個能吃文字吐文字的 LLM」。`auto` 先試 `cmd`，不成才退 `http`：

| 來源 | 是什麼 | 實測 |
|---|---|---|
| `cmd` | 一個 shell 指令，prompt 從 stdin 進、答案從 stdout 出 | 34.2 秒（`claude -p --model sonnet`）／ 14.9 秒（自製的 wrapper）|
| `http` | 任何吃 OpenAI 格式 `/v1/chat/completions` 的端點 | 8.8 秒（本機 proxy → Gemini Flash）|

**零設定的預設是 `cmd`**：`SIDECAR_CMD` 沒設的話，PATH 裡有 `claude` 就自動用 `claude -p --no-session-persistence`。這個工具的使用者按定義都有它。代價是慢——CLI 啟動開銷加上去大概是 HTTP 那條的三到四倍。

想快一點就指定模型或換工具：

```bash
SIDECAR_CMD='claude -p --model haiku'
SIDECAR_CMD='ollama run llama3'
SIDECAR_CMD='llm -m gpt-4o'
SIDECAR_CMD='my-own-wrapper'          # 自己寫一支讀 stdin 印 stdout 的就能接
```

最後那行是這個設計的重點：**要接什麼模型不必改這個 repo**。寫一支讀 stdin、印 stdout、失敗回非零的指令就行，`ww` 不需要知道它在做什麼。

指令用 shell 的拆詞規則切開（`shlex`），但**不經過 shell**，所以 pipe 和重導向不會生效。

每次跑完最後一行都寫出實際來源：

```
── 白話：View A  (de0e89f8，送出 3,083 字 → cmd:my-own-wrapper)      ← 送出前就知道要去哪
── 14.9s  來源 cmd:my-own-wrapper
── 11.4s  來源 http:gemini-3.8-flash-high  ← 退回原因：沒有設 SIDECAR_CMD，PATH 裡也沒有 claude
── 白話：View A  (快取命中 · 來源 cmd:claude -p)
```

送出前那行會先寫出**打算**用哪條，最後一行寫**實際**用了哪條。兩者不同就是中途退回了，退回原因會接在後面。

指定單一來源時是**嚴格模式**——`--source cmd` 失敗就 exit 1，不會偷偷換別條。

### 環境變數

```bash
SIDECAR_SOURCE=cmd           # 改預設來源（auto / cmd / http）
SIDECAR_CMD='...'            # cmd 那條要跑什麼指令
SIDECAR_PROXY=http://...     # http 那條的端點
SIDECAR_MODEL=...            # http 那條的模型
SIDECAR_API_KEY=...          # http 那條的 key；不給也不報錯，只是不送 Authorization（ollama / LM Studio 這類不需要 key）
```

輸入是本機 JSONL、輸出是純文字，中間那顆模型是純粹的可替換件。

## 換掉 prompt

內建的兩套是**起點，不是成品**。重講的品質幾乎全部由 prompt 決定，而什麼叫「講清楚」每個人的標準不一樣——所以這裡預期你會改。

把檔案放進 `~/.config/cc-sidecar-waitwhat/`（`SIDECAR_PROMPT_DIR` 可改位置）就會蓋掉內建的：

```
~/.config/cc-sidecar-waitwhat/wait-what.md    ← ww（整段脈絡）用的
~/.config/cc-sidecar-waitwhat/plain.md        ← ww N（白話重講）用的
```

兩個各自獨立，只放一個就只蓋那一個。檔案是空的會退回內建，不會送出空 prompt。

內建那兩套**刻意不指定輸出語言**，只寫「用跟原文相同的語言回答」——所以你的 CC 講英文就回英文、講中文就回中文。要固定語言就在自己的 prompt 裡寫死。

`~/.claude/glossary.md` 存在的話會附在 prompt 後面當個人語彙表，讓重講沿用你自己的說法。但**只在要重講的內容比語彙表長的時候才附**——短 turn 配上長語彙表，模型看到的幾乎全是詞彙，會答非所問（實測過一次：2,722 字的 payload 裡語彙表佔 2,050 字，模型回「你提供的內容缺少需要重講的技術說明」）。

## 終端機樣式

模型回的是 markdown，直接印在終端機上會看到一堆 `**`、反引號和 ``` 圍欄。

**有 [rich](https://github.com/Textualize/rich) 就用 rich**（`Markdown` 加 `soft_wrap=True`），它會真的排版表格、算對中文寬度、給程式碼區塊上底色。`soft_wrap` 不能省——沒有它，rich 會用英文的空白斷詞邏輯重排，把 `2*3*4` 從中間切成兩行。

沒有 rich 就退回內建的 `basic()`，五十行，夠用但不排表格：

| markdown | rich | 內建 fallback |
|---|---|---|
| 表格 | 畫線排版、中文對齊 | 原樣印出 `\| a \| b \|` |
| `**重點**` | 粗體 | 粗體 |
| `` `指令` `` | 上色加底 | 青色 |
| ` ```區塊``` ` | 底色框 | 縮排變暗 |
| `> 引用` | `▌` 帶底色 | `│ ` |

保留 fallback 是因為 rich 是選用的：這個 repo 不釘任何套件，rich 可能只是被別的東西當作相依裝進來，哪天就消失了。要確保有它就 `pip install rich`。

**輸出不是終端機時自動關掉**，所以 `ww 1 > out.md` 或 pipe 給別的工具拿到的是乾淨的原始 markdown。`--raw` 強制關閉，`NO_COLOR=1` 也認。

## 快取

存在 `~/.cache/cc-sidecar-waitwhat.json`（`SIDECAR_CACHE` 可改）。key 是來源 + system prompt + 完整 payload 的 SHA-256，每條來源的答案各佔一格。`auto` 模式查快取時每格都試，命中哪格就標哪個來源。

實測同一條指令連跑兩次：**12.09 秒 → 0.877 秒**。

**但命中率沒有想像中高**：只要那支 CC session 多寫了一則訊息，payload 就變了，一定落空。真正會命中的是「那一輪已經結束、你回頭再看一次」。CC 還在跑的時候重複打 `ww 1`，每次都是新的請求——這是刻意的，寧可重問也不給你過期的重講。

```bash
ww --cache-stats   # 看有幾筆、各是什麼模式
ww 1 --no-cache    # 強制重問一次
```

超過 200 筆會丟掉最舊的。檔案壞掉時當空的處理，不會炸。

## 已知限制

- **重講可能引入錯誤**。實測有一次把 `poll_interval` 的 2.0 秒講成 0.2 秒——紀錄裡兩個數字都出現過，模型挑錯邊。重講是二手資料，拿它定位、不要拿它當事實。
- **依賴 CC 的 session schema**（目前 2.1.270）。CC 改版可能動 `origin.kind` / `isSidechain` 這些欄位。壞掉的方向是沒輸出，不是靜默給錯答案。
- **`herdr agent read` 讀不到對話**。畫面底部只有輸入框和 statusline，對話早捲上去了，所以走 JSONL 而不是讀畫面。
- **CC 還在跑的時候重講，拿到的是半截**。`-l` 的狀態欄會標「還在跑」還是「等你回」；直接跑 `ww` 而對象還在跑時會先印一行警告。

## 需要什麼

一個 LLM（`claude` / `ollama` / `llm` / 任何 OpenAI 相容端點），加 Python。沒有必裝的 Python 套件；裝了 `rich` 版面會好看很多。

Python 版本開發於 3.14，只用標準庫，最舊用到的 API 是 `subprocess.run(capture_output=...)`（3.7）。**3.7–3.13 沒有實測過**。

`ps` 與 `lsof` 用來過濾已關閉的 session，缺了不會壞——那一步會直接跳過，只是清單裡會多出已經關掉的對話。

## 測試

```bash
python3 -m unittest discover -s tests
```

59 個，覆蓋解析層、找 session、prompt 覆寫、快取、終端機渲染與來源路由（其中 2 個在沒裝 rich 的環境會 skip）。`cmd` 那條用 `cat` / `head` / `false` 當假 LLM 測，`from_files` 用 fixture JSONL 測，都不需要真的模型或 herdr。herdr 那條依賴外部狀態，沒有自動化測試——驗證方式是實跑 `ww -l` 看它有沒有回出帶狀態的清單。
