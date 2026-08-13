# TASK P8 —— 修 O(N×M) 逐页重开文档缺陷

> 实施方：本机 `cursor-agent`（模型 `cursor-grok-4.6-high-fast`）
> 验收方：Claude（独立重算 + 黄金参照逐字节比对 + A/B 复测）
> 前置：P5/P6/P7 已验收（75 passed）。**不许改已有的 26 条 describe/chapters 测试**，我存了指纹。

---

## 0. 铁律

1. **禁止任何外发网络**。
2. **输出必须逐字节不变**。这是纯性能修复，**不是**行为变更。
   我存了黄金参照（修复前 WCCA p38-63 的 `document.md` + `document.ir.json`），
   修复后必须 SHA-256 完全一致。**任何输出差异 = 判负**，哪怕你认为新结果"更好"。
3. **不许 `try/except` 吞掉异常后继续**。
4. **不许伪造性能数字**。每个数字必须能被单条命令复现。
5. **不许改动现有测试让它们变绿**。
6. **不许泄漏文件句柄**。加了缓存就必须有明确的释放路径。

---

## 1. 缺陷（我已 profile 定位，别质疑，直接修）

`convert --pages 38-40`（3 页）在 220 页的 WCCA 上跑 **48.5 秒**。profile 前三名：

```
ncalls  cumtime  function
    18   36.882  pdfplumber/pdf.py:144(pages)          ← 占 76%
     3   21.499  figures.py:421 detect_raster_figures
     3   20.876  tables.py:44  extract_tables_pdfplumber
  2652   36.589  pdfminer/pdfpage.py:90(create_pages)
  2640   34.759  pdfminer/pdfpage.py:49(PDFPage.__init__)
```

根因：这两个函数**每处理一页就重开整本文档**，而且用 `len(pdf.pages)` 做边界检查
——`pdfplumber.pages` 是个会**物化全部页对象**的 property：

```python
# tables.py:44 与 figures.py:421 同一写法
with pdfplumber.open(pdf_path) as pdf:
    if page_index < 0 or page_index >= len(pdf.pages):   # ← 220 页全物化，只为查个下标
        return out
    page = pdf.pages[page_index]
```

处理 N 页的 M 页文档 = **O(N×M)**。

**同一模式还存在于**（pypdf / pdfium，实测合计约 27 s，也要修）：

| 位置 | 每次调用做的事 |
|---|---|
| `convert.py:78 _extract_native_text` | `PdfReader(pdf_path)` 全文档解析 |
| `convert.py:86 _embedded_image_count` | `PdfReader(pdf_path)` 全文档解析 |
| `render.py:11 render_page` | `pdfium.PdfDocument(pdf_path)` 全文档打开 |
| `render.py:31 page_size` | `PdfReader(pdf_path)` 全文档解析 |

实测单次打开成本（220 页 vs 26 页）：`PdfReader` 96.2ms vs 5.7ms（17×），
`pdfium` 4.8ms vs 0.5ms（9.9×）。

---

## 2. 要求

### 2.1 改法

同一 PDF 路径的文档句柄**开一次复用**。具体实现你定，但必须满足：

- 边界检查**不许**用 `len(pdf.pages)`。用 pypdf/PyMuPDF 拿页数（`_page_count` 已有），
  或用 pdfplumber 的惰性方式，**不要触发全页物化**。
- 缓存必须有明确释放：转换结束时关闭所有句柄。提供一个显式的 `close_all()` /
  上下文管理器，并在 `convert_pdf` 结束时调用（`finally` 里，异常路径也要关）。
- 单进程单线程假设即可，不必做线程安全，但**不许**用全局可变状态而不提供清理。
- 缓存键必须包含**文件路径 + mtime + size**，否则同名文件被替换后会读到旧句柄。

### 2.2 不许做的事

- 不许改抽取逻辑、阈值、评分。**这是纯性能修复。**
- 不许因为"顺手"就改 `--pages` 的语义。
- 不许把 `detect_raster_figures` / `extract_tables_pdfplumber` 的签名改成必须传句柄
  而破坏现有调用方——如果要改签名，所有调用点一并改，且保持默认行为。

---

## 3. 测试（加进 `tests/pdf2md/test_perf_handles.py`，新文件）

1. **输出不变**：对 `fixtures/synthetic` 里的合成 PDF，修复前后 `document.md`
   和 `document.ir.json` 内容一致（用一次转换的结果和已提交的期望值比，或
   自比：同一 PDF 连转两次结果逐字节相同）
2. **句柄释放**：`convert_pdf` 结束后缓存为空；用 `psutil` 或
   `lsof` 不可靠，改为断言缓存字典长度为 0
3. **异常路径也释放**：转换中途抛异常时缓存同样被清空
4. **缓存键含 mtime**：同路径文件内容变了（mtime 变）后，再次读取拿到的是新内容，
   不是缓存里的旧句柄
5. **不触发全页物化**：对多页 PDF 只处理 1 页时，断言 `pdfplumber` 的
   `create_pages` / `PDFPage.__init__` 调用次数远小于总页数
   （用 `unittest.mock.patch` 计数，或用 `pdf.pages` 是否被访问来断言）
6. **页数边界**：`page_index` 越界时行为与修复前一致（返回空，不抛）

---

## 4. 性能实测（必须跑，如实落盘）

修复后重跑，把数字写进报告：

```bash
W='/Users/mccree/Desktop/AnCoder/Test/测试文档/汽车电子：WCCA方法及流程_202510高宜国_9787111790488 ReN.pdf'
/usr/bin/time -p /usr/local/bin/python3 -m book_to_skill.pdf2md convert "$W" \
    --output /tmp/p8_after --profile fast --pages 38-63
```

**修复前基线（我实测）：244.62 s。**
切出的 26 页小 PDF 单独转是 21.11 s —— 那是理论下界附近。
修复后整本 `--pages 38-63` 应该显著向 21 s 靠拢。**具体到多少你测出来算数，不要凑。**
没达到预期也如实报，我要的是真数字。

---

## 5. 验收命令（我会原样重跑）

```bash
cd /Users/mccree/Desktop/AnCoder/doc2skill
/usr/local/bin/python3 -m pytest tests/pdf2md/ -q          # 必须 81 passed
/usr/local/bin/python3 -m ruff check book_to_skill/pdf2md/
```

外加**我这边独立做的黄金参照比对**（你不用做，但要知道我会做）：

```
document.md      sha256 = 47dff816731bc1daf5d5fce9454ee0e3e8bb9e6222063cfade2f9ac98bbf1d31
document.ir.json sha256 = 693bd41b6a685bbaf99870072c31b3ae96fb242a848853880c71854ebe46f31a
```

---

## 6. 不在本任务范围内（别做）

- 章节检测 / 切分的任何改动（P7 已验收，别动）
- `describe.py` / `quality.py`
- 并行化、多进程 —— 本任务只修重复打开，不引入并发
