# Changelog

All notable changes to price-check are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
