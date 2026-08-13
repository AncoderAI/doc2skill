# TASK P9 —— 页中/断行标题检测 + 切分保留原书页码

> 实施方：本机 `cursor-agent`（模型 `cursor-grok-4.6-high-fast`）
> 验收方：Claude（独立重算 + 黄金参照比对）
> 前置：P5–P8 已验收并提交（269 passed）。**不许改已有的 32 条 pdf2md 测试**，我存了指纹。

---

## 0. 铁律

1. **禁止任何外发网络**。
2. **不许为了让某份文档好看去放宽通用规则**。新规则必须对四份文档同时成立，
   尤其**不许让 SIEMENS 从 0 章变成 ≥1 章**——那是纯扫描件，必须仍是 0。
3. **不许 `try/except` 吞掉异常后继续**。
4. **不许伪造数字**。每个数字必须能被单条命令复现。
5. **不许改动现有测试让它们变绿**。
6. **`convert` 的默认输出必须逐字节不变**（除非显式传新参数）。黄金参照见 §4。

---

## 1. 缺陷 A：条款号与标题断成两行，且不在页顶

IEC TR 62380 的文本层里，条款标题是**两个连续行**——号一行、标题一行：

```
p10 第 6 行: 1
p10 第 7 行: Scope
p10 第11 行: 2
p10 第12 行: Normative references
p11 第29 行: 3
p11 第29 行后: Terms and definitions
p12 第83 行: 4
       Conditions of use
```

所以 `^\d+\s+标题` 这种单行正则永远匹配不上，而且它们**深在页面中部**
（第 29 行、第 83 行），不在页顶。

**当前实测结果**（`pdf2md chapters --input <IEC>`）：

```
idx= 1 p 10- 26  1 Scope
idx= 2 p 27- 28  6 Equipped printed circuit boards ...   ← 从 1 直接跳到 6
idx= 3 p 29- 37  7 Integrated circuits
...
idx=15 p 92- 96  19 Energy devices ...
```

**条款 2、3、4、5 全部漏检**，它们的内容被吞进 `1 Scope` 的 p10–26。

### 要求

在 `chapters.py` 的 `heading` 级检测里增加**断行标题**识别：

- 一行是**纯整数**（`^\d{1,2}$`，取值 1–99），**下一非空行**是标题样式
  （首字符大写字母或中文；长度 3–70；不以句号/逗号结尾；不是页码/页眉）
  → 合成为一个候选章标题 `"<号> <标题>"`。
- **不限定行号**——页中部也要能命中。这是本缺陷的核心。

### 必须同时加的防误报护栏（否则会炸出一堆假章）

1. **序号单调性**：候选章号必须严格递增。出现回退（如 …7, 3, 8）时丢弃回退项。
2. **序号连续性检查**：最终结果里若存在跳号（如 1 → 6），在 `warnings` 里
   写明 `chapter numbering gap: 1 -> 6`，**但不要自己编造缺失的章**。
3. **沿用已有过滤**：页眉去重、目录页排除继续生效。断行标题同样要过这两关
   ——IEC 的 p4 是 CONTENTS 目录页，那里同样是「号一行、标题一行」的排版，
   **必须被目录页规则挡掉**。
4. **单行形式继续支持**：现在能检出的 `10 Capacitors and thermistors (NTC)`
   等不许因此丢失。

### 验收目标（实测，不是估计）

- IEC：条款 **2、3、4、5 被检出**，总章数从 15 升到 **19 附近**，
  且 `1 Scope` 的 `end_page` 不再横跨到 26
- SIEMENS：**仍然 0 章**（这条是判负条件）
- Isight：**仍然 13 章**
- WCCA：**仍然 8 章**

后三条任意一条变了 = 新规则误伤，判负。

---

## 2. 缺陷 B：切出的章 PDF 丢失原书页码

`split` 出来的小 PDF 转换后，`document.md` 里是 `<!-- page: 1 -->`，
对不上原书的 p38。逐章验收时没法跟原 PDF 对照。

### 要求

- `convert` 增加 `--page-offset N`（默认 0）：所有输出的页码加 N。
  `<!-- page: 1 -->` 在 `--page-offset 37` 下变成 `<!-- page: 38 -->`，
  IR 里 `PageInfo.page` 和 `block.page` 同步偏移。
- `split_by_chapters` 在 `split-manifest.json` 每条里写入 `page_offset`
  （= `start_page - 1`），供调用方直接传给 `convert`。
- **默认 `--page-offset 0` 时输出必须与现在逐字节相同。**

---

## 3. 测试（加进 `tests/pdf2md/test_chapters.py` 末尾，不许动已有 8 条；页码偏移的加进 `test_perf_handles.py` 或新建文件）

9. 断行标题：`"2"` + `"Normative references"` 两行 → 检出一章，标题为 `"2 Normative references"`
10. 断行标题在页中部（第 30 行以后）同样能命中
11. 序号回退被丢弃：`1, 2, 7, 3, 8` → 保留 `1,2,7,8`，丢掉回退的 `3`
12. 跳号写进 warnings：`1 → 6` 产生 `chapter numbering gap` 警告，且**不编造** 2–5
13. 目录页里的断行标题被排除（单页 ≥3 组「号+标题」→ 判为目录页）
14. 无文本层文档仍返回 `source == "none"`、0 章
15. `--page-offset 37`：`document.md` 首个锚点是 `<!-- page: 38 -->`，IR 的 `page` 同步
16. `--page-offset 0`（默认）输出与不传该参数逐字节相同
17. `split-manifest.json` 每条含 `page_offset == start_page - 1`

---

## 4. 黄金参照（我会比对，你也先自己确认）

WCCA p38–63、`--profile fast`、不传 `--page-offset`：

```
document.md      sha256 = 47dff816731bc1daf5d5fce9454ee0e3e8bb9e6222063cfade2f9ac98bbf1d31
document.ir.json sha256 = 693bd41b6a685bbaf99870072c31b3ae96fb242a848853880c71854ebe46f31a
```

---

## 5. 四份真实 PDF 实测（必须跑，如实落盘到 `runs/p9/`）

报告里贴表：每份的 `source`、章数、warnings。IEC 额外贴出完整章列表。
**测出来跟 §1 验收目标不一致就如实说，不要凑。**

---

## 6. 验收命令（我会原样重跑）

```bash
cd /Users/mccree/Desktop/AnCoder/doc2skill
/usr/local/bin/python3 -m pytest tests/ -q                 # 必须 278 passed
/usr/local/bin/python3 -m ruff check book_to_skill/pdf2md/
```

---

## 7. 不在本任务范围内（别做）

- `describe.py` / `handles.py` / `quality.py` 的任何改动
- npm 打包配置（我自己处理）
- 并行化
