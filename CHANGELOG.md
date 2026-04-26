# Changelog

All notable changes to price-check are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.6.2] — 2026-04-26

### Fixed
- **`stats` 现在反映"真正相关的候选"分布**（去 outlier + 去 flagged + 去 low_relevance），不再把"标题不匹配的噪音商品"算进市场中位数。
  - 之前 v0.6.1：query "Mac Studio M3 Ultra 256G 内存 1T 硬盘" 召回 36 条 → maishou 把"内存""硬盘"当独立关键词分流，召回 22 条内存条/硬盘 → stats.median = ¥1004 → verdict 变成 "再等等 高于中位数 3962.6%"（实际 best_deal ¥40799 是真 Mac Studio 自营货）
  - 现在：stats 用 relevant_items（4 条真 Mac Studio）→ stats.median ≈ ¥45000 → verdict 准确
- 影响：`stats.count` 数值变小（只算相关候选），verdict_reason 里的 "N 平台中位数" 现在更精准
- `stats_raw` 不变，仍含原始全部 items（透明度）

### Why
v0.6.1 修了 search_url 用规范化 query 这层，但 maishou 召回层用的还是用户原 query（含"内存""硬盘"），所以召回结果里仍混入大量配件。verdict 算市场中位数时该把这些噪音排除，就像 best_deal 选择时排除一样 —— 两者比较基准必须一致。

## [0.6.1] — 2026-04-26

### Fixed
- **search_url 不再被规格词搜偏**：`_normalize_query_for_search()` 去除 query 里的"内存""硬盘""存储""主存""SSD""屏幕""显示器" 等规格描述词。用户问 "Mac Studio 256G 内存 1T 硬盘" 时，京东原生搜索 keyword 现在是 "Mac Studio 256G 1T"（之前会带"内存""硬盘"两词，被电商分词当配件搜偏）。
- **飞书 Top 3 表格不再串行**：`_render_human_report()` 把候选列表从 markdown table 改成纯文本格式（一条候选 3 行：价格店铺/标题/搜索链接），避免飞书消息渲染长 URL 在 table 里挤压成单列。

### Why this exists
User shipped v0.6.0 + flagged two issues:
1. JD search_url for "Mac Studio M3 Ultra 256G 内存 1T 硬盘" returned mostly memory sticks + hard drives (Chinese e-commerce engine literally tokenizing 内存/硬盘 as accessories).
2. Feishu rendered the markdown candidate table with all Top 1/2/3 search URLs squashed into the first column — visual mess.

Both fixed in this patch. No schema/break change vs v0.6.0.

## [0.6.0] — 2026-04-26 — BREAK CHANGE

### Why this exists

User reported affiliate short-links (`u.jd.com/*`, `m.tb.cn/*`) work pre-login but fail after login (the platform anti-self-purchase fraud system kicks in). Worse, even after we asked agents to display fallback `search_url` in v0.5.4, Molty kept "creatively" omitting it because LLMs are non-deterministic. Two architectural decisions:

1. **Drop affiliate short-links entirely** — no more `buy_url` / `copy_cmd`. We don't embed any affiliate tracking. Users can't be misled by "this link doesn't work after login" anymore, and the skill is morally cleaner (no income-skimming on top of upstream xiaohaook's invite_code).
2. **Take rendering away from the LLM** — Python now produces a fully-rendered `human_report` markdown string in the JSON. Agent's job is reduced to "send `human_report` verbatim, then optionally append a short '我的建议' section". This converts a non-deterministic LLM choice ("should I show search_url?") into a deterministic Python output.

### Removed (BREAK CHANGE)
- `best_deal.buy_url` field — gone
- `best_deal.copy_cmd` field — gone (Taobao share codes)
- `all_platforms[].buy_url` / `.copy_cmd` — gone
- `_enrich_with_urls()` helper function — gone
- `_data_layer.fetch_goods_detail()` is no longer called from main flow (still in module for backward compat / opt-in use)
- Feishu Bitable columns: `best_deal链接` / `best_deal口令` / `Top2链接` / `Top3链接` — must be manually deleted from existing Bitables

### Added
- `result["human_report"]` — Python-rendered complete markdown report (warning section / best_deal + search_url / Top 3 table + search_url / history price / verdict / transparency)
- `result["_meta"]["agent_must_render"]` — instruction string repeated inside the JSON
- `--report` CLI flag — emit just the markdown report, no JSON
- New Feishu Bitable columns: `best_deal搜索链接` / `Top2搜索链接` / `Top3搜索链接` (added by setup_feishu.py)
- SKILL.md gains a "⚠️ Agent 渲染硬规则" section at the top

### Migration

For end users:

```bash
# Old (v0.5.x) — buy_url + copy_cmd were available
clawhub install --version 0.5.4 price-check    # roll back if needed

# New (v0.6.0)
clawhub install price-check                     # gets latest

# CLI usage (bypass LLM entirely):
uv run ~/.openclaw/workspace/skills/price-check/bin/price_check.py "X" --report
```

For Feishu users (if you had v0.5.x setup):
- Old data in 旧链接 columns is preserved but new rows won't fill them
- Run `setup_feishu.py` once again to add the 3 new search_url columns

## [0.5.4] — 2026-04-26

### Fixed
- **Critical bug fix for v0.5.3**: `_make_search_url(source, query)` was renamed to take a `query` argument, but the caller in `_normalize_item` was still passing `title`, so the generated search URLs in v0.5.3 had the same noise problem as v0.5.2 (used maishou's dirty item title instead of the user's clean query). v0.5.4 actually fixes the call site.
- This is a textbook verification-before-completion miss — the function signature change was tested but the runtime output wasn't. v0.5.4 e2e test confirms `search_url?keyword=Mac+studio+M3+Ultra+256G+1T` (clean user query) instead of `?keyword=Apple%2F苹果AI电脑%2F...`.

## [0.5.3] — 2026-04-26

### Fixed
- **`search_url` keyword changed from item title to user's original query**. The maishou-provided title contains noise like "AI电脑", "台式机", "Z1CE001AH", and combined punctuation/CJK chars produced over-long encoded URLs that JD/Taobao search would tokenize awkwardly. Using the user's clean query instead gives accurate native-search results.
- `quote(query, safe="")` ensures all special characters (including `/`) are properly URL-encoded.

### Added
- **Critical UX guidance** in `references/report-template.md`: agents now must show a usage note explaining that affiliate short-links (`u.jd.com/*`, `m.tb.cn/*`) **must be opened in a logged-out / incognito window**. Logged-in users hitting these links will be flagged by the platforms' anti-self-purchase fraud system and the link will fail. The `search_url` (native search, no affiliate tracking) is the right link for logged-in shopping.
- Why this matters: a user reported their JD affiliate link worked pre-login but errored post-login. This is a fundamental constraint of the JD/Taobao/PDD affiliate systems, not a bug we can fix in code — but agents now have to explain it.

## [0.5.2] — 2026-04-26

### Added
- **`search_url` fallback field on every item** — when the `maishou` short-link is inaccurate (most commonly for special-channel SKUs like education-discount Mac Studios, enterprise-only iPhones, employee-only listings, where the JD/Taobao affiliate redirect lands on the store homepage or a similar SKU instead of the exact item), the `search_url` provides a native-platform search URL using the item's title, so users can reliably find the exact product.
- Native search URL templates for: Taobao/Tmall (source 1), JD (2), PDD (3), 1688 (10). Other platforms (Suning/Vipshop/Kaola/Douyin/Kuaishou) get `search_url = null` because their web search UX is poor.
- `best_deal` now carries the `search_url` from the underlying item.

### Changed
- `references/report-template.md` updated: when `search_url` is non-null, agents must show it as a fallback link with a one-line note about why (`"教育款 / 企业专享等特殊渠道商品转链可能不准，用此兜底"`).
- Agent tip #3 strengthened to require showing both `buy_url` and `search_url`.

### Why
User reported that `best_deal.buy_url` for "Mac Studio M3 Ultra 256G 1T 教育优惠" returned a JD short-link (`u.jd.com/...`) that didn't lead to the exact education-discount SKU. Investigation revealed maishou's `goodsId → JD SKU` mapping is fragile for special-channel products (education / enterprise / member-only). Native search by title is a robust fallback for these cases.

## [0.5.1] — 2026-04-26

### Changed (docs only)
- **Reframed skill positioning around user value**, not implementation. SKILL.md description / README intros / SKILL.md "what it does for you" section now lead with three concrete user-facing capabilities:
  1. Search lowest price across China's major e-commerce platforms (Taobao/Tmall, JD, PDD, Suning, Vipshop, Kaola, Douyin, Kuaishou, 1688)
  2. Tell whether to buy with a clear verdict + concrete evidence + clickable buy links
  3. Monitor historical prices locally (catches "fake-discount" rise-then-fall traps)
- Implementation details (three-layer filter, condition dictionary, etc.) moved to "How it actually works" subsections — no longer the lead.
- No code changes; bumping `_meta.version` from 0.5.0 → 0.5.1 to keep schema consistent.

## [0.5.0] — 2026-04-26

### Changed
- **Self-contained data layer** — the `maishou88.com` API client (HTTP endpoints / OPENID / items construction) was internalized into `bin/_data_layer.py`, derived from [shopmind-price-compare v2.2.0](https://clawhub.ai/skills/shopmind-price-compare) by **xiaohaook**. price-check no longer depends on the upstream `shopmind-price-compare` skill being installed alongside it. Users only install one skill now.
- Attribution preserved: `_data_layer.py` header + `README.md → Acknowledgements` + `SKILL.md → 数据层` section all credit the original author.

### Removed
- Removed `_load_shopmind()` + `importlib.util.spec_from_file_location` machinery — no longer needed.
- Removed "requires shopmind-price-compare" from SKILL.md and README.

## [0.4.1] — 2026-04-26

### Changed
- **Report renderer adopts "C-mode"**: 6 fixed sections (⚠️ warning → 🏆 best_deal+link → 📊 Top 3 table → 📈 history → 🤖 advice → ⚠️ transparency). Even when the verdict is "don't buy", agents must show all sections — no shortcuts. Designed to prevent agents from compressing data away in the name of brevity.
- **`🤖 我的建议` (Advice) section becomes independent**: agent's product-level judgment is presented separately from the raw `verdict` field. Allows phrasings like "tool says strongly recommend, but since SKU doesn't match, actually don't buy".
- Top-of-report ⚠️ warning block is now mandatory when `best_deal.relevance.missing` is non-empty OR `best_deal == null` (anti-misrecommendation guard).

### Fixed
- `setup_feishu.py` no longer hard-codes `version` in `config.json` (was overwriting the version field with stale value on each run).

### Added
- `config.example.json` with inline `_doc_*` field comments for all configurable options.
- SKILL.md gains "Zero-config by default, Feishu sync fully opt-in" introduction at the top.

## [0.4.0] — 2026-04-26

### Added
- **`LocalDBHistoryProvider`**: Reads accumulated SQLite snapshots as historical price source. No external dependency on manmanbuy/CamelCamel.
  - `market` dimension: aggregates `best_deal.price` over past N queries of the same query string.
  - `best_deal_history` dimension: tracks the same product's price across past queries.
- **Verdict promotion/demotion based on historical position**:
  - `current_rank=low` + `0.85 < ratio ≤ 0.95` → promotes "Buy" to "Strongly Recommend".
  - `history.trap` hit → demotes to "Don't Buy".
- **Stable product fingerprint via `(shop + title prefix)`**: shopmind's `goodsId` contains a session token that varies between calls, so exact-match fails. Fallback uses shop+title for cross-call matching.
- 7 new Feishu Bitable fields: 历史样本数 / 历史最低 / 历史最高 / 历史均价 / 当前位置 / 市场30日中位 / 当前/市场比.

### Fixed
- verdict_reason text bug when `ratio > 1.0` (negative percent display "差距仅 -49.3%" → now correctly "高于中位数 49.3%").

## [0.3.0] — 2026-04-26

### Added
- **Auto-fetch buy URLs for `best_deal` + Top 3 candidates** (parallel `asyncio.gather` over `shopmind._fetch_goods_detail`). No more "where to buy" follow-up question.
- **Local SQLite persistence** (`~/.openclaw/data/price-check/price-check.db`) with 3 tables: `queries`, `price_snapshots`, `query_cache`. Zero external dependency (Python stdlib).
- **30-min query cache** to reduce redundant shopmind API calls within short windows.
- **Optional Feishu Bitable sync** (default off). When enabled, every query writes a record to a Feishu multi-dimensional table for cross-device browsing.
- `setup_feishu.py` one-time setup script that auto-creates 24+ fields in a Feishu Bitable.

### Changed
- **Report layout becomes "shopping-led"**: `best_deal + link` and `Top 3` table moved to top; verdict moved to middle.
- `shopmind-price-compare` upstream refactored: extracted `_fetch_search_items()` + `_fetch_goods_detail()` helpers, added `--format json` mode (CLI backward-compatible).

## [0.2.0] — 2026-04-26

### Added
- **Title relevance scoring** (`_title_relevance`): token match rate (with G ↔ GB equivalence), `score < 0.75` rejected.
- **Ambiguous title detection** via `MODEL_PATTERNS`: titles listing 3+ different model tokens (e.g., "V8 V10 V11 V12 V15") flagged as multi-product noise.
- New condition category: **`accessory`** — recognises "充电支架 / 保护套 / 钢化膜 / Dok / 除螨仪" etc.
- `refurbished` keywords expanded: 样机 / 展示机 / 演示机 / 展品 / 模型机.
- `low_relevance_items` field added to output schema.
- `trap_warning` adds "💡 lower-priced untrusted candidates" transparency line.

### Changed
- `best_deal` selection now requires `relevance.score >= 0.75 AND not relevance.ambiguous` in addition to condition + trust filters.

## [0.1.x] — 2026-04-25 → 2026-04-26

### Initial development (rapid iteration)

- v0.1.0: shopmind importer wrapper + price-distribution outlier filter (`price < raw_median × 0.3` removed as accessories/noise).
- Condition keyword dictionary added: `bundle / refurbished / activation_questionable / parallel_import / trusted_domestic / unknown` (7-tier classification).
- Trusted shop classifier: shopName literal + regex (Apple official store / JD self-operated / brand flagship).
- `best_deal` 3-tier priority: trusted+domestic > trusted+parallel > untrusted+domestic_label. Suspicious conditions never enter best_deal.
- HistoryProvider plugin interface scaffolded (NoOp implementation as default).

[0.4.1]: https://github.com/yuxiaoyang2007-prog/price-check/releases/tag/v0.4.1
[0.4.0]: https://github.com/yuxiaoyang2007-prog/price-check/releases/tag/v0.4.0
[0.3.0]: https://github.com/yuxiaoyang2007-prog/price-check/releases/tag/v0.3.0
[0.2.0]: https://github.com/yuxiaoyang2007-prog/price-check/releases/tag/v0.2.0
