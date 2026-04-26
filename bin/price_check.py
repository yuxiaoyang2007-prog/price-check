#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["aiohttp"]
# ///
"""
price-check v0.5.0 — 比价 + verdict + 购买链接 一体化（自包含数据层）

vs v0.4.1：
- F: 数据层从 shopmind-price-compare 内化进来（bin/_data_layer.py）
     不再依赖 shopmind 上游 skill；用户安装 price-check 即可，无需额外依赖
     上游归属信息保留在 _data_layer.py 顶部 + README Acknowledgements
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp

# 模块加载路径（让相对 import 工作）
_BIN_DIR = Path(__file__).parent
sys.path.insert(0, str(_BIN_DIR))

import _data_layer as data_layer  # noqa: E402  内化的 maishou88.com API client
import db                          # noqa: E402
import feishu_sync                 # noqa: E402

HEADERS = data_layer.HEADERS
PLATFORM_MAP = data_layer.PLATFORM_MAP


# ---------- HistoryProvider plugin interface ----------
class HistoryProvider:
    name: str = "abstract"

    def get_history(
        self,
        product_query: str,
        best_deal: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        raise NotImplementedError


class NoOpHistoryProvider(HistoryProvider):
    name = "noop"

    def get_history(self, product_query, best_deal=None):
        return None


class LocalDBHistoryProvider(HistoryProvider):
    """v0.4: 用本地 SQLite price_snapshots 表当历史价数据源。

    返回结构：
        {
            "provider": "local_db",
            "market": { ... } | None,          # 该 query 的市场趋势
            "best_deal_history": { ... } | None,  # best_deal 商品历次价格
            "trap": None | "..."                # 检测到的"先涨后降"提示
        }
    """

    name = "local_db"
    MIN_QUERY_HISTORY = 3
    MIN_GOODS_HISTORY = 2
    LOOKBACK_DAYS = 90

    def get_history(self, product_query, best_deal=None):
        market = self._market_history(product_query)
        deal = None
        if best_deal:
            deal = self._goods_history(
                best_deal.get("goodsId"),
                best_deal.get("price"),
                best_deal.get("shopName"),
                best_deal.get("title"),
            )

        if market is None and deal is None:
            return None

        trap = self._detect_trap(deal) if deal else None

        return {
            "provider": "local_db",
            "market": market,
            "best_deal_history": deal,
            "trap": trap,
        }

    def _market_history(self, query: str) -> Optional[dict[str, Any]]:
        rows = db.query_history_by_query(query, days=self.LOOKBACK_DAYS)
        if len(rows) < self.MIN_QUERY_HISTORY:
            return None

        prices = [r["best_deal_price"] for r in rows if r.get("best_deal_price")]
        medians = [r["stats_median"] for r in rows if r.get("stats_median")]
        if not prices and not medians:
            return None

        latest = rows[-1]
        latest_price = latest.get("best_deal_price")
        median_30d = round(statistics.median(medians), 2) if medians else None

        return {
            "queries_count": len(rows),
            "earliest": rows[0].get("queried_at"),
            "latest": latest.get("queried_at"),
            "best_deal_price_min": round(min(prices), 2) if prices else None,
            "best_deal_price_median": round(statistics.median(prices), 2) if prices else None,
            "best_deal_price_max": round(max(prices), 2) if prices else None,
            "stats_median_30d": median_30d,
            "current_vs_30d_median": (
                round(latest_price / median_30d, 3)
                if (latest_price and median_30d) else None
            ),
        }

    def _goods_history(
        self,
        goods_id: str,
        current_price: Optional[float],
        shop: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        # 先尝试 goodsId 精确匹配
        rows = db.query_history_by_goodsid(goods_id, days=self.LOOKBACK_DAYS)
        # shopmind 的 goodsId 含 session token（中间一段会变），精确匹配往往失效；
        # fallback 按 (shop + title 前 30 字符) 模糊匹配作为商品稳定指纹
        if len(rows) < self.MIN_GOODS_HISTORY and shop and title:
            title_prefix = title[:30]
            rows = db.query_history_by_signature(shop, title_prefix, days=self.LOOKBACK_DAYS)
        if len(rows) < self.MIN_GOODS_HISTORY:
            return None

        prices = [r["price"] for r in rows if r.get("price")]
        if not prices:
            return None

        lo = min(prices)
        hi = max(prices)
        avg = round(statistics.mean(prices), 2)

        # 当前价位置（用 best_deal 实时价 vs 历史分布）
        rank = "mid"
        if current_price is not None and len(prices) >= 2:
            sorted_prices = sorted(prices)
            q1 = sorted_prices[len(sorted_prices) // 4]
            q3 = sorted_prices[len(sorted_prices) * 3 // 4]
            if current_price <= q1:
                rank = "low"
            elif current_price >= q3:
                rank = "high"

        # 找最低 / 最高对应的 snapshot
        low_row = min(rows, key=lambda r: r["price"])
        high_row = max(rows, key=lambda r: r["price"])

        return {
            "goodsId": goods_id,
            "snapshots_count": len(rows),
            "earliest": rows[0]["snapshot_at"],
            "latest": rows[-1]["snapshot_at"],
            "low": {"price": lo, "date": low_row["snapshot_at"]},
            "high": {"price": hi, "date": high_row["snapshot_at"]},
            "avg": avg,
            "current_price": current_price,
            "current_rank": rank,
            "_series": [{"date": r["snapshot_at"], "price": r["price"]} for r in rows],
        }

    def _detect_trap(self, deal: dict[str, Any]) -> Optional[str]:
        """先涨后降识别（v0.1 简版）：
        近 7 天内出现峰值，且峰值 > 历史均价 × 1.15，
        且当前价仍 > 历史均价 → 疑似"先涨没降回"。
        """
        series = deal.get("_series") or []
        if len(series) < 3:
            return None

        avg = deal.get("avg") or 0
        if avg <= 0:
            return None

        prices = [s["price"] for s in series]
        max_price = max(prices)
        max_idx = prices.index(max_price)
        max_date = series[max_idx]["date"]
        current_price = deal.get("current_price") or prices[-1]

        # 峰值远高于均价
        if max_price <= avg * 1.15:
            return None
        # 当前价仍接近或高于均价（没真降）
        if current_price < avg * 1.05:
            return None
        # 峰值必须在最近几个 snapshot 里（"近期"刚涨）
        if max_idx < len(series) - 3:
            return None

        return (
            f"近期峰值 ¥{max_price:.2f}（{max_date[:10]}）后未充分降回；"
            f"当前 ¥{current_price:.2f} 仍高于历史均价 ¥{avg:.2f}"
        )


# ---------- 配置常量 ----------
OUTLIER_RATIO = 0.3
MIN_CLEAN = 5
RELEVANCE_THRESHOLD = 0.75
AMBIGUOUS_MODEL_COUNT = 3


CONDITION_RULES: list[tuple[str, list[str]]] = [
    ("bundle",                  ["套装", "组合装", "礼盒装", "+iPhone", "+iPad",
                                 "+AirPods", "+MacBook", "+Apple",
                                 "+保护壳", "+钢化膜", "+保护套"]),
    ("accessory",               ["配件", "支架", "充电支架", "充电底座", "底座",
                                 "保护套", "保护壳", "保护膜", "屏幕膜", "钢化膜",
                                 "贴膜", "皮套", "替换头", "替换",
                                 "除螨仪", "除螨头", "电池组件",
                                 "Dok", "Dok免打孔",
                                 "适用于", "兼容"]),
    ("refurbished",             ["翻新", "认证翻新", "官翻", "renewed", "二手", "9成新",
                                 "样机", "展示机", "演示机", "展品", "模型机"]),
    ("activation_questionable", ["需签收激活", "需现场激活", "已激活", "已拆封", "拆封"]),
    ("parallel_import",         ["港版", "美版", "日版", "韩版", "欧版", "海外版",
                                 "海外", "全球版", "国际版"]),
    ("trusted_domestic",        ["国行", "大陆版", "国行正品", "国行原封"]),
]

SUSPICIOUS_CONDITIONS = ("refurbished", "bundle", "activation_questionable", "accessory")


TRUSTED_SHOP_LITERALS = [
    "Apple产品京东自营",
    "苹果京东自营",
    "Apple官方旗舰店",
    "苹果官方旗舰店",
]

TRUSTED_SHOP_PATTERNS = [
    re.compile(r"京东自营"),
    re.compile(r"^.*官方旗舰店$"),
    re.compile(r"^Apple.*旗舰店$"),
    re.compile(r"^.*天猫官方旗舰店$"),
]


MODEL_PATTERNS = [
    re.compile(r"V\d+", re.IGNORECASE),
    re.compile(r"iPhone\s*\d+(?:\s*Pro\s*Max|\s*Pro|\s*Plus|\s*Mini)?", re.IGNORECASE),
    re.compile(r"Switch\s*\d*", re.IGNORECASE),
    re.compile(r"\bOLED\b|\bLite\b", re.IGNORECASE),
    re.compile(r"Galaxy\s*S\d{1,2}(?:\s*Ultra|\s*Plus)?", re.IGNORECASE),
    re.compile(r"Pixel\s*\d+", re.IGNORECASE),
]


# ---------- shopmind 数据层适配 ----------
async def fetch_items(
    keyword: str, source: str = "0", page: int = 1
) -> list[dict[str, Any]]:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        result = await data_layer.fetch_search_items(session, keyword, source=source, page=page)
    raw_items = result.get("items") or []
    return [_normalize_item(it, query=keyword) for it in raw_items]


async def _enrich_with_urls(targets: list[dict[str, Any]]) -> None:
    """并发拉 detail，把 buy_url / copy_cmd 塞进 target dict（in-place）。"""
    if not targets:
        return

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async def _one(t: dict[str, Any]) -> None:
            try:
                d = await data_layer.fetch_goods_detail(
                    session, t.get("goodsId"), source=t.get("source", "1"),
                )
                t["buy_url"] = d.get("buy_url") or None
                t["copy_cmd"] = d.get("copy_cmd") or None
            except Exception as e:
                print(f"[price-check] _enrich_with_urls failed for {t.get('goodsId')}: {e}",
                      file=sys.stderr)
                t.setdefault("buy_url", None)
                t.setdefault("copy_cmd", None)

        await asyncio.gather(*[_one(t) for t in targets])


def _normalize_item(raw: dict[str, Any], query: str = "") -> dict[str, Any]:
    title = raw.get("title") or ""
    shop_name = raw.get("shopName") or ""
    cond_hits = _condition_hits(title)
    return {
        "goodsId": raw.get("goodsId"),
        "source": str(raw.get("source")),
        "platform": raw.get("sourceName") or PLATFORM_MAP.get(str(raw.get("source")), "未知"),
        "title": title,
        "shopName": shop_name,
        "originalPrice": float(raw.get("originalPrice") or 0),
        "rawPrice": float(raw.get("price") or 0),
        "price": float(raw.get("finalPrice") or 0),
        "couponAmount": raw.get("couponAmount", 0),
        "saved": raw.get("saved", 0),
        "discount": raw.get("discount", "无折扣"),
        "hasCoupon": raw.get("hasCoupon", False),
        "monthSales": raw.get("monthSales", 0),
        "condition": _classify_condition(cond_hits),
        "condition_hits": cond_hits,
        "is_trusted_shop": _is_trusted_shop(shop_name),
        "relevance": _title_relevance(query, title),
        "buy_url": None,    # v0.3 enrich 后填
        "copy_cmd": None,   # 同上
    }


# ---------- condition 识别 ----------
def _condition_hits(title: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for cond_name, keywords in CONDITION_RULES:
        matched = [kw for kw in keywords if kw in title]
        if matched:
            hits[cond_name] = matched
    return hits


def _classify_condition(hits: dict[str, list[str]]) -> str:
    for cond_name, _ in CONDITION_RULES:
        if cond_name in hits:
            return cond_name
    return "unknown"


def _is_trusted_shop(shop_name: str) -> bool:
    if not shop_name:
        return False
    for literal in TRUSTED_SHOP_LITERALS:
        if literal in shop_name:
            return True
    for pat in TRUSTED_SHOP_PATTERNS:
        if pat.search(shop_name):
            return True
    return False


# ---------- 标题相关性 ----------
def _tokenize(query: str) -> list[str]:
    return [t.strip() for t in query.split() if t.strip()]


def _title_relevance(query: str, title: str) -> dict[str, Any]:
    if not query or not title:
        return {"score": 0.0, "matched": [], "missing": [], "ambiguous": False}

    tokens = _tokenize(query)
    if not tokens:
        return {"score": 0.0, "matched": [], "missing": [], "ambiguous": False}

    title_lower = title.lower()
    matched: list[str] = []
    missing: list[str] = []
    for t in tokens:
        t_lower = t.lower()
        if t_lower in title_lower:
            matched.append(t)
        elif t_lower.endswith("g") and not t_lower.endswith("gb") and (t_lower + "b") in title_lower:
            matched.append(t)
        elif t_lower.endswith("gb") and t_lower[:-1] in title_lower:
            matched.append(t)
        else:
            missing.append(t)

    score = round(len(matched) / len(tokens), 2)

    all_models: list[str] = []
    for pat in MODEL_PATTERNS:
        all_models.extend(pat.findall(title))
    distinct_models = {m.lower().replace(" ", "") for m in all_models if m}
    ambiguous = len(distinct_models) >= AMBIGUOUS_MODEL_COUNT

    return {
        "score": score,
        "matched": matched,
        "missing": missing,
        "ambiguous": ambiguous,
    }


# ---------- 价格分布、outlier 剔除 ----------
def _price_stats(items: list[dict[str, Any]]) -> dict[str, float]:
    prices = [i["price"] for i in items if i["price"] > 0]
    if not prices:
        return {"count": 0, "min": 0.0, "max": 0.0, "median": 0.0, "stdev": 0.0}
    return {
        "count": len(prices),
        "min": min(prices),
        "max": max(prices),
        "median": round(statistics.median(prices), 2),
        "stdev": round(statistics.stdev(prices), 2) if len(prices) > 1 else 0.0,
    }


def _filter_outliers(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    valid = [i for i in items if i["price"] > 0]
    if len(valid) < 3:
        return list(items), [], 0.0
    prices = [i["price"] for i in valid]
    raw_med = statistics.median(prices)
    if raw_med <= 0:
        return list(items), [], 0.0
    threshold = raw_med * OUTLIER_RATIO
    clean = [i for i in items if i["price"] >= threshold]
    removed = [i for i in items if i["price"] < threshold]
    clean.sort(key=lambda x: x["price"])
    for idx, item in enumerate(clean):
        item["rank"] = idx + 1
    return clean, removed, round(threshold, 2)


# ---------- best_deal 选择 ----------
def _select_best_deal(
    clean_items: list[dict[str, Any]]
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    flagged = [i for i in clean_items if i["condition"] in SUSPICIOUS_CONDITIONS]
    flagged_ids = {i["goodsId"] for i in flagged}

    low_relevance = [
        i for i in clean_items
        if i["goodsId"] not in flagged_ids
        and (i["relevance"]["score"] < RELEVANCE_THRESHOLD or i["relevance"]["ambiguous"])
    ]
    low_rel_ids = {i["goodsId"] for i in low_relevance}

    candidates = [
        i for i in clean_items
        if i["goodsId"] not in flagged_ids and i["goodsId"] not in low_rel_ids
    ]

    tier1 = [i for i in candidates if i["is_trusted_shop"] and i["condition"] in ("trusted_domestic", "unknown")]
    tier2 = [i for i in candidates if i["is_trusted_shop"] and i["condition"] == "parallel_import"]
    tier3 = [i for i in candidates if not i["is_trusted_shop"] and i["condition"] == "trusted_domestic"]

    for tier in (tier1, tier2, tier3):
        if tier:
            best = min(tier, key=lambda x: x["price"])
            return (
                {
                    "platform": best["platform"],
                    "shopName": best["shopName"],
                    "price": best["price"],
                    "title": best["title"],
                    "condition": best["condition"],
                    "is_trusted_shop": best["is_trusted_shop"],
                    "relevance": best["relevance"],
                    "goodsId": best["goodsId"],
                    "source": best["source"],
                    "buy_url": None,    # v0.3 enrich 后填
                    "copy_cmd": None,
                    "url": None,         # 兼容旧字段名
                },
                flagged,
                low_relevance,
            )

    return None, flagged, low_relevance


# ---------- verdict ----------
def compute_verdict(
    best_deal: Optional[dict[str, Any]],
    stats: dict[str, float],
    history: Optional[dict[str, Any]],
) -> tuple[str, str]:
    n = int(stats["count"])
    med = stats["median"]

    if n == 0:
        return "无数据", "剔除后无可用价格记录"
    if med <= 0:
        return "无数据", f"中位数为 0（n={n}），无法判断"
    if best_deal is None:
        return (
            "数据质量不足，无法可信推荐",
            f"剔除噪音后 {n} 条中无满足相关性 + 信任层条件的候选",
        )

    price = best_deal["price"]
    rel = best_deal["relevance"]
    rel_str = f"匹配度 {int(rel['score'] * 100)}% ({len(rel['matched'])}/{len(rel['matched']) + len(rel['missing'])} token)"
    if rel["missing"]:
        rel_str += f"，缺 {'/'.join(rel['missing'])}"

    ratio = price / med
    locator = f"{best_deal['platform']}/{best_deal['shopName']}".rstrip("/")

    if history and history.get("trap"):
        return "别买", f"历史{history['trap']}"

    # 历史价附加信息（如有）
    hist_str = ""
    rank = None
    deal_hist = (history or {}).get("best_deal_history") or {}
    if deal_hist:
        rank = deal_hist.get("current_rank")
        avg = deal_hist.get("avg")
        lo_h = (deal_hist.get("low") or {}).get("price")
        hi_h = (deal_hist.get("high") or {}).get("price")
        if rank and avg is not None:
            rank_zh = {"low": "历史低位", "mid": "历史中位", "high": "历史高位"}.get(rank, rank)
            hist_str = f"；该商品历史 ¥{lo_h}–¥{hi_h}（均 ¥{avg}），当前处于 {rank_zh}"

    if ratio <= 0.85:
        pct = (1 - ratio) * 100
        verdict = "强烈推荐"
        return (verdict,
                f"可信最低价 ¥{price:.2f}（{locator}）比 {n} 平台中位数 ¥{med:.2f} 低 {pct:.1f}%；{rel_str}{hist_str}")
    if ratio <= 0.95:
        pct = (1 - ratio) * 100
        # 历史低位时升档为强烈推荐
        if rank == "low":
            return ("强烈推荐",
                    f"可信最低价 ¥{price:.2f}（{locator}）比 {n} 平台中位数 ¥{med:.2f} 低 {pct:.1f}%，且处于该商品历史低位；{rel_str}{hist_str}")
        return ("可以买",
                f"可信最低价 ¥{price:.2f}（{locator}）比 {n} 平台中位数 ¥{med:.2f} 低 {pct:.1f}%；{rel_str}{hist_str}")
    if ratio <= 1.0:
        pct = (1 - ratio) * 100
        # 历史高位时降档为再等等（已经是再等等，但加强 reason）
        return ("再等等",
                f"可信最低价 ¥{price:.2f}（{locator}）接近 {n} 平台中位数 ¥{med:.2f}（仅低 {pct:.1f}%）；{rel_str}{hist_str}")
    pct = (ratio - 1) * 100
    return ("再等等",
            f"可信最低价 ¥{price:.2f}（{locator}）高于 {n} 平台中位数 ¥{med:.2f} {pct:.1f}%；{rel_str}{hist_str}")


def compute_trap_warning(
    removed: list[dict[str, Any]],
    clean_count: int,
    threshold: float,
    flagged_count: int,
    low_relevance_count: int,
    best_deal: Optional[dict[str, Any]],
    clean_items: list[dict[str, Any]],
    flagged_items: list[dict[str, Any]],
    low_relevance_items: list[dict[str, Any]],
) -> Optional[str]:
    parts: list[str] = []

    if removed:
        n = len(removed)
        lo = min(i["price"] for i in removed)
        hi = max(i["price"] for i in removed)
        if clean_count < MIN_CLEAN:
            parts.append(
                f"⚠️ 剔除前 {n} 条价格远低于中位数（< ¥{threshold:.2f}，最低 ¥{lo:.2f}），"
                f"疑似配件/同关键词噪音；剔除后仅剩 {clean_count} 条，"
                f"不足 {MIN_CLEAN} 条最低样本，verdict 不可信。"
                f"建议加更精确关键词重跑。"
            )
        else:
            parts.append(f"⚠️ 已自动剔除 {n} 条配件/噪音商品（¥{lo:.2f}–¥{hi:.2f}，阈值 ¥{threshold:.2f}）")

    if flagged_count:
        parts.append(f"⚠️ 已过滤 {flagged_count} 条配件/翻新/套装/激活可疑商品")

    if low_relevance_count:
        ambiguous_n = sum(1 for i in low_relevance_items if i["relevance"]["ambiguous"])
        msg = f"⚠️ 已过滤 {low_relevance_count} 条标题不匹配的商品"
        if ambiguous_n:
            msg += f"，其中 {ambiguous_n} 条多型号堆砌"
        parts.append(msg)

    if best_deal:
        flagged_ids = {i["goodsId"] for i in flagged_items}
        low_rel_ids = {i["goodsId"] for i in low_relevance_items}
        safe = [
            i for i in clean_items
            if i["goodsId"] not in flagged_ids and i["goodsId"] not in low_rel_ids
        ]
        cheaper_untrusted = sorted(
            [i for i in safe if not i["is_trusted_shop"] and i["price"] < best_deal["price"]],
            key=lambda x: x["price"],
        )
        if cheaper_untrusted:
            cheapest = cheaper_untrusted[0]
            parts.append(
                f"💡 还有 {len(cheaper_untrusted)} 条更低价候选未进 best_deal："
                f"最低 ¥{cheapest['price']:.2f}（{cheapest['shopName'] or '店铺空'}），"
                f"因不是可信店铺被设计跳过。"
            )

    if not parts:
        return None
    return " ".join(parts)


# ---------- 主流程 ----------
def _safe_top_n(
    clean_items: list[dict[str, Any]],
    flagged: list[dict[str, Any]],
    low_relevance: list[dict[str, Any]],
    n: int = 3,
) -> list[dict[str, Any]]:
    """三层过滤后的安全候选 Top N（按 price 升序）。"""
    flagged_ids = {i["goodsId"] for i in flagged}
    low_rel_ids = {i["goodsId"] for i in low_relevance}
    safe = [
        i for i in clean_items
        if i["goodsId"] not in flagged_ids and i["goodsId"] not in low_rel_ids
    ]
    safe.sort(key=lambda x: x["price"])
    return safe[:n]


def _make_history_provider() -> HistoryProvider:
    """根据 config.json 选择 history provider；默认 local_db。"""
    try:
        config = feishu_sync.load_config()
        hp = config.get("history_provider") or {}
        ptype = hp.get("type", "local_db")
        if ptype == "noop":
            return NoOpHistoryProvider()
        return LocalDBHistoryProvider()
    except Exception:
        return LocalDBHistoryProvider()


async def run(query: str, source: str = "0", page: int = 1, no_cache: bool = False) -> dict[str, Any]:
    t0 = time.time()

    # 30min 缓存命中
    if not no_cache:
        cached = db.cache_get(query, source, page)
        if cached:
            cached["_meta"]["from_cache"] = True
            return cached

    items = await fetch_items(query, source=source, page=page)
    stats_raw = _price_stats(items)

    clean_items, removed, threshold = _filter_outliers(items)
    stats = _price_stats(clean_items)

    history_provider: HistoryProvider = _make_history_provider()

    best_deal: Optional[dict[str, Any]] = None
    flagged: list[dict[str, Any]] = []
    low_relevance: list[dict[str, Any]] = []

    if not items:
        verdict, verdict_reason = "无数据", "shopmind 未返回任何商品记录"
        history = None
    elif removed and len(clean_items) < MIN_CLEAN:
        verdict = "数据噪音过多，无法判断"
        verdict_reason = (
            f"原始 {stats_raw['count']} 条中 {len(removed)} 条疑似噪音（< ¥{threshold:.2f}），"
            f"剔除后仅 {len(clean_items)} 条，低于 {MIN_CLEAN} 条最低样本"
        )
        flagged = [i for i in clean_items if i["condition"] in SUSPICIOUS_CONDITIONS]
        low_relevance = [
            i for i in clean_items
            if i["relevance"]["score"] < RELEVANCE_THRESHOLD or i["relevance"]["ambiguous"]
        ]
        history = None
    else:
        best_deal, flagged, low_relevance = _select_best_deal(clean_items)
        # v0.4: 在 best_deal 已知后才查 history（含 best_deal 自己的历史）
        history = history_provider.get_history(query, best_deal=best_deal)
        verdict, verdict_reason = compute_verdict(best_deal, stats, history)

    # v0.3 改造 A+B：并发拉 best_deal + Top 3 安全候选 的 URL
    targets: list[dict[str, Any]] = []
    if best_deal:
        targets.append(best_deal)
    safe_top = _safe_top_n(clean_items, flagged, low_relevance, n=3)
    # safe_top[0] 跟 best_deal 可能是同一个商品，但 dict 引用不同；都拉一遍无所谓（短期缓存层会去重）
    for it in safe_top:
        if best_deal and it.get("goodsId") == best_deal.get("goodsId"):
            continue
        targets.append(it)
    await _enrich_with_urls(targets)

    trap = compute_trap_warning(
        removed, len(clean_items), threshold,
        len(flagged), len(low_relevance),
        best_deal, clean_items, flagged, low_relevance,
    )

    duration_ms = int((time.time() - t0) * 1000)

    result = {
        "product": query,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "best_deal": best_deal,
        "history_summary": history,
        "all_platforms": items,
        "removed_outliers": removed,
        "flagged_items": flagged,
        "low_relevance_items": low_relevance,
        "stats": stats,
        "stats_raw": stats_raw,
        "trap_warning": trap,
        "_meta": {
            "skill": "price-check",
            "version": "0.5.0",
            "history_provider": history_provider.name,
            "data_source": "internalized maishou88.com client (derived from shopmind-price-compare by xiaohaook)",
            "outlier_filter": f"price < raw_median × {OUTLIER_RATIO}",
            "min_clean_samples": MIN_CLEAN,
            "relevance_threshold": RELEVANCE_THRESHOLD,
            "ambiguous_model_count": AMBIGUOUS_MODEL_COUNT,
            "condition_classifier": "title-keyword (v0.3)",
            "trusted_shop_classifier": "shopName-pattern (v0.3)",
            "suspicious_conditions": list(SUSPICIOUS_CONDITIONS),
            "duration_ms": duration_ms,
            "from_cache": False,
        },
    }

    # 持久化（写库失败不影响输出）
    db.persist_query(query, source, page, result, duration_ms)
    db.cache_set(query, source, page, result)

    # 飞书同步（opt-in）—— fire-and-forget
    try:
        feishu_sync.sync_query_to_feishu(query, result)
    except Exception as e:
        print(f"[price-check] feishu sync error: {e}", file=sys.stderr)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="price-check v0.3.0 — 比价 + verdict + 购买链接 一体化"
    )
    parser.add_argument("query", help="商品搜索词（中英文用空格分隔，例：'iPhone 16 Pro 256G'）")
    parser.add_argument("--source", default="0", help="平台编号（0=全部）")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--no-cache", action="store_true",
                        help="忽略 30min 缓存，强制重打 shopmind API")
    args = parser.parse_args()

    result = asyncio.run(run(args.query, source=args.source, page=args.page, no_cache=args.no_cache))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
