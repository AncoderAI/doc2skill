---
name: pdf2md-describe
description: "Describe figures and tables in a pdf2md bundle by looking at images, writing JSONL responses, and merging them back. Batch size is controlled by deterministic CLI flags (--pending-only / --limit), not by dumping every asset into context. Use when the user asks to describe pdf2md figures, run describe-export / describe-merge / describe-status, or resume a describe checkpoint."
---

# pdf2md-describe

把 pdf2md bundle 里的图/表写成自然语言描述。Python 只导出请求、合并结果、统计进度；**看图写描述是你这一侧的事**。

**完整协议（描述质量规则、`round_trip` 三级判定表与判定顺序、响应字段约定、单条作业步骤）见
[`references/pdf2md-describe.md`](../../../references/pdf2md-describe.md)（仓库根目录起算）。开工前先读它。**

下面是不读完整协议也绝不能违反的部分。

## 循环协议

```
1. pdf2md describe-status --bundle <dir> --json     # 看还剩多少
2. pdf2md describe-export --bundle <dir> --pending-only --limit 20 --out batch.jsonl
3. 逐条读 batch.jsonl：按 asset_path 打开图片（<bundle>/<asset_path>），看图写描述
4. 写 responses.jsonl
5. pdf2md describe-merge --bundle <dir> --descriptions responses.jsonl
6. 回到 1，直到 done: true
```

`pdf2md` = `python3 -m book_to_skill.pdf2md.cli`。

## 铁律

- **不许编。** 图看不清就填 `round_trip: not_reproducible`，并在描述里写明看不清**什么**（哪一块、糊/裁切/遮挡/对比度）。
- **不许把 `ocr_labels` 拼接当描述交差**（`Labels: a; b` 那种正是本层要消灭的假描述）。
- **不许用前后文编造图上没有的节点、数值、分支。** 上下文只能消歧，不能代替看图。
- **`round_trip` 只有三个值**：`reproducible` | `partial` | `not_reproducible`。填别的会被 merge 整条拒绝。
- **控量必须改 `--limit` 数字**，不许靠自己少读几行 JSONL 假装控量。
- **不许用 Mermaid `table` 块**（Cursor/VS Code/GitHub 内置 Mermaid 不支持，预览报 Syntax Error）。
- 空白 `description` 会被 merge 拒绝。被拒的记录不会被标成已描述，下一轮 `--pending-only` 仍会捞到它们。
- **`verdict` 只有两个合法值**：`figure`（或缺省）走描述；`not_a_figure` 且 **非空 `reason`** 才从 IR 删除该 figure 及其资产。缺字段、非法值、已描述过 → **保留**，记入 `rejected`。
- **拿不准就判 `figure`**。删错的代价远大于留错。
- **不是图**：正文提示/警告框、纯公式框、表格或表格碎片、页眉页脚装饰、分隔线、整页混合内容。
- **是图**：流程图、框图、曲线/柱状/散点图、示意图、照片、电路图——即使没有 caption、即使全由直线和矩形构成。
- 删除会追加 `<bundle>/removed-blocks.jsonl`（`block_id/page/asset_path/reason/model/removed_at`）。没有这条记录就没有删除依据。
