# TASK P11 —— describe 协议增加 `not_a_figure` 判定：让看图的一方剔除误收

> 实施方：本机 `cursor-agent`（模型 `cursor-grok-4.6-high-fast`）
> 验收方：Claude（独立重跑 + 真实 bundle 端到端）
> 前置：`e51637e`（图内 OCR 已合入，285 passed）。**不许改已有的 45 条 pdf2md 测试**，我存了指纹。

---

## 0. 铁律

1. **禁止任何外发网络**。本批仍是纯 Python 管道活，不调模型。
2. **删除 block 是不可逆操作，必须 fail-closed**：只有拿到**明确判定**才删，
   缺字段、字段非法、拿不准 → **保留**，并如实记进报告。
3. **不许 `try/except` 吞掉异常后继续。**
4. **不许改动现有测试让它们变绿。**
5. **不许伪造数字。**
6. **默认行为不变**：不传新判定时，`document.md` 必须逐字节与现在相同。

---

## 1. 背景：四条纯几何路线全部证伪

我实测过（数字可复现，别推翻）：

| 尝试 | 结果 |
|---|---|
| 文字密度门槛（P10） | 图 132→230、96 页 16.2s→32.8s，**已回退** |
| 「有贝塞尔曲线 = 是图」 | p23 真图 364 条曲线，但**流程图/框图零曲线**，按此会全灭 |
| 按「被已识别表格覆盖」丢弃 | p33 糊块 38.2%、p46 含真图 55.8%、p23 真图 7.9%——**无阈值可分** |
| 图元级剔除表/正文后再聚类 | p23 图元 660 → **剩 1**，真图被剔没 |

**根因**：一个整页糊块里同时含真图与表格，任何"分类"都注定错；
而把它"分割"开，纯几何做不到。

看图的一方（多模态模型）却能一眼分辨 `CAUTION！`提示框不是图。
所以这个判断交给它，Python 侧只负责**忠实执行并留痕**。

---

## 2. 交付物

### 2.1 响应记录新增可选字段 `verdict`

`describe-merge` 读入的 JSONL 记录，增加可选字段：

```json
{"block_id":"p0052-figure-0003", "verdict":"not_a_figure",
 "reason":"正文提示框：CAUTION！The life expectancy is limited！",
 "model":"...", "generated_at":"..."}
```

`verdict` 取值（**只有这两个合法**）：

| 值 | 含义 | merge 行为 |
|---|---|---|
| `figure`（或字段缺失） | 是图 | 走现有描述流程 |
| `not_a_figure` | 不是图（正文框/公式/表格碎片/整页糊块） | **从 IR 删除该 block** |

**`not_a_figure` 时 `description` 不再必填**，但 **`reason` 必填且非空**——
没有理由就是没有依据，按 §0 第 2 条**保留不删**。

### 2.2 `merge_descriptions` 的删除语义

- 命中 `verdict == "not_a_figure"` 且 `reason` 非空 → 从 `ir.blocks` 移除该 block，
  并**删除它的资产文件**（`assets/figures/<...>.png`）
- `verdict` 是其他任意值 → 记入 `rejected.bad_verdict`，**不删不改**
- `not_a_figure` 但 `reason` 空/缺失 → 记入 `rejected.missing_reason`，**不删不改**
- 已经被描述过（`description_source == "vlm"`）的 block 收到 `not_a_figure`
  → 记入 `rejected.already_described`，**不删**（避免先描述后删的自相矛盾）

`describe-report.json` 增加：

```json
"removed_not_a_figure": 3,
"removed_block_ids": ["p0052-figure-0003", "..."],
"rejected": {"...": 0, "bad_verdict": 0, "missing_reason": 0, "already_described": 0}
```

**删除记录必须落盘可审计**：另写 `<bundle>/removed-blocks.jsonl`，
每行一条 `{block_id, page, asset_path, reason, model, removed_at}`。
这是唯一能事后追查"为什么这张图没了"的凭据。

### 2.3 `describe-status` 与 `--pending-only` 的一致性

删除后重算：`total_figures` 应减少，`pending` 相应减少，
再次 `describe-export --pending-only` **不得再导出已删除的 block**。

### 2.4 协议文档同步

`references/pdf2md-describe.md` 与 `.cursor/skills/pdf2md-describe/SKILL.md`
增加 `verdict` 说明，写清判定标准：

- **不是图**：正文提示/警告框、纯公式框、表格或表格碎片、
  页眉页脚装饰、分隔线、整页混合内容（既不是单一图也无法单独描述）
- **是图**：流程图、框图、曲线/柱状/散点图、示意图、照片、电路图、
  **即使它没有 caption、即使它全由直线和矩形构成**
- **拿不准就判 `figure`**（保留），不要删。删错的代价远大于留错。

---

## 3. 测试（加进 `tests/pdf2md/test_describe.py` 末尾，不许动已有的）

19. `not_a_figure` + 非空 `reason` → block 从 IR 移除，资产文件被删，`document.md` 不再含该 block 锚点
20. `not_a_figure` + 空 `reason` → 进 `rejected.missing_reason`，**block 仍在**，资产仍在
21. `verdict` 为非法值（如 `"maybe"`）→ 进 `rejected.bad_verdict`，block 仍在
22. 已描述过的 block 收到 `not_a_figure` → 进 `rejected.already_described`，block 仍在
23. `removed-blocks.jsonl` 每条含 `block_id/page/asset_path/reason/model/removed_at`
24. 删除后 `describe_status` 的 `total_figures` 减少，且 `--pending-only` 不再导出它
25. **默认行为不变**：不带 `verdict` 的记录集，`document.md` 与不传时逐字节相同
26. 混合批次：一批里同时有 `figure` 描述、`not_a_figure` 删除、非法记录，
    三者互不干扰，报告计数正确

---

## 4. 真实 bundle 端到端（必须跑，落盘 `runs/p11/`）

用 `/tmp/p10b_fast`（IEC 96 页，132 张图）复制一份，构造一批**人工判定**记录
（不调模型，手写 JSONL 即可），至少含：

- 3 条 `not_a_figure`（挑 `page-0052-*`、`page-0033-*` 这类）
- 2 条正常描述
- 1 条非法 `verdict`
- 1 条 `not_a_figure` 但 `reason` 为空

跑 `describe-merge`，报告里给出：删除前后 `total_figures`、
`removed-blocks.jsonl` 内容、`describe-report.json` 全文。

---

## 5. 验收命令（我会原样重跑）

```bash
cd /Users/mccree/Desktop/AnCoder/doc2skill
/usr/local/bin/python3 -m pytest tests/ -q          # 必须 293 passed
/usr/local/bin/python3 -m ruff check book_to_skill/pdf2md/
```

---

## 6. 不在本任务范围内（别做）

- 真调多模态模型（后续，且由我跑）
- 任何切图/聚类/门槛的改动（**四条几何路线已证伪，不要再试**）
- `chapters.py` / `handles.py` / `quality.py`
