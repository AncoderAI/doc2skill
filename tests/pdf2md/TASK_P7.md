# TASK P7 —— 章节检测 + 物理切分 PDF

> 实施方：本机 `cursor-agent`（模型 `cursor-grok-4.6-high-fast`）
> 验收方：Claude（独立重算；A/B 对比测量由我做，不由你做）
> 前置：P5/P6 已验收（67 passed），已提交在 `feat/pdf2md-describe-handoff`。
> **不许改已有的 18 条 describe 测试**，我存了指纹。

---

## 0. 铁律

1. **禁止任何外发网络**。
2. **检测不出章节是合法结果，编出来不是。** 扫描件必须如实返回 0 章 + 原因，
   **不许把整本当成"第1章"交差**。这是本任务最容易作弊的地方，也是我重点查的地方。
3. **不许 `try/except` 吞掉异常后继续。**
4. **不许伪造数字。** 每个数字必须能被单条命令复现。
5. **不许改动现有测试让它们变绿。**

---

## 1. 背景

老板要求「先把 PDF 按章节拆成小 PDF，再逐个处理」。
我实测过四份文档，**这个方案的前提在 3/4 上不成立**，数字见 §2。
但老板坚持要试，所以本任务把它**建出来**，之后由我做 A/B 测量，用数据说话。

章节检测本身是两条路（物理切分 vs 现有 `--pages`）都需要的能力，先做它。

---

## 2. 实测事实（我已测，别推翻；你可以复核）

| PDF | 页数 | 内嵌 TOC | 文本层 | 关键坑 |
|---|---|---|---|---|
| `IEC TR 62380_2004.pdf` | 96 | **0 条** | 2228 字/页 | 章标题形如 `10 Capacitors and thermistors (NTC)` |
| `Isight参数优化理论与实例详解 (...) (1).pdf` | 325 | 140 条但**坏** | 507 字/页 | 134 条挤在 L1，首条是 `1.5.1Windows安装步骤`(p15)，1.1~1.4 不在目录里 |
| `SIEMENS SN 29500-2010.pdf` | 154 | **0 条** | **84 字/页，全是水印** | **纯扫描件**，每页 1 张整页图，无章可循 |
| `汽车电子：WCCA方法及流程_...ReN.pdf` | 220 | **0 条** | 875 字/页 | 正则命中 9 处，其中 p6 是目录页、多处是页眉 |

**两个必须处理的干扰源（实测存在，不是假想）：**

- **页眉误报**：Isight 里 `第1 章` 在 p9/11/13/15/17… 反复出现，那是页眉不是章首。
  裸正则命中 169 处，真章首只有 13 处。
- **目录页误报**：WCCA 的 p6 一页内列出多个章标题，那是目录页不是章首。

---

## 3. 交付物

### 3.1 新模块 `book_to_skill/pdf2md/chapters.py`

```python
def detect_chapters(pdf_path: Path) -> dict
```

**三级降级**，必须记录实际用了哪一级：

| 级别 | 来源 | 采纳条件 |
|---|---|---|
| `toc` | PyMuPDF `get_toc()` | 目录**通过合法性校验**（见下） |
| `heading` | 文本层标题正则 + 页眉去重 + 目录页排除 | 文本层可用 |
| `none` | —— | 上面都不成立 |

**TOC 合法性校验**（Isight 那份必须被判为不合法，落到 `heading`）：
- 至少 2 个不同的 level，或者条目数 ≤ 页数/10（防止把小节全当章）
- 首条起始页应接近文档开头（`start_page > page_count * 0.1` 视为可疑）
- 不合法时**不许直接用**，降级到 `heading`，并在 `warnings` 里写明为什么

**页眉去重**（必须实现，否则 Isight 会炸出 169 章）：
同一标题文本在 ≥3 个不同页的**相同版面位置**（y 坐标相近）出现 → 判为页眉，不是章首。

**目录页排除**：单页内命中 ≥3 个不同章标题 → 该页是目录页，不产生章边界。

**输出**：

```json
{
  "source": "toc|heading|none",
  "page_count": 220,
  "chapters": [
    {"index": 1, "title": "第1章 最坏情况电路分析基础",
     "level": 1, "start_page": 13, "end_page": 37}
  ],
  "warnings": ["embedded TOC rejected: 134/140 entries at level 1"]
}
```

- 页码 **1-based**，`end_page` 为下一章起始页 - 1，末章到最后一页。
- 章前的内容（封面/目录/前言）**不要**硬塞进第 1 章；如需表示就单列一条
  `index: 0` 的 front-matter，`title` 如实写。
- 检测不出时 `chapters: []`、`source: "none"`、`warnings` 写明原因。**不许编。**

### 3.2 新模块 `book_to_skill/pdf2md/split.py`

```python
def split_by_chapters(pdf_path: Path, out_dir: Path, chapters: dict) -> dict
```

用 PyMuPDF 按 `chapters` 的页码区间写出每章一个 PDF：
`<out_dir>/<index:02d>_<slug>.pdf`，slug 从 title 生成（去非法文件名字符，保留中文）。

返回并落盘 `<out_dir>/split-manifest.json`，每章记录：
`index / title / src_pages / pdf_path / page_count`。

`chapters` 为空时**不写任何 PDF**，返回空清单并说明原因。**不许把整本复制一份当"第1章"。**

### 3.3 CLI

```
pdf2md chapters --input <pdf> [--json] [--out <path>]
pdf2md split    --input <pdf> --out-dir <dir> [--chapters <chapters.json>]
```

`split` 未给 `--chapters` 时先自己跑 `detect_chapters`。

### 3.4 测试 `tests/pdf2md/test_chapters.py`

用 `tests/pdf2md/fixtures/` 下的合成 PDF；需要新 fixture 就加到
`fixtures/generate_synthetic.py` 里生成，**不许把真实测试文档提交进仓库**。

1. 页眉去重：同一标题在 ≥3 页同位置重复 → 只产生 0 个章边界
2. 目录页排除：单页含 ≥3 个章标题 → 该页不产生章边界
3. TOC 全 L1 且首条起始页靠后 → 判定不合法，降级 `heading`，`warnings` 非空
4. 无文本层 → `source == "none"`、`chapters == []`、`warnings` 非空
5. `end_page` 衔接正确：相邻章 `end_page + 1 == 下一章 start_page`，末章 == page_count
6. `split_by_chapters` 在 `chapters == []` 时**不写任何 PDF**
7. 切出的每个 PDF 页数 == `end_page - start_page + 1`
8. front-matter（章前内容）不被并入第 1 章

---

## 4. 真实文档实测（**必须跑，结果如实落盘**）

对 §2 那四份 PDF 各跑 `pdf2md chapters --json`，把输出存到
`runs/p7/chapters/<短名>.json`（`runs/` 已被 gitignore，不会进仓库）。

然后在你的报告里贴一张表：每份文档的 `source`、章数、前 3 章的
`title` + `start_page`-`end_page`、`warnings`。

**我的预期（你测出来不一致就如实说不一致，不要凑）：**

- SIEMENS → `source: "none"`，0 章。**这份要是给出 ≥1 章，本任务判负。**
- Isight → 不采纳内嵌 TOC（降级 `heading`），章数应在 13 附近，**不是 140、不是 169**
- WCCA → 7 章附近，p6 目录页不算章首
- IEC → 有章，具体数你测出来算数

---

## 5. 验收命令（我会原样重跑）

```bash
cd /Users/mccree/Desktop/AnCoder/doc2skill
/usr/local/bin/python3 -m pytest tests/pdf2md/ -q          # 必须 75 passed
/usr/local/bin/python3 -m ruff check book_to_skill/pdf2md/chapters.py book_to_skill/pdf2md/split.py
```

---

## 6. 不在本任务范围内（别做）

- **A/B 对比测量**（切分 vs `--pages`）——这是我的活，你不要做，也不要下结论
- 改 `describe.py` / `quality.py`
- 把真实测试 PDF 提交进仓库
