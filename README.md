# price-check

> 中文文档: [README.zh-CN.md](README.zh-CN.md)

An [OpenClaw](https://openclaw.ai/) skill that combines **price comparison + verdict + buy links + local price history** into one tool. Works as a shopping assistant in Feishu (Lark) chat through your OpenClaw bot — ask "is iPhone 17 Pro 256G worth buying now?" and get a complete report with comparison table, direct purchase links, and a buy/wait verdict.

## Features

- **Multi-platform price comparison** — pulls live data from 22+ Chinese e-commerce platforms (Taobao/Tmall, JD, PDD, Suning, Vipshop, Kaola, Douyin, Kuaishou, 1688) via the upstream [shopmind-price-compare](https://clawhub.com/skills/shopmind-price-compare) skill
- **Three-layer filtering** that strips noise before deciding:
  1. **Price layer** — drops bottom-quartile outliers (`price < raw_median × 0.3`) such as accessories, cables, screen protectors
  2. **Trust layer** — flags items with seven `condition` categories (refurbished / bundle / accessory / activation_questionable / parallel_import / trusted_domestic / unknown) and shop trust score (Apple official store / JD self-operated / brand flagship)
  3. **Relevance layer** — title token-match scoring (G ↔ GB equivalence), drops items with relevance score < 0.75 or ambiguous multi-model titles ("V8 V10 V11 V12 V15" listings)
- **Verdict engine** — `Strongly Recommend / Buy / Wait / Don't Buy / Insufficient Data` based on `best_deal.price` vs market median, with auto-promotion when historical low
- **Auto-fetched purchase URLs + Taobao share codes** for `best_deal` and Top 3 candidates — no extra clicks needed
- **Local SQLite price history** — every query persists snapshots; after the same query runs ≥3 times, the verdict can reference "this product's historical low/high/avg" and detect trap pricing
- **Optional Feishu Bitable sync** — write each query result to a Feishu multi-dimensional table (31 columns including history fields), browse historical decisions and click purchase links from Feishu mobile/desktop
- **Zero-config by default** — install and run; Feishu sync is fully opt-in (off by default, runs without lark-cli)

## Architecture

```
User asks "X 多少钱合适买"
    ↓
OpenClaw bot (Molty) recognizes trigger keywords
    ↓
Calls: uv run bin/price_check.py "X"
    ↓
shopmind._fetch_search_items()         # data layer (zero modification)
    ↓
[1] _filter_outliers()                 # price layer
    ↓
[2] _select_best_deal()                # trust layer × relevance layer
    ↓
_enrich_with_urls()                    # parallel fetches buy_url / Taobao codes
    ↓
LocalDBHistoryProvider.get_history()   # reads SQLite snapshots accumulated locally
    ↓
compute_verdict() + compute_trap_warning()
    ↓
JSON to stdout  +  SQLite persist  +  (optional) Feishu Bitable sync
    ↓
Bot renders human report (6-section "C-mode": warning / best_deal+link / Top 3 table / history / advice / transparency)
```

## Requirements

- **Required**: `python3` ≥ 3.10, `uv` (auto-handled by OpenClaw skill metadata)
- **Required upstream**: [shopmind-price-compare](https://clawhub.com/skills/shopmind-price-compare) skill (for live data)
- **Optional**: `lark-cli` (only when you enable Feishu Bitable sync)

## Installation

```bash
# Via OpenClaw (recommended — skill auto-discovered)
openclaw skills install price-check
# Or manually clone into your OpenClaw workspace:
git clone https://github.com/yuxiaoyang2007-prog/price-check.git \
  ~/.openclaw/workspace/skills/price-check
```

## Usage

### Direct CLI

```bash
uv run ~/.openclaw/workspace/skills/price-check/bin/price_check.py "iPhone 17 Pro 256G"
# JSON output to stdout
```

CLI flags:

- `--source <N>` — restrict to one platform (0=all, 1=Taobao, 2=JD, 3=PDD, ...)
- `--page <N>` — pagination
- `--no-cache` — bypass 30-min query cache, force fresh data

### Through OpenClaw bot (natural language)

In Feishu/Lark chat with your OpenClaw bot:

```
"iPhone 17 Pro 256G 现在合适入手吗？哪里买最便宜？"
"戴森 V15 比价"
"Switch 2 港版 哪里买"
```

The bot recognizes the trigger keywords (`比价` / `值不值得买` / `哪里买` / `多少钱合适` / etc.), calls the script, renders the human-friendly C-mode 6-section report.

## Optional: Enable Feishu Bitable sync

Sync every query (best_deal + Top 3 + history price) to a Feishu multi-dimensional table for cross-device browsing. **Default off** — only do this if you want it.

1. Install `lark-cli`
2. Create an empty Bitable in your Feishu workspace
3. Authorize your Feishu Bot app as an editor on that Bitable
4. Run the one-time setup:

```bash
uv run ~/.openclaw/workspace/skills/price-check/bin/setup_feishu.py \
  'https://your-tenant.feishu.cn/base/<BASE_TOKEN>?...'
```

The script auto-creates 31 fields (query / verdict / best_deal price/platform/shop/title/url / Top2 url / Top3 url / match score / condition / median / historical low/high/avg / current rank / trap warning / etc.) and writes the config to `~/.openclaw/data/price-check/config.json`.

To disable later: edit that config, set `feishu_sync.enabled = false`.

## Configuration

All optional settings live in `~/.openclaw/data/price-check/config.json` — see [config.example.json](config.example.json) for the schema. Sections:

- `storage.cache_ttl_seconds` — query cache TTL (default 1800s)
- `history_provider.type` — `local_db` (default) or `noop`
- `history_provider.min_query_history` — minimum past queries before market history kicks in (default 3)
- `history_provider.min_goods_history` — minimum past snapshots per product before product history kicks in (default 2)
- `feishu_sync.enabled` — opt-in flag (default false)
- `feishu_sync.base_token` / `table_id` / `lark_cli_profile` — set by `setup_feishu.py`

## Local data layout

```
~/.openclaw/data/price-check/
├── price-check.db       # SQLite — three tables: queries / price_snapshots / query_cache
└── config.json          # OPTIONAL — falls back to defaults if absent
```

Inspect history with plain SQLite:

```bash
sqlite3 ~/.openclaw/data/price-check/price-check.db \
  "SELECT queried_at, query, verdict FROM queries ORDER BY id DESC LIMIT 20"

# Backup
cp ~/.openclaw/data/price-check/price-check.db ~/Backups/

# Reset (delete all data)
rm -rf ~/.openclaw/data/price-check/
```

## Roadmap

- **v0.5** — fuzzy match for SKU keywords (e.g., "V12 plus" ↔ query "V12 Pro" with synonym handling)
- **v0.5** — separate handling of carrier-operated JD self-operated stores (China Unicom / Mobile / Telecom — currently treated as trusted but actually higher-risk for contract phones)
- **v0.6** — fetching `best_deal.url` becomes pull-on-demand again (not always); price history visualization for Bitable charts
- **v1.0** — `HistoryProvider` plug-in points for external sources (manmanbuy / Smzdm / JD price-protection API)

## Privacy

- **All data stays on your machine.** SQLite is local; no external service writes happen unless you opt into Feishu sync.
- The `config.json` may contain your Feishu `base_token` — keep this file out of version control (already in `.gitignore` / outside the repo path).
- This skill does **not** track any user identity, purchase records, or PII.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- Upstream data layer: [shopmind-price-compare](https://clawhub.com/skills/shopmind-price-compare) — the live-data crawler that price-check wraps
- Hosting platform: [OpenClaw](https://openclaw.ai/) — the agent runtime
