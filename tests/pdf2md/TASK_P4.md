# TASK P4 —— 标注基准、图片/公式四步闭环、真实改进回路

> 实施方：本机 `cursor-agent`
> 验收方：Claude（独立重算，不接受你报告里任何未经复现的数字）
> 分三阶段 A → B → C，**每阶段单独验收，A 未通过不得开始 B**。
> 本文件里所有"现状"数字都是我实测出来的，不是估计。你可以复核，但不要假设它们是错的。

---

## 0. 铁律（违反其一即本阶段判负，不看其他成果）

1. **禁止任何外发网络**。被测 PDF 属未授权外发材料。OCR 一律本地 tesseract。
2. **判成功看产物内容，不看退出码**。每一步产物必须自校验非空且结构合法。
3. **不许伪造或估算数字**。每个数字必须由脚本从原始文件算出并落盘，且能被单条命令复现。
4. **不许 `try/except` 吞掉异常后继续**。失败必须如实记为 `{"status":"failed","error":"<类型: 消息>"}`。
5. **失败是合法结果，掩盖失败比失败本身严重得多。**
6. **不许把推断出来的东西标成人工核验过的**。标注数据的每个字段必须带 provenance，见 §A.3。
   把 silver 标成 gold = 判负，这是首轮验收未过的同一病根，不要再犯。
7. **不许为了让分数好看去改评分函数的常数项**。评分只能因为抽取变好而变高。

---

## 1. 环境约束（实测过的坑，别重新踩）

- 用 **`/usr/local/bin/python3`**（3.13.5）。项目 `venv/` 缺依赖，别用。
- pip 必须写成 `timeout 300 /usr/local/bin/python3 -m pip install --timeout 30 --retries 5 <单个包>`，
  多个包分开装；回退清华镜像时必须如实记录用了哪个源。
- `tesseract` 5.5.2 已装（163 语言，含 eng/deu）。**poppler 全家没装，别依赖。**
- 被测文件（只读，勿改勿移）：
  - `/Users/mccree/Desktop/AnCoder/Test/测试文档/IEC TR 62380_2004.pdf`
  - `/Users/mccree/Desktop/AnCoder/Test/测试文档/SIEMENS SN 29500-2010.pdf`

---

## 2. 现状事实（我已实测，见 `runs/p0/pdf_facts.json`）

| | IEC TR 62380 | SIEMENS SN 29500 |
|---|---|---|
| 页数 | 96 | 154 |
| 文本层字符 | 208,978 | 25,410（全是水印乱码） |
| 内嵌位图 | **仅 3 张**（p1/p3 的 116×116 图标） | 154 张整页 jpxdecode 1241×1754 |
| pdfplumber 表格 | 331（71 页有表） | 0 |
| 页旋转 | 95 页 0°，1 页 90° | 全 0° |

**由此得出的两条硬结论，任务书按它写，不要推翻：**

- IEC 的图**几乎全是矢量绘图**。走内嵌图片（XObject）抽取一张也拿不到。
- SIEMENS **每页恰好一张整页扫描图**，"整页图"这个信号零区分度。figure 必须做**页内区域级**检测。

---

## 3. 三个缺陷的确切位置（我已定位，直接改这些地方）

**缺陷 1 —— figure 恒为零（结构性，不是参数问题）**

- `book_to_skill/pdf2md/convert.py:414-420`：figure 只在
  `page_image is not None` 且 `page_type ∈ {IMAGE_BASED, SCANNED, BROKEN_ENCODING}`
  且 **`len(ocr_text.strip()) < 80`** 时产出整页图。
  即**只有 OCR 失败时才有图**。IEC 走原生文本路径 `page_image is None`，永远不进这个分支。
- `book_to_skill/pdf2md/figures.py:19` 的 `crop_and_save` **全仓库零调用点**。接口留好了，没人用。

**缺陷 2 —— formula 恒为零**

- `book_to_skill/pdf2md/cli.py:182`：`_write_generated_corpus` 把 profile 硬编码成 `"fast"`，
  而 `profiles.py:47` 的 `fast` 档 `enable_formulas=False`。**裸 PDF 跑 benchmark 时公式功能整个是关的。**
- 即使打开：`figures.py:14` 的 `_FORMULA_HINT` 只认 `$...$` / `\frac{` / `\sum` 等 LaTeX 字面量和少数 unicode 数学符号。
  PDF 文本里不会出现 `$...$`。`convert.py:400-411` 把非 `$` 命中一律走 `formula_failure`，
  而 `quality.py:143` 的 `formula_score = 15*(ok_f/len(formulas))` 对失败项计 0 分。
  **所以盲目打开 `enable_formulas` 会让分数不升反降。这是陷阱，别踩。**

**缺陷 3 —— 评分有 28 分是冻结常数**

`book_to_skill/pdf2md/quality.py:126-156` `_score_dimensions`：

| 分项 | 满分 | 当前实际 | 为什么恒定 |
|---|---|---|---|
| figures | 20 | **恒 5.0** | `min(20, 5 + figures*3)`，figures 恒 0 |
| formulas | 15 | **恒 8.0** | `if formulas else 8.0`，formulas 恒空 |
| heading_order | 10 | **恒 10.0** | `10.0 if headings>0 or len(blocks)>0`，永真 |
| integrity_offline | 5 | **恒 5.0** | 三个硬门都过就给满 |
| **合计冻结** | | **28/100** | |

只有 text_ocr(25) 和 tables(25) 会动。`eval/__init__.py:83-118` 的 `score_against_truth` 同病：
truth 字段缺失时逐项回落到 `12.5/5.0/1.0/8.0` 常数。而 `cli.py:179-190` 生成的 manifest
**从来不写 `truth` 字段** → `benchmark.py:40` `truth = doc.get("truth", {})` 取空 → 回落到 `_score_dimensions`。
**这就是"占位基准"的确切机制。**

**缺陷 4 —— optimize 只有参数搜索**

`book_to_skill/pdf2md/optimize/search.py:12-30` `SEARCH_DIMS` 是 17 条固定单参数扰动，没有改进回路。
且因为 28 分冻结，`rank_candidates` 几乎没有可排序的信号——
实测 `runs/pdf2md/scores/20260811T164127Z.json` 里 4 个候选**总分全是 36.5**，
`winner: null, reason: "no_improvement"`。这不是"没有更好的候选"，是**度量分辨率为零**。

---

# 阶段 A —— 建立可信标注基准（先做这个）

> 为什么 A 在 B 前面：B 做完了也没法证明它变好了。度量仪器必须先校准。
> 你的老板要的是"人工标注基准"，A 的产物就是它。

## A.1 锚点测量（这是基准的骨架，必须先量）

标准文档自带可验证锚点：图题、表题、公式编号。**它们从文档自身导出，不是我们发明的。**

新建 `runs/p4/anchors.json`，对两份 PDF 各自逐页统计：

```json
{
  "<文件名>": {
    "per_page": {
      "<页号>": {
        "figure_captions": [{"label": "Figure 12", "line": "<原文整行>", "y": <float>}],
        "table_captions":  [{"label": "Table 5",  "line": "...", "y": <float>}],
        "equation_numbers":[{"label": "(14)",     "line": "...", "y": <float>}]
      }
    },
    "totals": {"figure_captions": <int>, "table_captions": <int>, "equation_numbers": <int>},
    "source": "text_layer|ocr",
    "patterns_used": ["<你实际用的正则原文>"]
  }
}
```

- IEC 走文本层。SIEMENS 无可用文本层，走 300dpi 渲染 + tesseract `deu+eng`，`source` 如实写 `ocr`。
- 图题正则至少覆盖：`Figure\s+\d+`、`Fig\.\s*\d+`、`Bild\s+\d+`、`Abbildung\s+\d+`。
  表题至少覆盖 `Table\s+\d+`、`Tabelle\s+\d+`。公式编号至少覆盖行尾独立 `(\d+)` 且该行含数学符号。
- **`patterns_used` 必须是你实际跑的正则原文**，我要用它复现。
- 命中数为 0 也是合法结果，但要在报告里说清是"文档真没有"还是"正则没覆盖到"，并给出你据以判断的证据。

## A.2 分层抽样（抽样必须钉死，不许运行时随机）

新建 `runs/p4/goldset/sample.json`，每份文档选 **20 页**，覆盖以下层，每层至少 2 页：

- IEC：有表页 / 有图题页 / 有公式编号页 / 纯正文页 / **那 1 页 90° 旋转页（必选）** / 目录或索引页
- SIEMENS：德文为主页 / 英文为主页 / 含表格视觉结构页 / 含图页 / 纯正文页 / 封面或版权页

```json
{"<文件名>": {"pages": [<20 个页号>], "strata": {"<页号>": "<层名>"}, "selection_rule": "<你的确定性选择规则原文>"}}
```

`selection_rule` 必须是确定性的（例如"每层按页号升序取前 N 页"），我会用它复现出同一组页号。

## A.3 逐页标注（**provenance 是这一阶段的命门**）

`runs/p4/goldset/<DOC>/page-<NNNN>.json`，每个抽样页一个文件：

```json
{
  "page": <int>,
  "text": "<该页应有的正文全文>",
  "blocks": [{"type": "heading|text|table|figure|formula", "order": <int>, "bbox": [x0,y0,x1,y1]}],
  "tables":   [{"rows": <int>, "cols": <int>, "caption": "<str|null>", "bbox": [...]}],
  "figures":  [{"caption": "<str|null>", "category": "chart|diagram|photo|other", "bbox": [...]}],
  "formulas": [{"number": "<str|null>", "latex": "<str|null>", "bbox": [...]}],
  "provenance": {
    "text":     {"level": "gold|silver", "method": "<str>", "agreement": <float|null>},
    "tables":   {"level": "...", "method": "...", "agreement": null},
    "figures":  {"level": "...", "method": "...", "agreement": null},
    "formulas": {"level": "...", "method": "...", "agreement": null}
  },
  "verified_by": "human|dual-derivation|null",
  "verified_at": "<ISO8601|null>"
}
```

**provenance 规则，逐字执行：**

- `gold` = 人工逐字核对过。**只有人真的看过才能写 gold。你不是人，你不能给自己签 gold。**
- `silver` = **两条互相独立的方法各自导出后一致**。必须记 `method`（两条方法都写清）和 `agreement`（一致度）。
  - IEC 的 text：pdfplumber 文本层 vs 300dpi 渲染 + tesseract OCR，CER ≤ 0.05 记 silver，`agreement` 填 `1-CER`。
  - SIEMENS 的 text：**没有文本层，两条 OCR 配置（psm3@300dpi vs psm6@400dpi）不是真正独立**。
    一致也只能记 silver，且必须在 `method` 里写明 `"weak-independence: both tesseract"`。**不许因此升级成 gold。**
- 两法不一致的页/字段 → `level` 记 `silver`、`agreement` 记实际值，并进 §A.4 的待人工清单。
- `verified_by` 只有人工核过才写 `human`；否则写 `dual-derivation` 或 `null`。**默认 null。**

## A.4 人工核验入口（你搭台，人来核）

`runs/p4/goldset/<DOC>/review.md` —— 让人能翻着看的核验清单：

- 每个抽样页一节，嵌入该页 300dpi 渲染图（相对路径，落在 `runs/p4/goldset/<DOC>/renders/`）
- 并排列出你标注的 text / tables / figures / formulas
- **两法不一致的字段用 `> ⚠️ 需人工判定：<两法各自的结果>` 显式标出，排在每节最前面**
- 末尾一节 `## 待人工核验汇总`，按"不一致字段数"降序列出页号，人按这个顺序核最省时间

`runs/p4/goldset/verify.py` —— 人核完一页后跑
`/usr/local/bin/python3 runs/p4/goldset/verify.py <DOC> <页号> --field text --level gold`
把该字段升级为 gold 并写入 `verified_by: "human"` + `verified_at`。
**这个脚本不许有"全部升级为 gold"的批量开关。** 一次一页一字段，逼着人真的看。

## A.5 评分改成 fail-closed（**这条是 A 阶段的核心交付**）

改 `book_to_skill/pdf2md/eval/__init__.py` 的 `score_against_truth`：

- **删掉所有"truth 缺失就给常数"的回落分支**（当前 89/93/97/102/106 行的 `12.5 / 5.0 / 1.0 / 8.0`）。
- 某分项没有 gold/silver 标注 → 该分项记 `null`，**不计入总分，同时把满分从分母里扣掉**。
- 报告必须新增字段：

```json
{"scored_dimensions": ["text_ocr", "tables"], "unscored_dimensions": ["figures", "formulas"],
 "max_possible": <int>, "total_raw": <float>, "total_normalized_100": <float>,
 "truth_coverage": {"pages_annotated": <int>, "pages_total": <int>,
                    "gold_fields": <int>, "silver_fields": <int>}}
```

- **总分必须同时报 `total_raw`（实得/可得）和 `max_possible`。** 禁止把"没量到"粉饰成"满分"或"及格"。
- `quality.py:_score_dimensions` 保留，但改名为 `heuristic_scores` 并在报告里标 `"kind": "heuristic_no_truth"`，
  **不得再作为 `total_score` 的来源**。它是无标注时的诊断信号，不是成绩。

## A.6 A 阶段自检清单（提交前自己跑，结果写进报告）

- [ ] `runs/p4/anchors.json` 的 `per_page` 键数分别 == 96 / 154
- [ ] `runs/p4/goldset/sample.json` 每份文档恰好 20 页，每层 ≥2 页，IEC 含那页 90° 旋转页
- [ ] 每个抽样页都有 `page-<NNNN>.json`，且 `json.load` 通过
- [ ] **全仓库 grep 不到任何 `verified_by": "human"` 是脚本自动写入的**（人工升级只能来自 `verify.py` 单页调用）
- [ ] `score_against_truth` 里不存在无标注回落常数（我会 AST 扫）
- [ ] 对一份**完全无标注**的 manifest 跑 benchmark，输出 `total_raw=0 / max_possible=0 / unscored_dimensions` 五项齐全，**不报一个好看的分数**
- [ ] `ruff check --select E9,F book_to_skill/ tests/` 无错
- [ ] `/usr/local/bin/python3 -m pytest tests/ -q` 全绿（不许打挂现有测试）
- [ ] `bash runs/p4/reproduce_a.sh` 从零复现 A.1–A.3 全部产物

## A.7 A 阶段报告 `runs/p4/A_REPORT.md`

每个数字后附产生它的确切命令。末尾必须有 `## 我没能做到的`，如实列出。
**这一节为空且实际有失败项 = 隐瞒 = 判负。**
另需一节 `## 需要人来做的`，写清还剩多少页多少字段等着人核，以及核完预计能把 truth_coverage 提到多少。

---

# 阶段 B —— 图片与公式四步闭环（A 验收通过后才开始）

四步闭环的形状**对齐已有的表格闭环**（`extract_tables_pdfplumber` → `table_to_markdown`
→ `parse_markdown_table` → 比对，回读那步在 `eval/__init__.py:71-79`）：

**① 检出 → ② 落地 → ③ 回读 → ④ 比对**

## B.1 figure 四步

1. **检出**：产出带 `bbox` + `route` 的候选。三条路由都要实现，`route` 字段如实记来源：
   - `vector`：原生页用 pdfplumber 的 `curves`/`lines`/`rects` 按邻近聚类成区域。
     **必须扣掉已被 table bbox 认领的区域和贯穿全宽的细分隔线**，否则 IEC 的 331 个表会全被误报成图。
   - `raster`：内嵌 XObject（IEC 仅 3 张，SIEMENS 154 张整页——**整页那种直接丢弃，见下**）。
   - `region`：扫描页在渲染图上做连通域/投影分析，取"墨迹密集但 OCR 无词"的块。
   - **硬规则：bbox 面积 ≥ 页面 92% 的候选一律丢弃并记 `dropped: full_page`。** 否则 SIEMENS 会每页假报一张图。
2. **落地**：调 `figures.py:19` 的 `crop_and_save`（**它现在零调用点，就是给你留的**）写 PNG 到
   `assets/figures/`，同时写 IR `FigureBlock`。资产必须可解码、≥32×32、非纯色。
3. **回读**：从 `document.md` 重新解析 `![alt](path)`，还原出 `{alt, path}` 集合。
4. **比对**：
   - 回读集合 == IR 中 figure 集合（路径与顺序都要对），不一致记 `round_trip: "failed"` + 原因
   - **锚点对账**：本页检出数 vs `runs/p4/anchors.json` 的 `figure_captions` 数，差异写入报告
   - 题注绑定：bbox 邻近行匹配到 `Figure N`/`Bild N` 的，写进 `FigureBlock.caption`

## B.2 formula 四步

1. **检出**：**先修 `cli.py:182` 的 `profile: "fast"` 硬编码**（改成可配，默认跟随 `--profile`）。
   检出不许只靠 `$...$`——PDF 文本里没有 `$`。要按**数学排版特征**判定：
   上下标密度、`=`/`≤`/`×` 等算符、希腊字母（IEC 全篇 λ、π）、行尾独立编号 `(N)`。
2. **落地**：能转 LaTeX 的写 `latex`；转不出的**必须裁图存 `assets/formulas/` 并写
   `failed: true` + `failure_reason`**（当前 `convert.py:402` 传的是 `asset=None`，是个洞，补上）。
3. **回读**：从 `document.md` 重新解析 `$$...$$` 和 `<!-- formula_failed: ... -->`。
4. **比对**：`normalize_latex` 后 token 序列与 IR 一致；失败项也必须能被回读到（失败不许消失）。

## B.3 评分口径（**这条防止你用注水数据刷分**）

- `figures`/`formulas` 两个分项只在该页有 gold/silver 标注时才计分，口径 = **F1（bbox IoU ≥ 0.5 算命中）**，不是"有就给分"。
- **`formula_score` 改成对 `failed` 项计部分分**（有裁图 + 有明确失败原因 = 0.3 分权重）。
  理由：如实报告失败必须优于假装没有公式。当前口径下诚实失败得 0 分，会诱导实现方隐瞒。
- 阶段 B 通过条件：两份文档 `figures`/`formulas` 从 `unscored` 变成有真实 F1 数字（**F1 是多少都行，
  但必须是量出来的**），且总分构成里冻结常数 = 0。

---

# 阶段 C —— 真正的改进回路（B 验收通过后才开始）

保留 `SEARCH_DIMS` 参数搜索，在其上加两条按文档类型分流的回路：

- **IEC（有文本层）走"与参照工具逐节 diff"**：对每一节，把本引擎输出与 pdfminer/pdfplumber/pypdf/MarkItDown
  的输出对齐比对，定位缺失段落/串行/表格漏抽，产出 `runs/p4/diff/<DOC>/<section>.json` 指明差异类型，
  再由回路针对差异类型生成候选修正。
  **参照工具只作为差异证据，不作为 ground truth**（`teachers.py:1` 的注释已经写了这条，遵守它）。
- **SIEMENS（扫描件）走"闭环校验失败项驱动"**：AnyDoc 对它直接 `unsupported`、MarkItDown 只吐 12.6KB 重复水印，
  **扫描件上不存在可蒸馏的参照对象**。所以只能用 B 阶段四步闭环的失败项（round_trip failed / 锚点对不上 /
  资产非法）作为修正信号。
- 每轮必须落盘 `runs/p4/optimize/<ts>/round-<N>.json`：改了什么、为什么改、改前改后各分项、是否被接受。
- **拒绝回归**：任一分项下降超过 `max_dim_drop` 即拒绝，沿用 `search.py:74-79` 的既有逻辑。

---

## 4. 我的验收方式（提前告知，别抱侥幸）

1. 用我自己的脚本独立重算 A.1 锚点、A.3 每个标注页的字段，与你的 JSON 逐字段比对。**任何一项对不上即打回。**
2. AST 扫描：`score_against_truth` 里若还有无标注回落常数 → 判负。
3. AST 扫描：`except` 块内出现 `pass` / `return False` / `return None` 而未重新抛出或未记录到产物 → 判负。
4. **provenance 审计**：随机抽 5 个标称 `silver` 的字段，我自己跑那两条方法验一致度；
   对不上即判负。任何 `gold` 若无对应 `verify.py` 单页调用痕迹 → 判负。
5. 在守卫开启下手动发起一次外部连接，必须抛异常；再用 `sandbox-exec` 断网跑一遍全流程。
6. 跑 `reproduce_a.sh`，比对 sha256。
7. 检查报告的「我没能做到的」是否与实际失败项一致。

---

## 5. 明确不要做的事

- 不要在 A 阶段写任何 figure/formula 抽取代码（B 的活）
- 不要动 `net_guard.py` 的拦截逻辑、`pyproject.toml`、`package.json`、`.github/`
- 不要为了让数字好看去调参或改评分常数
- 不要把两条 tesseract 配置的一致当成"独立验证"（见 A.3）
- 不要一次做完 A+B+C 再来交，**A 做完就停下等验收**
