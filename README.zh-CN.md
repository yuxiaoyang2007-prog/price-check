# price-check

> English docs: [README.md](README.md)

[OpenClaw](https://openclaw.ai/) skill：把**比价 + verdict + 购买链接 + 本地历史价**做成一体化工具。装在 OpenClaw 里之后，你在飞书里直接对你的 Bot 说"iPhone 17 Pro 256G 现在合适入手吗？"，它会回一份完整报告 —— 多平台对比表 + 可点击购买链接 + 该不该买的 verdict + 历史价位置。

## 核心特性

- **全网横向比价** —— 通过上游 [shopmind-price-compare](https://clawhub.com/skills/shopmind-price-compare) 拉 22+ 个国内电商平台（淘宝/天猫、京东、拼多多、苏宁、唯品会、考拉、抖音、快手、1688）实时数据
- **三层过滤**自动剥离噪音：
  1. **价格层** —— 剔除底部 outlier（`price < raw_median × 0.3`），过滤掉配件/数据线/钢化膜等同关键词杂物
  2. **信任层** —— 7 档 condition 识别（refurbished / bundle / accessory / activation_questionable / parallel_import / trusted_domestic / unknown）+ 店铺信任度（Apple 自营 / 京东自营 / 品牌官方旗舰店）
  3. **相关性层** —— title token 命中率（含 G ↔ GB 等价匹配），匹配度 < 0.75 或多型号堆砌（"V8 V10 V11 V12 V15" 这种模糊 listing）会被过滤
- **verdict 决策**：`强烈推荐 / 可以买 / 再等等 / 别买 / 数据质量不足` —— 基于 best_deal.price vs 市场中位数；当前价处于历史低位时自动升档
- **best_deal + Top 3 自动拉购买链接 + 淘口令** —— 点击直达，不用追问"哪里买"
- **本地 SQLite 历史价积累** —— 每次查询都写库；同 query 跑 ≥3 次后，verdict 会引用"该商品历史最低/最高/均价"+ 检测先涨后降陷阱
- **飞书多维表格同步**（可选）—— 启用后每次查询写一行到飞书 Bitable（31 列字段含历史段），手机/PC 飞书 App 直接刷查询历史 + 点击购买链接
- **零配置即用** —— 装上就能跑，飞书同步默认关闭，不用就完全不用配

## 架构

```
用户问"X 多少钱合适买"
    ↓
OpenClaw bot (Molty) 识别触发词
    ↓
调用：uv run bin/price_check.py "X"
    ↓
shopmind._fetch_search_items()         # 数据层（不修改上游）
    ↓
[1] _filter_outliers()                 # 价格层
    ↓
[2] _select_best_deal()                # 信任层 × 相关性层
    ↓
_enrich_with_urls()                    # 并发拉 buy_url / 淘口令
    ↓
LocalDBHistoryProvider.get_history()   # 读本地 SQLite 积累的快照
    ↓
compute_verdict() + compute_trap_warning()
    ↓
JSON 到 stdout  +  写本地 SQLite  +  （可选）飞书 Bitable 同步
    ↓
Bot 渲染 6 段报告（C 模式：警告 / best_deal+链接 / Top 3 表 / 历史价 / 我的建议 / 透明度）
```

## 依赖

- **必须**：`python3` ≥ 3.10、`uv`（OpenClaw skill metadata 自动处理）
- **必须的上游 skill**：[shopmind-price-compare](https://clawhub.com/skills/shopmind-price-compare)（数据来源）
- **可选**：`lark-cli`（只在启用飞书同步时需要）

## 安装

```bash
# 通过 OpenClaw（推荐 — skill 自动识别）
openclaw skills install price-check
# 或手动 clone 到 OpenClaw workspace：
git clone https://github.com/yuxiaoyang2007-prog/price-check.git \
  ~/.openclaw/workspace/skills/price-check
```

## 用法

### 直接 CLI

```bash
uv run ~/.openclaw/workspace/skills/price-check/bin/price_check.py "iPhone 17 Pro 256G"
# stdout 输出 JSON
```

CLI 参数：

- `--source <N>` —— 限制单平台（0=全部，1=淘宝，2=京东，3=拼多多 ...）
- `--page <N>` —— 翻页
- `--no-cache` —— 忽略 30 分钟查询缓存，强制重新拉数据

### 通过 OpenClaw bot（自然语言）

在飞书里对你的 OpenClaw bot 说：

```
"iPhone 17 Pro 256G 现在合适入手吗？哪里买最便宜？"
"戴森 V15 比价"
"Switch 2 港版 哪里买"
```

Bot 识别触发词（`比价` / `值不值得买` / `哪里买` / `多少钱合适` 等），调脚本，按 C 模式 6 段格式渲染报告回你。

## 可选：启用飞书多维表格同步

把每次查询（best_deal + Top 3 + 历史价）同步到飞书 Bitable，方便手机/PC 飞书 App 跨设备浏览。**默认关闭**，想用才需要做。

1. 装 `lark-cli`
2. 在飞书云空间建一张空多维表格
3. 把你的飞书 Bot 应用授权为该 Bitable 的编辑者
4. 跑一次性配置脚本：

```bash
uv run ~/.openclaw/workspace/skills/price-check/bin/setup_feishu.py \
  'https://your-tenant.feishu.cn/base/<BASE_TOKEN>?...'
```

脚本会自动建 31 个字段（查询词 / verdict / best_deal 价格 / 平台 / 店铺 / 标题 / 链接 / Top2 链接 / Top3 链接 / 匹配度 / Condition / 中位数 / 历史最低 / 历史最高 / 历史均价 / 当前位置 / Trap 提示 / 标记已购等），并把配置写到 `~/.openclaw/data/price-check/config.json`。

如需关闭：编辑该 config，把 `feishu_sync.enabled` 改成 `false`。

## 配置

所有可选配置都在 `~/.openclaw/data/price-check/config.json` —— 完整字段参考 [config.example.json](config.example.json)。主要分组：

- `storage.cache_ttl_seconds` —— 查询缓存 TTL（默认 1800 秒）
- `history_provider.type` —— `local_db`（默认）或 `noop`
- `history_provider.min_query_history` —— market 历史价生效的最低查询次数（默认 3）
- `history_provider.min_goods_history` —— 商品历史价生效的最低快照数（默认 2）
- `feishu_sync.enabled` —— opt-in 开关（默认 false）
- `feishu_sync.base_token` / `table_id` / `lark_cli_profile` —— 由 `setup_feishu.py` 自动写入

## 本地数据布局

```
~/.openclaw/data/price-check/
├── price-check.db       # SQLite — 三张表：queries / price_snapshots / query_cache
└── config.json          # 可选 — 不存在时走默认值
```

直接用 sqlite3 命令查历史：

```bash
sqlite3 ~/.openclaw/data/price-check/price-check.db \
  "SELECT queried_at, query, verdict FROM queries ORDER BY id DESC LIMIT 20"

# 备份
cp ~/.openclaw/data/price-check/price-check.db ~/Backups/

# 重置（删除所有数据）
rm -rf ~/.openclaw/data/price-check/
```

## Roadmap

- **v0.5** —— SKU 关键词模糊匹配（如 "V12 plus" 与 query "V12 Pro" 同义识别）
- **v0.5** —— 运营商京东自营店单独识别一档（中国联通 / 移动 / 电信 —— 当前算可信但实际是合约机风险偏高）
- **v0.6** —— `best_deal.url` 改回按需拉取；飞书 Bitable 加价格历史可视化图
- **v1.0** —— `HistoryProvider` 接入外部数据源（慢慢买 / 什么值得买 / 京东价保 API 等）

## 隐私

- **所有数据本地保存**。SQLite 是本地文件，除非你主动启用飞书同步，否则不会有任何外部写入
- `config.json` 里可能含飞书 `base_token`，**不要**提交到版本控制（已在 `.gitignore` / 仓库路径外）
- 本 skill **不**追踪任何用户身份 / 购买记录 / PII

## License

MIT —— 详见 [LICENSE](LICENSE)。

## 致谢

- 上游数据层：[shopmind-price-compare](https://clawhub.com/skills/shopmind-price-compare) —— price-check 包装的实时数据爬虫
- 运行平台：[OpenClaw](https://openclaw.ai/) —— agent runtime
