# pdf2md figure/table description protocol

把 pdf2md bundle 里的图/表写成自然语言描述。Python 只负责导出请求、合并结果、统计进度；**看图写描述发生在多模态 agent 这一侧**（Cursor、Claude Code 等能读图的 host）。

批次控制是确定性的 CLI 能力，不是靠自觉少塞几张。一本书可能有几百张图，一次全塞进多模态上下文会爆，质量也会掉。

入口：

```
python3 -m book_to_skill.pdf2md.cli <subcommand> ...
```

下文 `pdf2md ...` 是协议缩写，实际命令把 `pdf2md` 换成上面这一行。

---

## 循环协议

按这个骨架循环，直到 `done: true`。每轮只处理当前 batch，不要一次导出全书。

```
1. pdf2md describe-status --bundle <dir> --json     # 看还剩多少
2. pdf2md describe-export --bundle <dir> --pending-only --limit 20 --out batch.jsonl
3. 逐条读 batch.jsonl：按 asset_path 打开图片，看图写描述
4. 写 responses.jsonl
5. pdf2md describe-merge --bundle <dir> --descriptions responses.jsonl
6. 回到 1，直到 done: true
```

规则：

- `--pending-only` 跳过 `block.meta.description_source == "vlm"` 的 block。已经描述过的不要再导出、不要再看。
- `--limit 20` 是默认批次上限。图特别密或单张信息量很大时把 20 再降到 8–12，但**必须改 `--limit` 数字**，不许靠自己少读几行 JSONL 假装控量。
- 需要表时再加 `--include-tables`。默认只出 figure。
- `asset_path` 是 bundle 相对路径。打开图片用 `<bundle>/<asset_path>`，例如 `<bundle>/assets/figures/p0012_fig0001.png`。
- 每一轮 merge 成功后再 status，不要凭记忆数还剩多少。
- 不要并行开多个 merge 打同一个 bundle。

---

## 描述质量规则

描述必须让没看见原图的人能重建**结构**。空话（「这是一张流程图」）不合格。

必须写到的内容：

- **节点/标签**：图上每个可读的框、端口、状态、图例项。用图上的原文，不要改名。
- **连接关系**：谁连到谁、箭头方向、是数据流 / 控制流 / 反馈还是引用。
- **坐标轴含义**（图表类）：X/Y 各是什么量、单位、刻度范围；曲线/柱/点在表达什么趋势。
- **流程图**：写清方向（自左向右 / 自上而下）和分支条件（菱形上的判断、Yes/No 各通向哪里）。
- **数据图表**：写清量纲与单位，以及趋势（上升/下降/拐点/对比组）。
- **照片类**（`category == "photo"`）：写清画面内容，以及它在前后文中的作用（示意实物、实验装置、界面截图等）。

辅助字段的用法：

- `caption`、`ocr_labels`、`context_before`、`context_after` **只能用来消歧**（确认这是哪一张图、某个缩写指什么）。
- **正文上下文不能代替看图**。没在图上看到的节点、数值、箭头，不许因为前后文「好像该有」就写进去。
- `ocr_labels` 经常缺字、乱序、把坐标轴刻度收进来。它是线索，不是描述草稿。

表格（`kind == "table"`）：对照 `table_markdown` 看结构——表头、单位、合并单元格、脚注。描述的是表在讲什么，不是把格子原文再贴一遍。

---

## `round_trip` 判定（三选一，如实填）

这是合并层的合法值集合。填错会被 `describe-merge` 拒绝，整条不应用。不要发明第四个值。

| 值 | 何时选 | 描述必须覆盖什么 | 明确允许丢失什么 |
|---|---|---|---|
| `reproducible` | 图清晰，结构、标签、分支、数值都能从描述还原 | 全部节点、全部连接、全部可读数值/条件 | 无。选这个等于声称「按描述能重画出同一张信息图」 |
| `partial` | 结构看得清，但像素级样式或精确坐标丢了 | 拓扑、主标签、主路径/主趋势 | 线型/颜色/字体、精确像素坐标、装饰性图标、抗锯齿细节 |
| `not_reproducible` | 图糊、被裁、被挡、分辨率不够，或关键区不可读 | 只写**实际看见**的片段，并写明看不见什么、为什么看不见 | 其余全部。不要补全 |

判定顺序（不要跳）：

1. 关键节点或关键数值看不清 → `not_reproducible`。不要降级成 `partial` 再靠猜测把缺口填上。
2. 结构清楚，但样式/精确坐标无法保证 → `partial`。这是工程图、截图、带装饰的流程图的常见结果。
3. 只有在「按描述重画，信息不丢」时才用 `reproducible`。照片几乎不可能是 `reproducible`（像素不可重建）；照片用 `partial`（画面内容与作用写清）或 `not_reproducible`（糊/裁切）。

示例：

- 流程图框内文字全清晰、箭头方向明确，但线是抗锯齿灰色、圆角半径未知 → `partial`。
- 右半张被页边裁掉，只看见「输入 → 求解器」，输出侧缺失 → `not_reproducible`，描述里写「右缘被裁，输出侧不可见」。
- 坐标轴刻度糊成色块，趋势线还在 → `not_reproducible`（缺量纲/刻度就不是一张可复现的数据图）。不要编刻度。
- OCR 给出 `["输入","求解器","输出"]` 但图上「求解器」框看不清 → 写「求解器框内文字不可读」，`not_reproducible`。**不许把 OCR 三个词串成一句交差。**

---

## 禁止事项

- 图看不清就填 `not_reproducible`，并在描述里说明看不清**什么**（哪一块、是糊、裁切、遮挡还是对比度不够）。**不许编。**
- 不许把 `ocr_labels` 原样拼接当描述交差（`Labels: a; b` 那种正是本层要消灭的假描述）。
- 不许用前后文编造图上没有的节点、数值、分支。
- 不许用 Mermaid `table` 块（Cursor/VS Code/GitHub 内置 Mermaid 不支持，预览会报 Syntax Error）。结构关系用 JSON 的 `entities` / `relations`，或用普通句子。需要画流程时用 `flowchart`/`graph`，不要 `table`。
- 不许一次把全书图片读进上下文。用 `--pending-only --limit`。
- 不许在 Python 侧为「省事」伪造 description。本协议不改 `describe.py` 来编描述。
- 空白 `description`、非法 `round_trip` 会被 merge 拒绝。写完后确认每条都有非空描述和三选一的 `round_trip`。

---

## 响应记录格式

`responses.jsonl`：一行一条 JSON，字段如下。

```json
{"block_id":"p0038-figure-0000","description":"……","round_trip":"partial",
 "entities":["输入","求解器"],"relations":[{"from":"输入","to":"求解器"}],
 "chart_data":null,"model":"<看图的模型名>","generated_at":"2026-08-13T12:00:00Z"}
```

字段约定：

- `block_id`：从 batch.jsonl 原样复制，不要改。
- `description`：自然语言，可多行。写你看见的结构，而不是 caption 复述。
- `round_trip`：`reproducible` | `partial` | `not_reproducible`。
- `entities`：图上的节点/标签列表。没有就 `[]`，不要用 OCR 列表充数。
- `relations`：`{"from":"...","to":"..."}`。没有就 `[]`。
- `chart_data`：仅当图是数据图表且你**读出了**可结构化的点/系列时填写；否则 `null`。不要把看不清的刻度编成数字。
- `model`：实际在看图的模型名。
- `generated_at`：UTC ISO-8601。

merge 成功的标志是 IR 里该 block 的 `meta.description_source == "vlm"`。之后 `--pending-only` 会跳过它。失败的记录进 `describe-report.json` 的 `unknown_ids` / `rejected`，不会被标成已描述——下一轮 status 仍会看到它们。

---

## 单条作业步骤

对 batch.jsonl 里的每一条：

1. 读记录：`block_id`、`kind`、`asset_path`、`caption`、`ocr_labels`、`category`、`context_*`。
2. 打开图片。打不开或文件损坏 → `not_reproducible`，描述写「asset 无法打开：…」。
3. 先看图，再决定要不要看 caption / OCR / 上下文。
4. 按上面的质量规则写 `description`，按三级表选 `round_trip`。
5. 填 `entities` / `relations` / `chart_data`。
6. 把这一行追加到 `responses.jsonl`。不要改已经 merge 过的文件里的旧行。

一批全部写完再 `describe-merge`。不要每写一条就 merge 一次（除非在排障）。
