# Changelog

All notable changes to **book-to-skill** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.5.0-beta.5] - 2026-08-13

### Changed
- **pdf2md scoring is now fail-closed and page-aligned.** The previous quality score could
  not discriminate between conversions: on IEC TR 62380 all six dimensions were pinned
  (`text_ocr` saturated once `nonempty_page_ratio` passed 0.98, `tables` paid a flat 25.0
  for "any table at all" so 331 tables scored the same as 1, and the rest were constants),
  producing a 73.0 that was a checksum of "did the pipeline run". Scoring now compares per
  page, aggregates `text_ocr` as a char-weighted mean, and drops unannotated dimensions
  from both numerator and denominator. Reports carry `scored_dimensions`,
  `unscored_dimensions`, `max_possible`, `total_raw`, `total_normalized_100` and
  `truth_coverage`. `_score_dimensions` is now `heuristic_scores`, tagged
  `kind=heuristic_no_truth`, and is a diagnostic field only — never `total_score` and never
  the optimizer's ranking key.
- `tables`, `figures` and `formulas` score as pooled F1 with IoU ≥ 0.5 matching, so false
  positives cost precision. Empty reference ∩ empty candidate yields `null` (unscored),
  never full marks. Items lacking a bbox can no longer match.
- Optimizer ranking reads `total_normalized_100` and refuses to declare a winner when
  `max_possible` is 0 or truth coverage is too thin (`insufficient_truth_coverage`).

### Added
- **Scanned-page table extraction** via `img2table` + local Tesseract (`extract_tables_img2table`),
  covering borderless numeric grids that `pdfplumber` cannot see. On SIEMENS SN 29500 this
  moves table extraction from 0 tables to real bilingual cells with geometry. Optional
  extra: `pdf2md-scan-tables`; the import is lazy, so installs without it are unaffected.
- `doctor` probes `img2table` and `cv2` and reports them as optional, naming the capability
  that degrades when they are missing.
- Figure detection routes are explicit (`vector` / `raster` / `region`) with recorded drop
  reasons. Candidates covering ≥92% of the page are dropped as `full_page` and candidates
  under 0.5% as `too_small`, so full-page scans and page logos no longer register as figures.

### Fixed
- **Blank pages no longer score as figures.** `convert.py` emitted a full-page figure only
  when OCR returned under 80 characters, so figures appeared exactly when OCR failed. On
  SIEMENS SN 29500 that awarded 17.0/20 for four blank pages — one of them pure white at
  grayscale range (255, 255) — while those same pages pushed `nonempty_page_ratio` to 0.974
  and broke the hard gate.
- Formula candidates are no longer manufactured from bare math characters. The old regex
  produced 256 blocks on IEC TR 62380, all of them failures, and wrote 256
  `<!-- formula_failed -->` comments into the output. Failed formulas now carry a real crop
  and a failure reason instead of `asset_path=None`.
- `_write_generated_corpus` no longer hardcodes `profile: "fast"`, which had silently
  disabled formulas for every benchmark run started from bare PDF paths.

### Notes
- Evaluation corpora under `runs/` are gitignored and not shipped.
- Annotation provenance is three-tier across independent method families (geometry/text
  layer, pixel/OCR, and a DocLayNet-lineage layout model used **only** to derive references,
  never in the extraction path). Agreement ≥ 0.95 is `silver`; disagreement is `disputed`
  and excluded from scoring. Nothing is human-verified — the corpora are machine-derived
  and `verified_by` is null throughout. The layout model is reference evidence, not ground
  truth. Scorable coverage is deliberately thin and reported as such.

## [1.5.0-beta.1] - 2026-08-12

### Added
- **pdf2md subsystem** — offline high-fidelity PDF→Markdown (`book-to-skill-pdf2md`) with
  IR bundle, quality gates, teacher adapters, synthetic benchmark fixtures, and profile
  optimizer (local auto-commit only; never push). Technical extract prefers pdf2md;
  Docling remains optional when installed. See `references/pdf-workflow.md`.
- Optional extras: `pdf2md`, `pdf2md-technical`, `pdf2md-eval`.

### Changed
- Split verbose Skill sections into `references/` so root `SKILL.md` stays under 500 lines.
- CI adds a Python 3.12 `pdf2md-unit` job; the base matrix stays dependency-light.
- npm installer now ships `references/` and nested `book_to_skill/pdf2md/`.

## [1.4.2] - 2026-08-07

### Added
- Added `book-to-skill` as the primary npm executable, so users can run
  `npm install --global book-to-skill` followed by `book-to-skill install`.
  The previous `book-to-skill-skill` command remains available for compatibility.

### Documentation
- Documented both persistent global installation with `npm` and one-shot
  execution with `npx`, including lifecycle commands for each workflow.

## [1.4.1] - 2026-08-07

### Changed
- **Keyless npm releases** — tag-triggered GitHub Actions publishing now uses npm
  Trusted Publishing with short-lived OIDC credentials instead of a repository
  `NPM_TOKEN`. Stable versions publish to `latest`; prerelease versions derive
  their dist-tag from the prerelease identifier, such as `beta` or `alpha`.

### Fixed
- Updated the extraction-complete banner to reference the current
  `AncoderAI/doc2skill` repository.

## [1.4.0] - 2026-08-07

### Added
- **npm Agent Skill distribution** — `npx book-to-skill install` installs the
  complete Skill into the Codex-compatible `~/.agents/skills` root by default,
  with explicit host mappings for Claude Code, Copilot CLI, and Amp plus custom
  target directories.
- **Managed lifecycle commands** — the npm launcher supports `install`, `update`,
  `doctor`, and `uninstall`. A versioned manifest records file hashes so updates
  refuse to overwrite locally modified managed files unless `--force` is explicit,
  while user-managed files are preserved.
- **npm release gates** — package/Python versions, the publish allowlist, Skill
  metadata, installer behavior, packed contents, and fresh-consumer installation
  are validated before publication. A tag-driven GitHub Actions workflow supports
  `NPM_TOKEN` and npm Trusted Publishing/OIDC.

### Documentation
- Added npm installation and lifecycle commands while retaining `git clone` as a
  manual Skill install and `pip install` as the extraction-only CLI path.
- Added Codex as a first-class host using the open Agent Skills user root at
  `~/.agents/skills`.

## [1.3.0] - 2026-07-30

### Added
- **Korean chapter headings** — `제N장` (and `제N절`/`제N관`/`제N편`, plus the statutory
  inserted-article `의N` form) are now detected, with the `제` prefix required so the
  everyday counter `장` (e.g. `사진 10장` = "10 photos") never false-matches. Validated
  against a ~3,000-statute corpus (precision 0.999 / recall 1.000) (#82).
- **Thai chapter headings** — `บทที่ N`, `ตอนที่ N` and `ภาคที่ N` are now detected as
  chapter boundaries, with Thai numerals (๐–๙) as well as Arabic digits. Thai-language
  books previously had no heading detection at all and fell back to length-based
  splitting. Ordinary words that begin with a chapter word (`บทความ`, `ตอนนี้`) are not
  treated as headings.

### Documentation
- Clarified the two install paths so they are not confused: **`git clone` into a
  skills folder** registers the `/book-to-skill` agent skill (Claude Code / Copilot
  CLI / Amp), while **`pip install book-to-skill`** installs only the standalone
  extraction CLI and does not register the skill. README and the docs landing now
  show both explicitly.
- README now leads with the measured headline (24×–51× fewer tokens than a
  context-dump) and a 3-step "how it works", so the value lands in the first
  screen instead of being buried mid-page.

### Security
- **Generated-skill prompt-injection scan** — a dependency-free advisory scanner
  flags instruction-override phrases, model control tags, invisible Unicode,
  generated frontmatter that widens authority, and exfiltration-shaped content
  before a generated skill is accepted or published. Findings identify only the
  rule and file/line location and never echo attacker-controlled text (#73).
- **Invisible-Unicode extraction hardening** — every parser result now removes
  zero-width U+200B/U+200C/U+200D/U+2060/U+FEFF characters and the Unicode tag block
  U+E0000-U+E007F before metrics or `full_text.txt` are produced, reports the
  removal count, and rejects sources containing no visible content after the scrub.
- **DOCX XXE / Billion Laughs hardening** — the DOCX extractor now scans the
  archive and rejects any XML part that declares a DTD or entities before
  parsing, blocking XML external-entity and entity-expansion attacks (#53, #54).
- **Subprocess argument-injection hardening** — file paths are absolutised
  before being passed to `pdftotext` / `pdfinfo` / `ebook-convert`, so a filename
  starting with `-` cannot be interpreted as a command-line option (#53, #54).
- **Dependency CVE review on pull requests** — a `dependency-review` CI job
  flags any newly introduced dependency carrying a moderate-or-higher CVE (or a
  denied license) and posts the findings as a PR comment. Dependabot now also
  covers the `pip` ecosystem.

### Changed
- **The `pdf` extra now installs `pypdf` instead of the deprecated `PyPDF2`**
  (`pip install book-to-skill[pdf]`). `pypdf` is the maintained successor;
  `PyPDF2` is end-of-life and no longer receives security fixes (#54).
- PDF text from `pdftotext` is now cleaned before use: hyphenated line-wraps are
  rejoined (`informa-\ntion` → `information`) and repeated running
  headers/footers and per-page page numbers are stripped. Fewer tokens and
  cleaner input for chapter detection; conservative (edges only, ≥3 pages, so
  mid-page content is never removed).

### Fixed
- Consolidated chapter detection now analyzes extracted source text without the generated
  `SOURCE:` boundary banners, preventing those banners from becoming phantom setext headings
  and collapsing `chapters_detected` to 2 for short source paths (#81).
- **`Chapter I.` — a chapter word followed by a Roman numeral — is now detected.** It
  matched neither existing pattern (`_EXPLICIT_CHAPTER` required Arabic digits after the
  chapter word; `_ROMAN_HEAD` required the numeral to start the line), so books using
  this common form segmented on footnote cross-references instead of chapters. Measured
  on Project Gutenberg #132 (*The Art of War*, Giles translation): 2 detected "chapters",
  both footnote citations, become the 13 real headings.
- PDF text extracted via `pdftotext` is now decoded as UTF-8 rather than the
  process locale encoding, so accented characters and punctuation are no longer
  mojibake on non-UTF-8 locales (e.g. Windows).
- Text files (`.txt`, `.md`, `.rst`, `.adoc`, `.html`, `.rtf`) saved as UTF-16 or
  UTF-32 (e.g. Windows Notepad "Unicode" or PowerShell output) are now decoded by
  their byte-order mark instead of being read as `cp1252`/`latin-1` mojibake.
- The dependency-free RTF fallback (used when `striprtf` is not installed) now
  decodes `\uN` unicode escapes — smart quotes, dashes, accented letters — instead
  of dropping them and leaving only the ASCII fallback character.
- The stdlib HTML parser (the fallback for HTML files and EPUB extraction when
  BeautifulSoup is not installed) no longer decodes HTML entities twice, so
  double-encoded entities such as `&amp;amp;` survive intact.
- The dependency-free DOCX fallback (used when `python-docx` is not installed)
  now reconstructs tables as tab-joined rows in document order, instead of
  flattening each cell onto its own line.
- The dependency-free EPUB extractor (used when `ebooklib` is not installed) now
  reads content in true spine (reading) order instead of manifest order, so
  chapters are no longer scrambled. Content documents not listed in the spine are
  still included (appended after the spine content).

## [1.2.0] — 2026-06-17

### Added
- **Installable Python package.** The extractor is now a proper `book_to_skill`
  package with a `pyproject.toml` (hatchling build backend), a `book-to-skill`
  console script, and `python -m book_to_skill`. Optional extractors are exposed
  as extras (`epub`, `pdf`, `docx`, `rtf`, `technical`, `all`); the base install
  stays dependency-free with stdlib fallbacks. `requires-python = ">=3.9"`.
  `scripts/extract.py` is kept as a thin shim so the existing skill flow is
  unchanged (#34, #35, #48).
- **Markdown / AsciiDoc heading detection.** Structure detection recognizes ATX
  headings (`#`, `==`) as chapters when no numeric "Chapter N" headings are
  present, fixing a zero-chapter result for `.md` / `.adoc` sources. Headings
  inside fenced code blocks are ignored (#44).
- **setext / reStructuredText underline headings** — a title line over a row of
  `=` or `-` is now detected, so `.rst` and setext-style Markdown no longer
  report zero chapters. Guarded against thematic breaks, table borders, and YAML
  front matter (#51).
- **More chapter languages.** Chapter-word detection now covers French, German,
  Italian, and Dutch (`Chapitre`, `Kapitel`, `Capitolo`, `Hoofdstuk`), and
  heading titles starting with `Ü`/`Û`/`Ý`/`Þ` (e.g. "Überblick") are accepted (#49).
- **Multilingual table-of-contents detection** — Chinese, Japanese, French,
  German, Italian, and Dutch (#44).

### Fixed
- **Full-width Arabic digits in CJK chapter headings** — `第１章` (U+FF10–FF19),
  common in Japanese typesetting, is now detected like `第1章` (#46).
- **Parser errors are no longer swallowed silently.** Unexpected exceptions in
  any extractor are logged to stderr (extractor name + exception type) while the
  fallback chain still returns `None` and continues, so corrupt files and
  encoding errors are diagnosable (#47, #50).
- **All-punctuation ATX "titles"** (e.g. a `=====   =====` table border) are no
  longer miscounted as chapters (#51).
- **Package imports on interpreters that evaluate annotations eagerly.** Added
  `from __future__ import annotations` to every module using PEP 604 unions
  (`str | None`), so the package imports and runs cleanly on Python 3.9 (#34).

### Security
- **CI security scanning** — CodeQL (Python, security-and-quality + weekly
  schedule), Bandit (gates on HIGH severity; reports MEDIUM+ informationally),
  and Zizmor (GitHub Actions workflow audit, informational), plus a Dependabot
  config for the `github-actions` ecosystem. Known finding to harden next:
  Bandit B314 (`xml.etree.ElementTree.fromstring` in the DOCX parser).

### Changed
- CI test matrix now includes Python 3.9 so the import path above is guarded and
  cannot silently re-break.

## [1.1.0] — 2026-06-12

### Added
- **GitHub Copilot CLI as a first-class target** — the same `SKILL.md` now
  discovers, installs, and runs across GitHub Copilot CLI, Amp, and Claude Code
  via the open Agent Skills standard. Skill Locations cover 8 discovery paths and
  the script probe walks all of them (#30).
- **`validate_skill.py --lens claude|copilot|amp`** — audits a generated SKILL.md
  against each host's rules; `claude` stays the default for CI back-compat (#30).
- **Attribution banner** — `scripts/banner.txt` is printed at the start of each
  run (best-effort, never fails the run).

### Changed
- `SKILL.md` frontmatter trimmed toward the open-standard minimum and the
  description now names all three hosts so each agent's auto-loader picks it up (#30).
- README headline + "Agent Skills" badge; install/usage sections cover all three
  hosts. `docs/ARCHITECTURE.md` shows per-host destination paths (#30).

### Notes
- `allowed-tools` was dropped from the frontmatter for host-neutrality; the skill
  is conformant on all three hosts (validated with all three lenses). If Claude
  users hit permission-prompt friction, the Bash grant from #18 will be restored
  with Claude-native tokens (Copilot ignores the key either way).

## [1.0.0] — 2026-06-08

First formally tagged release. The converter is stable, multi-format, and
validated on real books.

### Added
- **Multi-format extraction** — PDF, EPUB, DOCX, HTML, Markdown, reStructuredText,
  AsciiDoc, RTF, and MOBI/AZW/AZW3 (via Calibre), through a modular `extractor`
  package with per-format parsers and graceful stdlib fallbacks.
- **`extract.py --check`** — preflight that reports which extractors are installed
  for every format and the exact command to install whatever is missing (#21).
- **Adaptive per-chapter depth** — token budget scales with `BOOK_TYPE × DEPTH`;
  study-depth chapters require a worked example, and the cheatsheet is generated as
  a decision/reasoning layer (decision rules, trees, trade-offs, thresholds, tells)
  rather than a keyword list (#20).
- **`tools/discovery_tax.py`** — measures the "Discovery Loop Tax": tokens a
  context-dump vs a discovery loop vs book-to-skill put into context to answer one
  question, on a real book (#23).
- **Update / fold-in workflow** — merge new sources into an existing skill, keeping
  chapter index, topic index, glossary, patterns, and cheatsheet in sync.
- **GitHub Actions CI** — lint (ruff), test matrix (py3.10–3.13), dependency-free
  smoke test, and SKILL.md Claude-conformance validation (#15, #18).

### Changed
- **README positioning** — copyright & fair-use section, "Beyond books" use cases,
  context-dump / RAG / 1M-window FAQ, and a measured Discovery Loop Tax + real
  per-conversion cost table across four books (#19, #27).
- Default output target is `~/.claude/skills/` for Claude Code, with Amp skill
  directories also supported (#13, #14).

### Fixed
- **Chapter detection** — scans the full text (was capped at 50k chars) and counts
  distinct explicit `Chapter N` / `Capítulo N` headings, rejecting numbered list
  items, inline cross-references, and years; adds Portuguese support (#26).
- **Roman-numeral headings** — `I: Loomings`, `II. The Carpet-Bag` are now detected
  with canonical-numeral validation (#28).
- **EPUB extraction** — resolve OPF-relative hrefs in the stdlib zipfile fallback (#11, #12).
- **Batch resilience** — one bad source is skipped with a warning instead of aborting
  the whole run; explicit input order is preserved (#7).

### Known limitations
- Chapter auto-detection needs explicit `Chapter N` / `Capítulo N` or Roman-numeral
  headings. Books that head chapter bodies with bare titles (e.g. *Moby-Dick*, where
  numerals appear only in the table of contents) or use section titles (e.g. Pro Git)
  do not auto-segment.
- Technical PDFs extracted in text mode may lose heading structure; use technical
  mode (Docling) to preserve tables, code, and headings.

[Unreleased]: https://github.com/AncoderAI/doc2skill/compare/v1.4.2...HEAD
[1.4.2]: https://github.com/AncoderAI/doc2skill/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/AncoderAI/doc2skill/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/AncoderAI/doc2skill/releases/tag/v1.4.0
[1.3.0]: https://github.com/virgiliojr94/book-to-skill/releases/tag/v1.3.0
[1.2.0]: https://github.com/virgiliojr94/book-to-skill/releases/tag/v1.2.0
[1.1.0]: https://github.com/virgiliojr94/book-to-skill/releases/tag/v1.1.0
[1.0.0]: https://github.com/virgiliojr94/book-to-skill/releases/tag/v1.0.0
