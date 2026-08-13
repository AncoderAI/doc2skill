# TASK P6 —— cursor 侧多模态 skill + 断点续跑

> 实施方：本机 `cursor-agent`（模型 `cursor-grok-4.6-high-fast`）
> 验收方：Claude（独立重算，不接受你报告里任何未经复现的数字）
> 前置：P5 已验收通过（61 passed）。**不许改 P5 已有的 12 条测试**，我存了指纹。

---

## 0. 铁律（违反其一即判负）

1. **禁止任何外发网络**。新增 Python 代码一行网络调用都不许有。
2. **判成功看产物内容，不看退出码**。
3. **不许伪造描述**。看不清的图必须如实标 `not_reproducible` 并说明原因，
   **不许猜、不许用 OCR 标签拼一个交差**。这是整个 P5/P6 的立身之本。
4. **不许 `try/except` 吞掉异常后继续**。
5. **不许改动现有测试让它们变绿**。

---

## 1. 背景：为什么要分批

P5 已经打通 `describe-export → (多模态) → describe-merge`。
但一本书可能有几百张图，**一次全塞进多模态 agent 的上下文会爆**。
老板担心的"上下文过长导致质量下降"，真正适用的就是这一步（不是 PDF 转换那步——
那步是纯确定性 Python，没有上下文概念）。

所以批次控制必须做成**确定性的、可测的 Python 能力**，而不是靠 agent 自觉。

---

## 2. 交付物

### 2.1 `describe.py` 增强

#### `export_requests(...)` 增加两个参数

```python
def export_requests(
    bundle_dir: Path, *, include_tables: bool = False,
    pending_only: bool = False, limit: int | None = None,
) -> list[dict]:
```

- `pending_only=True`：跳过 `block.meta.get("description_source") == "vlm"` 的 block，
  即只导出**还没描述过的**。这是断点续跑的基础。
- `limit=N`：截断到前 N 条（在既有 `(page, block_id)` 排序之后截）。`None` = 不限。
- 两者可组合。默认值保持现状，**P5 的 12 条测试必须继续绿**。

#### 新函数 `describe_status(bundle_dir: Path) -> dict`

不修改任何文件，只读 IR 统计：

```json
{
  "total_figures": 120, "described_figures": 40, "pending_figures": 80,
  "total_tables": 15,  "described_tables": 0,  "pending_tables": 15,
  "done": false
}
```

`done` 为真当且仅当 pending_figures 和 pending_tables 都为 0。

### 2.2 CLI

```
pdf2md describe-export --bundle <dir> [--out <path>] [--include-tables]
                       [--pending-only] [--limit N]
pdf2md describe-status --bundle <dir> [--json]
```

`describe-status` 无 `--json` 时打印人读摘要，有则打印 `describe_status()` 的 JSON。

### 2.3 cursor skill：`.cursor/skills/pdf2md-describe/SKILL.md`

标准 skill 格式（`---` frontmatter 带 `name` / `description`），内容必须包含：

**循环协议**（这是骨架，照写）：

```
1. pdf2md describe-status --bundle <dir> --json     # 看还剩多少
2. pdf2md describe-export --bundle <dir> --pending-only --limit 20 --out batch.jsonl
3. 逐条读 batch.jsonl：按 asset_path 打开图片，看图写描述
4. 写 responses.jsonl
5. pdf2md describe-merge --bundle <dir> --descriptions responses.jsonl
6. 回到 1，直到 done: true
```

**描述质量规则**（写进 skill，agent 照做）：

- 描述必须包含：**节点/标签**、**连接关系**、**坐标轴含义**（图表类）
- 流程图要写清方向（自左向右 / 自上而下）和分支条件
- 数据图表要写清 X/Y 轴的量纲与单位，以及趋势
- 照片类（`category == "photo"`）写清画面内容与它在文中的作用
- 利用记录里的 `caption` / `ocr_labels` / `context_before` / `context_after` 辅助判断，
  **但正文上下文只能用来消歧，不能拿来代替看图**

**`round_trip` 判定标准**（三选一，如实填）：

| 值 | 含义 |
|---|---|
| `reproducible` | 依描述可重画出结构与全部信息 |
| `partial` | 结构可复现，但像素级样式/精确坐标丢失 |
| `not_reproducible` | 图糊/被裁/信息不足，描述只能覆盖片段 |

**禁止事项**：

- 不许用 Mermaid `table` 块（Cursor/GitHub 预览会报 Syntax Error，社区提案未被支持）
- 图看不清就填 `not_reproducible` 并在描述里说明看不清什么，**不许编**
- 不许把 `ocr_labels` 原样拼接当描述交差

**响应记录格式**（一行一条 JSON）：

```json
{"block_id":"p0038-figure-0000","description":"……","round_trip":"partial",
 "entities":["输入","求解器"],"relations":[{"from":"输入","to":"求解器"}],
 "chart_data":null,"model":"cursor-grok-4.6-high-fast","generated_at":"2026-08-13T12:00:00Z"}
```

### 2.4 测试（加进 `tests/pdf2md/test_describe.py` 末尾，不许动已有 12 条）

13. `pending_only=True` 跳过已描述的 block：先 merge 一半，再 export 只剩另一半
14. `limit=N` 截断到 N 条，且截的是排序后的前 N 条
15. `pending_only` + `limit` 组合正确
16. `describe_status` 计数正确，且**不修改任何文件**（调用前后 bundle 内所有文件 mtime+内容不变）
17. 全部描述完后 `describe_status()["done"] is True`
18. `describe-status --json` CLI 退出码 0 且输出可 JSON 解析

### 2.5 skill 自校验

`.cursor/skills/pdf2md-describe/SKILL.md` 必须能通过项目已有的
`tools/validate_skill.py`（先读它，按它的要求写 frontmatter）。
如果那个校验器不适用于 cursor skill 格式，如实说明并跳过，**不要改校验器**。

---

## 3. 验收命令（我会原样重跑）

```bash
cd /Users/mccree/Desktop/AnCoder/doc2skill
/usr/local/bin/python3 -m pytest tests/pdf2md/ -q          # 必须 67 passed
/usr/local/bin/python3 -m ruff check book_to_skill/pdf2md/describe.py
test -f .cursor/skills/pdf2md-describe/SKILL.md && echo SKILL存在
```

---

## 4. 不在本任务范围内（别做）

- 章节索引 `chapters.json`（P2）
- 真去调用多模态模型（skill 是给 cursor agent 读的说明，不是可执行程序）
- 改 `quality.py` 评分函数
