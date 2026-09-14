# Brag Plan: cc-sidecar-waitwhat

## What is this app?

一個 CLI，在 Claude Code 之外把它剛剛講的那段重講成白話。整個設計的重點是 CC 完全不知道這件事發生過——重講不經過它的 context，它的 session 紀錄裡一個字都沒有。

## The angle

多數工具在賣「更快」「更聰明」。這個在賣**不被察覺**。

笑點是它的動機有多偏執：你看不懂 AI 講什麼，但你不能問它，因為問這個動作本身就是訊號——它會以為那段很重要，接下來幾輪都往那邊靠。所以你另外開一個終端機，偷偷問第二個模型，然後把視窗收起來，假裝什麼都沒發生。

這是一個為了**保護 AI 的注意力**而存在的工具。而且它是認真的。

## Hook (first 2-3 seconds)

終端機裡一段密密麻麻的技術文字往上捲，停住。游標停在輸入框，什麼都沒打。

畫面上壓一行字：**看不懂。**

不解釋、不配圖、不加音效。就是那個「你盯著螢幕不知道該問什麼」的兩秒。

## Key moments (the middle)

- **想打字又縮手**：游標在 CC 輸入框閃，打出「這什麼意思」四個字，然後一個一個退格刪掉。這是全片最重要的一個動作——它演出了「不能問」。
- **下拉終端機滑出**：畫面上緣滑下一個獨立終端機，蓋住上半部。打 `ww 1`。底下那個 CC 視窗完全沒動。
- **兩行收據**：`── 白話：Codex desktop advisor 改善 (de0e89f8，送出 2,315 字 → cmd:claude -p)` 和 `── 10.9s 來源 cmd:claude -p`。重講文字在中間浮現。這是產品真正的樣子，不是示意圖。
- **收回去**：終端機往上滑走。CC 視窗回到全畫面，跟第一秒**一模一樣**——同樣的捲動位置、同樣的空輸入框。

## Outro / punchline

畫面靜止在那個沒有變化的 CC 終端機上。

兩行壓字依序出現：**session 外請它重講**（白）、**不要污染 context**（灰）。然後專案名。

原本是「它不知道你剛剛沒聽懂。」——那句是效果，不是做法，使用者看完不知道要幹嘛。換成現在這版：第一行講做法，第二行講理由。deadpan 的笑點少了一點，但這支片是要貼在 README 開頭的，看完就要知道工具在做什麼比較重要。

## User flow worth showing

entry → key action → result，三拍都是真的：

1. CC 吐出一段看不懂的技術說明，你停在輸入框前
2. `cmd+shift+W` 叫出下拉終端機，打 `ww 1`
3. 白話版出現、看完、收起來；CC 那側零變化

## Tone

- Preset: `deadpan`
- Creative direction: 一個為了不驚動 AI 而存在的工具，全程用做企業產品的嚴肅度來拍
- Interpretation: 長停頓、大量留白、一次只給一行字。不要配快節奏音樂、不要塞三張功能卡。笑點是整支片子從頭到尾沒有笑——包括那個偏執的動機被當成理所當然講出來。

## Format: landscape — 1920x1080
## Duration: 25 秒

## Visual identity (from the project)

專案沒有網頁，視覺識別來自它實際的終端機輸出（`sidecar/render.py`）：

- Background: `#0d1117` 深色終端機底（Ghostty 暗色 + `background-opacity 0.92` 的毛玻璃感）
- Accent: ANSI cyan `\033[36m` → `#2aa198`，行內程式碼用色
- Secondary accent: ANSI yellow `\033[33m` → `#b58900`，標題用色
- Dim: `\033[2m`，程式碼區塊與收據行
- Text: `#c9d1d9`
- Display font: Maple Mono NF（使用者 Ghostty 實際字型）
- Body font: 同上，全片等寬字
- Strongest visual element: 那兩行 `──` 開頭的收據行。它們是這個工具唯一的「介面」。

## Share copy (draft)

跟丟 Claude Code 在講什麼的時候，不要問它——那段會留在它的 context 裡，而且會讓它以為那段很重要。所以我寫了一個在它背後問第二個模型的 CLI。

## Audio direction

- Role: intentional near-silence，只有機械聲
- Music: 低音量的低頻 bed，從 Scene 2 進，Scene 5 收掉。不要節拍感。
- Music treatment: −20dB 左右，淡入 1s，最後 2s 淡出到全無
- Music cue guidance: 不做 beat sync。deadpan 的節奏來自停頓不是鼓點。如果要對點，只對「退格刪字」那一下。
- Audio-reactive treatment: none
- SFX posture: sparse。全片最多四個聲音：打字、退格、終端機滑出、滑回。
- Audio-coupled moments: 打「這什麼意思」的鍵盤聲，退格的四下，`ww 1` 的兩下加 Enter
- Restraint rule: 不准有 whoosh、不准有 riser、不准有結尾的 logo 音效。退格那四下是全片音量最高的地方。

## Storyboard

### Scene 1 — 看不懂 — 4.5s
全畫面終端機。一段真實的技術文字往上捲了半秒後停住（用 README 裡「但『不留痕』只對目標 session 成立」那段的實際文字）。游標在輸入框閃。捲動停止後留 1.5s 什麼都不發生。壓字「看不懂。」淡入，停 1.8s。
Sequential/interaction: yes — 文字捲動後完全靜止，游標持續閃爍，刻意的空白
Audio intent: 只有環境底噪，讓沉默變得不舒服
Audio-coupled idea: none
Music: none（還沒進）
Transition mood: 無轉場，直接接下一個動作 → Scene 2

### Scene 2 — 想問又不能問 — 4.6s
同一個畫面。游標開始打字：「這什麼意思」，一個字一個字出現（0.25s 一個字）。字打完後，畫面下方浮出一行「問了，它會以為那段很重要。」——**這是全片唯一解釋動機的地方**，沒有它，結尾的「它不知道」就只是一句沒有前提的話（第一版實測就是這樣，使用者回饋「看不懂有啥意義」）。那行停 2s，然後退格把字刪光。
Sequential/interaction: yes — 逐字打入再逐字刪除，這是全片的核心動作
Audio intent: 打字聲平淡，退格四下要清楚、有實體感
Audio-coupled idea: 每個字一次鍵盤聲；退格四下是全片最響的音
Music: 低頻 bed 在退格結束後淡入
Transition mood: 硬切 → Scene 3

### Scene 3 — 另一個終端機 — 4.5s
畫面上緣滑下一個獨立終端機視窗（0.4s），蓋住上半部，底下 CC 視窗仍然看得到、完全沒動。新視窗裡打 `ww 1`（0.6s）+ Enter。
Sequential/interaction: yes — 下拉動作 + 打指令
Audio intent: 滑出是一個乾的短聲，不是 whoosh
Audio-coupled idea: `ww 1` 兩鍵加 Enter
Music: bed 持續
Transition mood: 無轉場 → Scene 4

### Scene 4 — 收據與重講 — 5s
下拉終端機裡依序出現：先是收據行 `── 白話：Codex desktop advisor 改善 (de0e89f8，送出 2,315 字 → cmd:claude -p)`（dim），接著重講文字浮現三行（cyan 標行內程式碼），最後 `── 10.9s 來源 cmd:claude -p`。每行間隔 0.5s，全部出現後整組停 2s 讓人讀完。
Sequential/interaction: yes — 三段依序出現，但最後必須整組停住夠久
Audio intent: 每行一個極輕的 tick，不要變成節奏
Audio-coupled idea: 三次輕 tick，音量遞減
Music: bed 持續
Transition mood: 乾淨收起 → Scene 5

### Scene 5 — 什麼都沒發生 — 2s
下拉終端機往上滑走（0.4s）。CC 視窗回到全畫面——捲動位置、游標、空輸入框，跟 Scene 1 結束時**完全一致**。停 1.6s，什麼都不動。
Sequential/interaction: yes — 關鍵是「回到原狀」這件事本身要被看見
Audio intent: 滑回的聲音之後，音樂開始淡出
Audio-coupled idea: 滑回的乾聲
Music: 淡出
Transition mood: 慢淡 → Scene 6

### Scene 6 — 落款 — 5.2s
「session 外請它重講」先出現，0.65s 後「不要污染 context」接上（較暗），再 0.9s 後小字 `cc-sidecar-waitwhat`。三段依序、不同明度，讀起來是做法→理由→名字。**全部出現後靜止 2.8 秒**——第一版只留 0.3 秒，實測根本來不及讀完（使用者回饋「結束得太快」）。brag 的 readable 法則是「一句話約 0.3s 一個詞、最少 1.2s，而且要 settled 之後才開始算」，0.3 秒連一行都不夠。音樂已經全無。
Sequential/interaction: none
Audio intent: 完全靜音收尾
Audio-coupled idea: none
Music: none
Transition mood: 直接結束

**總長：4.5 + 4.6 + 4.5 + 5 + 2 + 5.2 = 25.8 秒**（實際 render 25.0 秒，踩在 brag 15–25 秒的上限）

**Music mood for this video:** deadpan（近乎無聲，只有低頻 bed 撐住中段）
**Audio summary:** 從完全沉默開始，退格那四下是唯一的音量高點，低頻 bed 在中段撐著，最後兩秒回到完全沉默——聲音的弧線跟「什麼都沒發生」這個結論一致。
