# -*- coding: utf-8 -*-
"""
キーワード生成・Yahoo判定・傾向分析の移植
GAS版: キーワード生成処理_ / Yahoo判定処理_ / 傾向分析処理_ の移植
データはスプレッドシートではなくJSONファイル（data/keywords.json）で管理
"""
import re
import random
import time
import logging
import requests
from config import (
    CONFIG, 除外キーワード, 除外サイト, SNSサイト, 掲示板サイト, 強豪サイト,
    MAIN_WORDS, FORMATS, get_current_year_context, generate_dynamic_categories,
    get_marketing_strategy,
)
from ai_client import call_ai_with_fallback, extract_text

log = logging.getLogger(__name__)

文字数除外ドメイン = [
    "reddit.com", "5ch.net", "2ch.net", "chiebukuro.yahoo.co.jp",
    "oshiete.goo.ne.jp", "okwave.jp", "twitter.com", "x.com",
    "instagram.com", "facebook.com", "youtube.com", "tiktok.com",
    "bbs", "detail.chiebukuro",
]


def generate_keywords(existing_keywords):
    """AIでSEOキーワードを30個生成。existing_keywordsは既存リスト（重複除外用）"""
    strategy = get_marketing_strategy()
    year_context = get_current_year_context()
    target_words = strategy["bestWords"] if random.random() < 0.7 else MAIN_WORDS
    categories = generate_dynamic_categories()

    main_word = random.choice(target_words)
    category = random.choice(categories)
    fmt = random.choice(FORMATS)

    prompt = f"""あなたはSEO専門のマーケターです。以下の条件で、人間が実際に検索する「自然な検索キーワード」を30個生成してください。

【メインワード】{main_word}
【テーマ】{category}
【切り口】{fmt['type']}（{fmt['instruction']}）
【対象年】{year_context}

【絶対ルール（厳守）】
1. 「{main_word}」を必ず含めること
2. 他のメインワード（Fintokei、プロップファーム、MT5等）は混ぜないこと
3. 出力は「1行に1キーワード」のみ。記号・番号・解説は一切不要
4. キーワードは2〜4語のスペース区切り
5. 「〜とは」「〜する方法」などの文章形式は不可

【良い出力例】
{main_word} スマホアプリ 設定
{main_word} 週末持ち越し リスク
{main_word} 5分足 スキャルピング

それでは、30個出力してください。"""

    res = call_ai_with_fallback(prompt, 1500)
    text = extract_text(res)
    if not text:
        log.error("❌ AIがテキストを返しませんでした")
        return []

    lines = text.split("\n")
    candidates = []
    for line in lines:
        kw = re.sub(r"^[\d.\s・\-*]+", "", line).strip()
        if (len(kw) >= 5 and main_word in kw
                and not kw.endswith("ス") and not kw.endswith("の") and not kw.endswith("法")):
            candidates.append(kw)

    new_keywords = [kw for kw in candidates if kw not in existing_keywords]
    log.info(f"✅ {len(new_keywords)}件の新規キーワードを生成")
    return new_keywords


def yahoo_check(kw):
    """Yahoo検索結果からキーワードの合否を判定"""
    for w in 除外キーワード:
        if w in kw:
            return {"合格": False, "理由": "❌ 除外KW", "urls": []}
    try:
        res = requests.get(
            f"https://search.yahoo.co.jp/search?p={requests.utils.quote(kw)}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0"},
            timeout=20,
        )
        html = res.text
        urls = []
        for match in re.finditer(r'<a\s+[^>]*href="(https?://[^"]+)"', html):
            url = match.group(1)
            if "yahoo" not in url and url not in urls:
                urls.append(url)
            if len(urls) >= 30:
                break

        if not urls:
            return {"合格": False, "理由": "❌ 構造解析エラー", "urls": []}

        top = urls[:5]
        has_sns = False
        has_board = False
        top_strong = False
        for j, url in enumerate(top[:3]):
            domain = url.split("/")[2] if len(url.split("/")) > 2 else ""
            for e in 除外サイト:
                if e in url:
                    return {"合格": False, "理由": "❌ 除外サイト上位", "urls": top}
            for s in SNSサイト:
                if s in domain:
                    has_sns = True
            for b in 掲示板サイト:
                if b in url:
                    has_board = True
            if j == 0:
                for s in 強豪サイト:
                    if s in domain:
                        top_strong = True

        if top_strong:
            return {"合格": False, "理由": "❌ 1位が強豪", "urls": top}
        if has_sns or has_board:
            kind = "SNS" if has_sns else "掲示板"
            return {"合格": True, "理由": f"✅ 合格（{kind}あり）", "urls": top}
        return {"合格": False, "理由": "❌ SNS/掲示板なし", "urls": top}
    except Exception:
        return {"合格": False, "理由": "❌ エラー", "urls": []}


def _is_length_excluded(url):
    return any(d in url for d in 文字数除外ドメイン)


def analyze_trend(kw, urls):
    """上位サイトを取得して傾向分析・平均文字数を算出"""
    texts = []
    length_list = []

    for i, url in enumerate(urls[:3]):
        try:
            res = requests.get(
                url, timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            html = res.text
            cleaned = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
            cleaned = re.sub(r"<style[\s\S]*?</style>", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"<noscript[\s\S]*?</noscript>", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"<header[\s\S]*?</header>", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"<footer[\s\S]*?</footer>", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"<nav[\s\S]*?</nav>", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"<aside[\s\S]*?</aside>", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"<[^>]+>", " ", cleaned)
            cleaned = (cleaned.replace("&nbsp;", " ").replace("&lt;", "<")
                       .replace("&gt;", ">").replace("&amp;", "&"))
            cleaned = re.sub(r"&[a-z]+;", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()

            jp_parts = re.findall(r"[^\x00-\x7F\s][^。！？\n]{5,}[。！？」』]", cleaned)
            jp_text = " ".join(jp_parts)
            measure_text = jp_text if len(jp_text) > 200 else cleaned
            texts.append(measure_text[:600])

            if _is_length_excluded(url):
                log.info(f"  >> URL{i + 1}: 掲示板/SNS系 → 文字数除外: {url.split('/')[2] if len(url.split('/')) > 2 else url}")
                continue
            if len(measure_text) < 200 or len(measure_text) > 50000:
                log.info(f"  >> URL{i + 1}: 異常値のため文字数除外（{len(measure_text)}字）")
                continue

            length_list.append(len(measure_text))
            log.info(f"  >> URL{i + 1}: 日本語{len(jp_text)}字 / 全体{len(cleaned)}字 ✅")
        except Exception as e:
            log.info(f"  >> URL{i + 1} 取得エラー: {e}")

    avg_length = round(sum(length_list) / len(length_list)) if length_list else 0
    log.info(f"  >> 文字数計測: {len(length_list)}件 / 平均{avg_length}字")

    if not texts:
        return {"傾向テキスト": "-", "文字数平均": 0}

    prompt = (
        "以下の上位サイトの内容を分析し、記事作成の傾向と対策を【150字以内】で要約してください。\n"
        "必ず150字以内に収めること。箇条書き不要、文章でまとめること。\n"
        f"キーワード：{kw}\n" + "\n---\n".join(texts)
    )
    res = call_ai_with_fallback(prompt, 300)
    trend_text = extract_text(res)[:200] if res else "-"

    return {"傾向テキスト": trend_text, "文字数平均": avg_length}
