# TASK P5 —— 图/表 → 自然语言：离线交接层

> 实施方：本机 `cursor-agent`（模型 `cursor-grok-4.6-high-fast`）
> 验收方：Claude（独立重算，不接受你报告里任何未经复现的数字）
> 本文件里所有"现状"数字都是我实测出来的，不是估计。你可以复核，但不要假设它们是错的。

---

## 0. 铁律（违反其一即判负，不看其他成果）

1. **禁止任何外发网络**。被测 PDF 属未授权外发材料。本任务新增的 Python 代码
   **一行网络调用都不许有**——不许 import `requests`/`httpx`/`urllib`/`openai`/`anthropic`。
   多模态识别发生在 Python 之外（cursor 侧），Python 只负责导出请求和合并结果。
2. **判成功看产物内容，不看退出码**。
3. **不许伪造描述**。没有拿到真实描述的图，`description` 必须保持原样，
   `description_source` 必须如实标记。把 OCR 标签拼接冒充自然语言描述 = 判负。
4. **不许 `try/except` 吞掉异常后继续**。
5. **失败是合法结果，掩盖失败比失败本身严重得多。**
6. **不许改动现有测试让它们变绿**。现有 `tests/pdf2md/` 全部必须继续通过。

---

## 1. 环境约束（实测过的坑，别重新踩）

- 用 **`/usr/local/bin/python3`**（3.13.5）。项目 `venv/` 缺依赖，别用。
- PyMuPDF 1.27.2.2 已装在系统 python3，可用。
- 跑测试：`/usr/local/bin/python3 -m pytest tests/pdf2md/ -q`

---

## 2. 现状事实（我已实测，别推翻）

- `book_to_skill/pdf2md/figures.py:104-108` 生成的所谓"描述"是：
  ```python
  description = "Labels: " + "; ".join(labels[:12])   # 或 "OCR labels present"
  ```
  这是 OCR 标签拼接，**不是自然语言**。这是本任务要解决的缺口。
- `book_to_skill/pdf2md/assemble.py:50-52` 把图渲染成光秃秃的 `![alt](path)`。
- `book_to_skill/pdf2md/ir.py:70-87` 的 `FigureBlock` **已经有**
  `description` / `entities` / `relations` / `chart_data` / `round_trip` 字段，
  只是从没被真正填过。**复用它们，不要新建平行结构。**
- 全模块 grep `openai|anthropic|requests|httpx|api_key|vision` 零命中。
  `optimize/net_guard.py` 主动 patch `socket.connect` 封非 loopback。**这个性质必须保持。**

---

## 3. 交付物

### 3.1 新模块 `book_to_skill/pdf2md/describe.py`

两个纯函数 + 两个 IO 入口，**不许有网络**。

#### `export_requests(bundle_dir: Path, *, include_tables: bool = False) -> list[dict]`

读 `<bundle>/document.ir.json`，对每个 `type == "figure"` 的 block 产出一条记录：

```json
{
  "block_id": "p0012-fig-0001",
  "kind": "figure",
  "page": 12,
  "asset_path": "assets/figures/p0012_fig0001.png",
  "caption": "图3-1 工作流示例",
  "ocr_labels": ["输入", "求解器", "输出"],
  "category": "diagram",
  "bbox": [72.0, 100.0, 520.0, 380.0],
  "context_before": "……同页该 block 之前最近的正文，≤400 字",
  "context_after": "……同页该 block 之后最近的正文，≤400 字"
}
```

- `asset_path` 保持 **bundle 相对路径**，不许写绝对路径。
- `include_tables=True` 时额外导出 `type == "table"` 的 block，`kind` 为 `"table"`，
  用 `table.caption`，并把表格的 Markdown 文本放进 `table_markdown` 字段。
- 顺序按 `(page, block_id)` 稳定排序。

#### `merge_descriptions(bundle_dir: Path, records: list[dict], *, strict: bool = False) -> dict`

把描述写回 IR 并重新装配 `document.md`。**fail-closed 规则：**

| 情况 | 处理 |
|---|---|
| `block_id` 在 IR 里不存在 | 记入 `unknown_ids`，不应用 |
| `description` 缺失/空白 | 记入 `rejected.empty`，不应用 |
| `round_trip` 不在 `{reproducible, partial, not_reproducible}` | 记入 `rejected.bad_round_trip`，不应用 |
| 目标 block 不是 figure/table | 记入 `rejected.wrong_type`，不应用 |
| 合法 | 应用 |

应用时写入：`figure.description`、`entities`、`relations`、`chart_data`、`round_trip`，
并在 **block.meta** 里写 `description_source="vlm"`、`description_model=<记录里的 model>`、
`described_at=<记录里的 generated_at>`。

返回并落盘 `<bundle>/describe-report.json`：

```json
{
  "total_figures": 42, "described": 40,
  "unknown_ids": ["..."],
  "rejected": {"empty": 1, "bad_round_trip": 0, "wrong_type": 0},
  "skipped_already_described": 0
}
```

结束时重写 `document.ir.json` 和 `document.md`。
`strict=True` 且存在任何 `unknown_ids` 或 `rejected` 时，调用方返回退出码 2。

### 3.2 改 `assemble.py` 的图渲染

**只有** `block.meta.get("description_source") == "vlm"` 时才追加自然语言块：

```markdown
<!-- block: p0012-fig-0001 -->
![图3-1 工作流示例](assets/figures/p0012_fig0001.png)

> **【图：图3-1 工作流示例】**
> 流程自左向右：输入参数经求解器计算后输出目标值……
```

多行描述每行都要加 `> ` 前缀。**没有 vlm 描述的图，渲染必须和现在一模一样**
（这条有测试守着——OCR 标签拼接的假描述绝不能渲染成引用块）。

表格同理：在表格后追加 `> **【表：<caption>】**` 引用块。

### 3.3 新 CLI 子命令

在 `book_to_skill/pdf2md/cli.py` 里按现有扁平风格加两个：

```
pdf2md describe-export --bundle <dir> [--out <path>] [--include-tables]
pdf2md describe-merge  --bundle <dir> --descriptions <path> [--strict]
```

- `--out` 缺省为 `<bundle>/describe-requests.jsonl`。
- 输入输出都是 **JSONL**（一行一条 JSON），不是 JSON 数组。
- `describe-merge` 打印 `describe-report.json` 的摘要到 stdout。

### 3.4 测试 `tests/pdf2md/test_describe.py`

必须覆盖，一条都不能少：

1. `export_requests` 对每个 figure block 产出一条，`asset_path` 是相对路径
2. `export_requests` 的 `context_before`/`context_after` 取的是同页相邻正文，且截断在 400 字
3. `merge_descriptions` 应用合法记录后，`document.md` 出现引用块，IR 里 `description_source == "vlm"`
4. 未知 `block_id` → 进 `unknown_ids` 且不应用；`strict` 下 CLI 返回 2
5. 空白 `description` → 进 `rejected.empty` 且不应用
6. 非法 `round_trip` → 进 `rejected.bad_round_trip` 且不应用
7. **幂等**：同一份 descriptions 连续 merge 两次，`document.md` 字节级相同
8. **OCR 标签假描述不渲染**：`description="Labels: a; b"` 且无 `description_source`
   的图，渲染结果不含 `>` 引用块
9. `describe.py` 无网络：在 `net_guard.install_guard()` 生效下跑完整 export+merge 不抛 `NetworkBlocked`

用 `tests/pdf2md/fixtures/synthetic` 下已有的合成 PDF 造 bundle，别依赖真实测试文档。

---

## 4. 验收命令（我会原样重跑，你先自己跑通）

```bash
cd /Users/mccree/Desktop/AnCoder/doc2skill
/usr/local/bin/python3 -m pytest tests/pdf2md/ -q
/usr/local/bin/python3 -m ruff check book_to_skill/pdf2md/describe.py
grep -rnE "requests|httpx|urllib|openai|anthropic" book_to_skill/pdf2md/describe.py   # 必须零命中
```

---

## 5. 不在本任务范围内（别做）

- cursor 侧的多模态 skill（下一批）
- 章节索引 `chapters.json`（P2，别提前动）
- 任何对 `quality.py` 评分函数的改动

---

## 6. 第二轮修订（验收未过，必须修）

第一轮 9 条测试全绿、现有 49 条未受影响、离线性质保持——这些都确认了。
但我实测出两个缺陷，都有复现命令，别质疑数字，直接修。

### 6.1 无 caption 的图，alt 被塞进整段描述（严重）

**复现：**
```python
FigureBlock(asset_path="assets/figures/a.png", caption=None, description=<多行VLM描述>)
block.meta = {"description_source": "vlm"}
```
**实际输出：**
```markdown
![流程自左向右：输入参数经求解器计算后输出目标值。 第二行：反馈回路从输出指回输入。](assets/figures/a.png)

> **【图：】**
```
两个毛病：整段描述被复制进 alt 属性；引用块标题空成 `【图：】`。

**影响面实测**：`runs/p4/**/document.ir.json` 共 45 张图，**14 张无 caption（31.1%）**。
约 1/3 的图会踩中。这是常规路径，不是边角。

**要求：**
- 有 VLM 描述时，`alt` **不许**回退到 `description`。无 caption 时 alt 用 `block_id`。
- 引用块标题无 caption 时同样回退到 `block_id`，**不许出现空的 `【图：】`**。
- 表格同理。
- **无 VLM 描述时的渲染必须一字不变**（现有第 8 条测试守着这个，它必须继续绿）。

### 6.2 `describe-report.json` 自身不自洽

**复现**：1 图 + 1 表各给一条合法描述，报告是
`{"total_figures": 1, "described": 2}` —— `described > total_figures`，读的人算不出覆盖率。

**要求**：报告改成分开计数，字段名自己定但必须能算出覆盖率，至少含
图的总数/已描述数、表的总数/已描述数。`rejected` / `unknown_ids` / `skipped_already_described` 保持。

### 6.3 本轮新增测试（加进 test_describe.py，不许动已有 9 条）

10. 无 caption + VLM 描述：alt **不等于** description，且不含换行；引用块标题非空
11. 无 caption + **无** VLM 描述：渲染与修改前逐字节相同（回归守卫）
12. 1 图 + 1 表全部描述成功时，报告字段自洽，能算出图/表各自的覆盖率

### 6.4 验收命令（我会原样重跑）

```bash
/usr/local/bin/python3 -m pytest tests/pdf2md/ -q          # 必须 61 passed
/usr/local/bin/python3 -m ruff check book_to_skill/pdf2md/describe.py book_to_skill/pdf2md/assemble.py
```
