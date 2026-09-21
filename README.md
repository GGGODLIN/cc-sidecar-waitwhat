# cc-sidecar-waitwhat

在 Claude Code 之外重講它剛剛說的話。CC 不知道你用過這個工具。

*[English](README.en.md)*

![cc-sidecar-waitwhat demo](screenshots/demo.gif)

| 指令 | 做什麼 | 送什麼給模型 | 實測 |
|---|---|---|---|
| `ww` | 跟丟了，重講整段脈絡 | 整個 session 的對話與工具紀錄 | 68,171 字 → 864 token，19.6 秒 |
| `ww 1` | 看不懂這一輪，白話重講 | 你的問題 + 那一輪的完整回應 | 2,315 字 → 184 token，10.9 秒 |
| `ww 3` | 往回三輪都重講 | 最後三個 turn | 7,290 字 → 301 token，11.0 秒 |

數字代表**往回幾個 turn**，選 session 請用 `-s`。這裡的一個 turn 算「你問一次加上 CC 那一輪的回應全部」，中間呼叫工具不會拆開算。

`ww` 就是 wait what。內建兩套 system prompt：不加數字用「跟丟了」（補足前情、從頭梳理）；加數字用「白話」（大砍細節、只給一個明確建議）。兩套都能換。

## 為什麼要跑在 CC 外面

在 CC 裡下 `/wait-what` 有四個代價，sidecar 能避開其中幾項：

| 代價 | 預設路徑（`claude -p`）| 換一家模型 |
|---|---|---|
| 1. 重講輸出留在 context 裡，之後每輪都帶著 | 消掉 | 消掉 |
| 2. 重講的人是 CC 自己，帶著同一個盲點 | 消一半。另一個 process、乾淨的對話脈絡，但同一家模型、同一份 CLAUDE.md 與 memory | 消掉 |
| 3. 「請你重講」這個動作本身扭曲後續推理 | 消掉 | 消掉 |
| 4. 花 token | 沒消，只是從 CC 的 context 搬到另一次呼叫，還多付一整套 harness 的 system context | 消掉（改花別家的）|

如果你不介意 CC 這個殼知道你按過按鈕、只要模型看不到重講內容，[cc-mod-waitwhat](https://github.com/GGGODLIN/cc-mod-waitwhat) 是跑在 CC 裡面的 Claude Mods 版：按鈕畫在提示框上方，不用切終端機、不用選 session，同一組環境變數與 prompt 覆寫檔。在 Orca 裡那兩顆按鈕會改成拆一格終端跑這支 `ww`，session 由下面第一條路自己認。

這裡的核心保證只有一個：**目標 session 的 JSONL 裡完全不會留下重講痕跡**。CC 單向寫檔，外部工具只讀不寫。只要在 CC 裡敲指令，就算加 `!` 跑 shell 都不行，全都會寫進去。

**但「不留痕跡」只限目標 session，不保證整棵 `~/.claude` 都乾淨**：`claude -p` 預設會在工作目錄開一個新的 session 檔（實測 300–500KB，多半在記載入 hook 與 MCP 的過程），因此預設指令加了 `--no-session-persistence`。即便產生了這些檔案，也不會出現在 `ww -l` 裡。headless 寫入的紀錄沒有 `origin.kind`，`is_human` 會直接過濾掉。

## 怎麼知道要重講哪一支

三條路依序試，第一條命中就用它當清單第一列，其餘幾支照樣列出來。

### 第一條：同一個 tab 的鄰居

Orca 和 herdr 都會把 tab id 塞進每一格的環境變數，而且 CC 行程會繼承：

```
ORCA_TAB_ID     → Orca
HERDR_TAB_ID    → herdr
```

ww 讀自己這格的值，用 `ps -E` 掃 CC 行程的環境變數，值一樣的就是同一個 tab 的鄰居。所以在 CC 旁邊拆一格跑 ww，它自己知道要重講哪一支，不必問誰被 focus。

拿到 pid 之後再對 session id，兩條規則：

1. 啟動參數裡有 uuid 就直接用（`claude --resume <id>` / `--session-id <id>`）
2. 沒有的話，拿行程啟動時間對 session 檔第一筆記錄的時間，5 秒窗內**唯一**命中才算數

不唯一就不猜，跳過。剛開還沒講過話的 CC 根本沒有 session 檔，也一樣跳過——沒講過話就沒有東西可以重講。

這條路不需要呼叫 orca 或 herdr 的任何指令，只讀環境變數。換了多工終端機就是在上面那張表多一行；都沒有的話整條路自動熄火，往下走。

### 第二條：`herdr agent list`

有 herdr 就很簡單：

```
agent_session.value      → session id，對到 ~/.claude/projects/**/<id>.jsonl
focused                  → 你正在看哪個 pane
agent_status             → idle = 球在你這邊，working = agent 還在跑
terminal_title_stripped  → 人類可讀的名字
```

不給參數就抓 `focused: true` 那支。用 `-l` 看清單，用 `-s 3` 選第三支。

第一條命中時，herdr 只負責補齊清單其餘幾支；`focused` 讓給第一條，因為「你眼前這格」比「全機哪一格被 focus」準。

### 第三條：掃檔案

**沒裝 herdr，這是主路徑、不是備胎**：掃描 `~/.claude/projects/`，依每支的「最後一則真人訊息」排序。清單標題直接抓 **CC 最後說的話**，因為真人常只回「a」或「1」，看不出是哪件事。工作目錄直接從檔案裡的 `cwd` 欄位拿（目錄名會把 `/` 和 `.` 轉成 `-`，轉不回來），再用 `ps` 搭配 `lsof` 查活著的進程，濾掉關閉的 session。

這做法比 herdr 缺了幾樣：拿不到 `focused`、不知對象是 `idle` 還是 `working`、換對話時會慢一拍（新 session 在你打字前，舊的還佔著位子）。第一條路補得回前兩樣裡的第一樣。

**不要用 mtime 排序**。背景 agent 一直寫檔，你盯著看的那支反而最久沒動，排出來正好是反的。

## 裝

```bash
ln -sf "$PWD/bin/ww" ~/.local/bin/ww
```

需要 Python 和一個 LLM。沒有非裝不可的 Python 套件；裝了 `rich` 排版會好看很多。

### 在哪叫出來

只要不在 CC 所在的 shell 裡跑就行。CC 裡加 `!` 跑 shell 一樣出局，輸出照樣進 context。

| 你的終端機 | 做法 |
|---|---|
| Ghostty | 下拉終端機 |
| iTerm2 | Preferences → Profiles → Keys → 設一個 Hotkey Window |
| tmux | `bind-key w split-window -h 'ww 1; read'` 或直接開一個常駐 pane |
| kitty | `map cmd+shift+w launch --type=os-window ww 1` |
| 任何 | 就開第二個終端機視窗，切過去打 `ww 1` |

Ghostty 設定範例（`~/.config/ghostty/config`）：

```
keybind = global:cmd+shift+w=toggle_quick_terminal
quick-terminal-position = top
quick-terminal-screen = macos-menu-bar
```

按 cmd+shift+W 會叫出懸浮 shell，跑完 `ww 1` 再按一次收合。在 macOS 設 `global:` 必須開輔助使用權限給 Ghostty（系統設定 → 隱私權與安全性 → 輔助使用），沒給的話快捷鍵只在 Ghostty 視窗裡有用。

**用 herdr 的話，千萬別叫它開新 pane 顯示**。focus 跑掉，選 session 的邏輯馬上抓瞎。

## 誰提供這次的重講

重講只要有個能吃文字、吐文字的 LLM 就行。設為 `auto` 時先試 `cmd`，不行再換 `http`：

| 來源 | 是什麼 | 實測 |
|---|---|---|
| `cmd` | 一個 shell 指令，prompt 從 stdin 進、答案從 stdout 出 | 34.2 秒（`claude -p --model sonnet`）／ 14.9 秒（自製的 wrapper）|
| `http` | 任何吃 OpenAI 格式 `/v1/chat/completions` 的端點 | 8.8 秒（本機 proxy → Gemini Flash）|

什麼都沒設時走 `cmd`：沒填 `SIDECAR_CMD` 且 PATH 找得到 `claude`，就用 `claude -p --no-session-persistence`。既然你用 CC，電腦裡肯定有。缺點就是慢，CLI 啟動的時間算進去，比 HTTP 慢了三到四倍。

要快就換模型或換工具：

```bash
SIDECAR_CMD='claude -p --model haiku'
SIDECAR_CMD='ollama run llama3'
SIDECAR_CMD='llm -m gpt-4o'
SIDECAR_CMD='my-own-wrapper'          # 自己寫一支讀 stdin 印 stdout 的就能接
```

指令會照 shell 規則切參數（`shlex`），但**不經過 shell 執行**，不能寫 pipe 或重導向。

每次跑完，最後一行會印出實際來源：

```
── 白話：View A  (de0e89f8，送出 3,083 字 → cmd:my-own-wrapper)      ← 送出前就知道要去哪
── 14.9s  來源 cmd:my-own-wrapper
── 11.4s  來源 http:gemini-3.8-flash-high  ← 退回原因：沒有設 SIDECAR_CMD，PATH 裡也沒有 claude
── 白話：View A  (快取命中 · 來源 cmd:claude -p)
```

選定來源就是**嚴格模式**。下了 `--source cmd` 只要失敗就 exit 1，不主動 fallback。

### 環境變數

```bash
SIDECAR_SOURCE=cmd           # 改預設來源（auto / cmd / http）
SIDECAR_CMD='...'            # cmd 那條要跑什麼指令
SIDECAR_PROXY=http://...     # http 那條的端點
SIDECAR_MODEL=...            # http 那條的模型
SIDECAR_API_KEY=...          # http 那條的 key；不給也不報錯，只是不送 Authorization（ollama / LM Studio 這類不需要 key）
```

## 換掉 prompt

預設 prompt 是打底用的，建議自己改。講得清不清楚很主觀，重講品質又幾乎全看 prompt。

把檔案放進 `~/.config/cc-sidecar-waitwhat/`（可用 `SIDECAR_PROMPT_DIR` 自訂）就能覆蓋：

```
~/.config/cc-sidecar-waitwhat/wait-what.md    ← ww（整段脈絡）用的
~/.config/cc-sidecar-waitwhat/plain.md        ← ww N（白話重講）用的
```

兩套各自獨立，放哪個就蓋哪個。如果是空檔會退回預設，不送出空內容。

內建 prompt 沒限定語言，只要求「用跟原文相同的語言回答」。CC 說英文就回英文，說中文就回中文。想綁死語言，在自訂 prompt 裡寫明就行。

有 `~/.claude/glossary.md` 的話會接在後面當術語表，讓模型講你的黑話。不過重講內容如果比術語表短就不會帶。內容太短卻塞一整串名詞，模型會搞錯焦點直接回歪。實測 2,722 字的 payload 裡塞了 2,050 字術語表，模型直接回「你提供的內容缺少需要重講的技術說明」。

## 終端機樣式

模型吐的是 markdown，直接丟終端機會看到整片的 `**`、反引號和代碼圍欄。

裝了 [rich](https://github.com/Textualize/rich) 就優先用 rich（帶 `Markdown` 與 `soft_wrap=True`）。它能正確畫出表格、算對中文字寬、給代碼區塊上底色。`soft_wrap` 一定要開，不然 rich 會拿英文邏輯斷行，把 `2*3*4` 拆成兩截。

沒裝 rich 就換內建的 `basic()`。五十行代碼，能讀但不管表格排版：

| markdown | rich | 內建 fallback |
|---|---|---|
| 表格 | 畫線排版、中文對齊 | 原樣印出 `\| a \| b \|` |
| `**重點**` | 粗體 | 粗體 |
| `` `指令` `` | 上色加底 | 青色 |
| ` ```區塊``` ` | 底色框 | 縮排變暗 |
| `> 引用` | `▌` 帶底色 | `│ ` |

保留這套陽春 fallback 是因為不想綁套件。rich 可能是別的工具順手裝進來的，隨時會不見。想穩定用就 `pip install rich`。

**只要輸出不是 terminal 就會自動關閉樣式**。像 `ww 1 > out.md` 或 pipe 給其他指令，拿到的都是乾淨的 markdown。加 `--raw` 會強制關掉，也支援 `NO_COLOR=1`。

## 快取

快取寫在 `~/.cache/cc-sidecar-waitwhat.json`（可用 `SIDECAR_CACHE` 改）。key 拿模式與標準化後的 user／assistant 對話算 SHA-256，不含入口、模型來源或各自的 payload 包裝。因此 terminal `ww` 與 CC 內按鈕會互相命中；哪個入口先產生答案，另一邊就直接沿用。舊版 key 不搬移，同一段舊對話升級後第一次重看仍會重問一次。

同一條指令連敲兩次的實測：**12.09 秒 → 0.877 秒**。

命中率其實不高。只要 CC session 多跳一句話，payload 不同就直接 miss。會命中的情況，多半是那一輪聊完了你回頭重看。CC 還在跑時連敲 `ww 1` 都會重問，寧可重新算也不給你舊答案。

```bash
ww --cache-stats   # 看有幾筆、各是什麼模式
ww 1 --no-cache    # 強制重問一次
```

紀錄超過 200 筆會自動刪掉最舊的。快取檔壞了就當作空的，不會崩潰。

## 已知限制

- **重講可能出錯**。實測曾把 `poll_interval` 的 2.0 秒寫成 0.2 秒。上下文兩個數字都有，模型猜錯了。重講是二手摘要，拿來抓方向就好，不要當絕對事實。
- **依賴 CC 目前的 session 結構**（版號 2.1.270）。改版如果動到 `origin.kind` 或 `isSidechain`，腳本會抓不到東西，但不會默默吐錯話。
- **`herdr agent read` 拿不到對話**。畫面下方只有輸入列和狀態列，文字早捲上去了，所以直接看 JSONL。
- **CC 還在動就下指令，只會拿到半截**。`-l` 裡會標「還在跑」還是「等你回」。如果對象正在動，`ww` 會先跳警訊。
- 沒 `ps` 和 `lsof` 也能跑。過濾無效 session 的步驟會直接跳過，清單裡會多出幾個關掉的對話。

在 Python 3.14 下寫的，只碰標準庫。最舊只依賴 3.7 的 `subprocess.run(capture_output=...)`，但 3.7 到 3.13 之間沒逐一測過。

## 測試

```bash
python3 -m unittest discover -s tests
```

共有 84 個測試，覆蓋解析邏輯、尋找 session、tab 鄰居比對、prompt 覆寫、快取、渲染與來源路由（沒 rich 的環境會 skip 掉 2 個）。`cmd` 測項拿 `cat`、`head`、`false` 模擬 LLM，`from_files` 與 `from_pane` 用 fixture JSONL 加假的行程清單，不用開模型、不用裝 herdr、也不用真的在 Orca 裡跑。herdr 與 Orca 的實機行為依賴外在環境，沒有自動化測試，直接在 CC 旁邊拆一格跑 `ww -l`，看第一列有沒有標成你正在看的那支。
