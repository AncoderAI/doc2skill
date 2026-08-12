# TASK P0 —— 基线固化与环境事实测定

> 实施方：本机 `cursor-agent`（Grok 4.5）
> 验收方：Claude（独立重算，不接受你报告里的任何未经复现的数字）
> 本阶段**只做测量，不做实现**。任何抽取内核代码属于 P1，本阶段写了也不计分。

---

## 0. 铁律（违反其一即本阶段判负，不看其他成果）

1. **禁止任何外发网络转换 API**。Firecrawl / AnyDoc 托管端点 / 任何云 OCR 一律禁止。被测 PDF 属未授权外发材料。
2. **判成功看产物内容，不看退出码**。你自己的命令返回 0 不代表成功；每一步产物必须自校验非空且结构合法。
3. **不许伪造或估算数字**。所有数字必须由脚本从原始文件算出并落盘；报告里的每个数字都要能被单条命令复现。
4. **不许 `try/except` 吞掉异常后继续**。测量脚本遇到失败必须把失败如实记录为 `{"status":"failed","error":"<类型: 消息>"}`，不得静默跳过、不得用空字符串顶替。
5. **失败是合法结果**。某个工具跑不通就如实记 `unsupported` / `failed` 并写清原因。**掩盖失败比失败本身严重得多。**
6. 不得修改 `book_to_skill/` 下任何现有文件，不得改 `pyproject.toml` / `package.json` / `.github/`。本阶段只允许新建下列指定路径。

---

## 1. 环境约束（实测过的坑，别重新踩）

- 用 **`/usr/local/bin/python3`**（3.13.5）。项目 `venv/` 缺依赖，别用。
- 本机到境外源网络不稳。所有 pip 必须写成：
  `timeout 300 /usr/local/bin/python3 -m pip install --timeout 30 --retries 5 <单个包>`
  **多个包分开装**。直连连续失败可回退清华镜像，但**必须在 `env_probe.json` 里如实记录用了哪个源**。
- `tesseract` 二进制已装。**poppler 全家（pdftotext/pdfinfo/pdfimages/pdftoppm）没装，别依赖它。**
- 被测文件（只读，勿改勿移）：
  - `/Users/mccree/Desktop/AnCoder/Test/测试文档/IEC TR 62380_2004.pdf`
  - `/Users/mccree/Desktop/AnCoder/Test/测试文档/SIEMENS SN 29500-2010.pdf`

---

## 2. 交付物（路径与结构不可更改）

### 2.1 `book_to_skill/pdf2md/optimize/net_guard.py`

唯一允许在 P0 写的产品代码。要求：

```python
def install_guard(allow_loopback: bool = True) -> None:
    """патч socket.socket.connect：非回环地址一律 raise NetworkBlocked。"""
def is_active() -> bool: ...
class NetworkBlocked(RuntimeError): ...
```

- 必须真正拦截，不是打个日志就放行。
- 配套 `tests/pdf2md/test_net_guard.py`：**必须包含一个正向证明** —— 守卫开启后对一个外部地址发起 connect，断言抛出 `NetworkBlocked`；再断言 `127.0.0.1` 仍可连。不许用 mock 假装拦截成功。

### 2.2 `runs/p0/env_probe.json`

```json
{
  "python": {"executable": "...", "version": "..."},
  "binaries": {"tesseract": "<path|null>", "pdftotext": "<path|null>",
               "pdfinfo": null, "pdfimages": null, "pdftoppm": null,
               "gs": "...", "qpdf": null, "mutool": null},
  "tesseract_langs_count": <int>, "tesseract_has": {"eng": true, "deu": true, "chi_sim": true},
  "libs": {"<名>": {"installed": bool, "version": "<str|null>", "license": "<str|null>"}},
  "pip_index_used": "pypi|tsinghua|none",
  "net_guard_active": true
}
```

`libs` 必须覆盖：`pypdfium2, pdfplumber, pdfminer.six, pypdf, markitdown, PyMuPDF, Pillow, numpy, pandas, pytest`。
**`license` 字段必填**，从 `importlib.metadata` 取（`License` 字段为空时回落到 `Classifier` 里的 `License ::` 行）。已装的不要重装。

### 2.3 `runs/p0/pdf_facts.json`

对**两份 PDF 各自**测出下列事实。全部用已装库算，**不许估算**：

```json
{
  "<文件名>": {
    "page_count": <int>,
    "file_size_bytes": <int>,
    "text_layer_chars_total": <int>,
    "text_layer_chars_per_page": [<每页字符数, 长度=page_count>],
    "embedded_images": [{"page": <int>, "count": <int>,
                         "sizes": [[w, h], ...], "formats": ["jpx", ...]}],
    "page_rotation": {"<角度>": <页数>},
    "tables_pdfplumber": {"pages_with_tables": <int>, "total": <int>,
                          "per_page": {"<页号>": <int>}},
    "tables_pymupdf": {"...同上，若 PyMuPDF 未装则 null..."},
    "font_size_histogram": {"<字号>": <字符数>},
    "repeated_line_candidates": [{"text": "...", "pages": <出现页数>}]
  }
}
```

- `repeated_line_candidates`：出现在**超过半数页面**的行（页眉页脚与水印的候选）。按出现次数降序取前 10。这条直接决定 P1 的水印剔除，必须准。
- `tables_pdfplumber` 与 `tables_pymupdf` **两者都要**（PyMuPDF 未装就装上——它只用于测量侧，不进产品依赖）。**两者数字不一致是预期的，如实记录，不要试图调参让它们对齐。**

### 2.4 `runs/p0/osd_probe.json`

对 SIEMENS **全部页**做 tesseract 方向探测（`--psm 0`），记录：

```json
{"<页号>": {"orientation_deg": <int>, "rotate": <int>, "orientation_conf": <float>,
            "script": "<str>", "script_conf": <float>}}
```

外加汇总 `{"rotate_distribution": {"0": <n>, "90": <n>, ...}}`。
渲染用 **pypdfium2**（不要用 PyMuPDF，产品侧要走 pypdfium2）。渲染 dpi 固定 300。

### 2.5 `runs/p0/baselines/<DOC>/<TOOL>.{txt,meta.json}`

`<DOC>` ∈ {`IEC`, `SIEMENS`}，`<TOOL>` ∈ {`markitdown`, `pdfminer`, `pypdf`, `pdfplumber`, `anydoc`}。

**全部在 `net_guard` 开启的前提下运行。** 每个 `meta.json`：

```json
{"tool": "...", "version": "...", "status": "ok|failed|unsupported",
 "error": "<失败时必填，类型: 消息>", "elapsed_sec": <float>,
 "chars": <int>, "lines": <int>,
 "heading_lines": <int>, "pipe_table_rows": <int>,
 "distinct_chars": <int>, "top_repeated_line": {"text": "...", "count": <int>},
 "sha256_of_txt": "..."}
```

- `heading_lines` = 以 `#` 开头的行数。`pipe_table_rows` = 同时含 ≥2 个 `|` 的行数。
- `top_repeated_line` 是判断「输出是不是全是水印」的关键，必填。
- 失败的工具**也要建 meta.json** 并写 `status`/`error`，`.txt` 写空文件。不许省略条目。

### 2.6 `runs/p0/anydoc_report.md`

AnyDoc（`firecrawl-anydoc`）的安装与试跑记录。必须回答：

1. 装上了吗？版本？装不上的确切报错？
2. 本地库（非托管 API）对 IEC 是否可用？对 SIEMENS 是否可用？确切报错原文。
3. 它的 OCR 能力是否**只存在于托管 API**？给出你据以判断的证据（源码位置 / 文档 / 报错文本），不要凭印象。
4. 结论：能否作为第三条离线召回 oracle。

**若结论是"不能"，直接写"不能"并给证据。这不是失败，这是本阶段要的答案之一。**

### 2.7 `runs/p0/P0_REPORT.md`

汇总。每个数字后面附**产生它的确切命令**。禁止出现任何无法用单条命令复现的数字。
末尾必须有一节 `## 我没能做到的`，如实列出跑不通的项与原因。**这一节为空且实际有失败项 = 隐瞒 = 判负。**

### 2.8 `runs/p0/reproduce.sh`

一条命令从零复现 2.2–2.5 全部产物。我会用它复现，产物 sha256 对不上就打回。

---

## 3. 自检清单（提交前你自己跑一遍，结果写进 P0_REPORT.md）

- [ ] `runs/p0/` 下每个 `.json` 都能被 `json.load` 解析
- [ ] 每个 `baselines/*/*.txt` 要么非空、要么其 `meta.json` 的 `status != "ok"`
- [ ] `pdf_facts.json` 里 `text_layer_chars_per_page` 长度 == `page_count`（两份都要）
- [ ] `osd_probe.json` 的键数 == SIEMENS 页数
- [ ] `test_net_guard.py` 通过，且其中的拦截断言不是 mock
- [ ] `bash runs/p0/reproduce.sh` 从零跑通
- [ ] `ruff check --select E9,F book_to_skill/ tests/` 无错
- [ ] `/usr/local/bin/python3 -m pytest tests/ -q` 全绿（**不许打挂现有测试**）

---

## 4. 验收方式（我会做的事，提前告知你）

1. 用我自己的脚本独立重测 2.2–2.4 的每一个数字，与你的 JSON 逐字段比对。**任何一项对不上即打回**，包括你"顺手估算"的。
2. 跑 `reproduce.sh`，比对 sha256。
3. AST 扫描：`tests/pdf2md/` 下任何 `except` 块内出现 `pass` / `return False` / `return None` 而未重新抛出或未记录到产物 → 判负。
4. 抓包/审计 `net_guard` 是否真拦截：我会在守卫开启下手动发起一次外部连接，必须抛异常。
5. 检查 `P0_REPORT.md` 的「我没能做到的」一节是否与实际失败项一致。

---

## 5. 明确不要做的事

- 不要写 render/ocr/tables/figures/assemble 任何一行（P1 的活）
- 不要写指标、不要写四步闭环（P2/P3 的活）
- 不要为了让数字"好看"去调任何参数——P0 要的是**现状的真实快照**
- 不要动 `SKILL.md`、`README.md`、CI 配置
