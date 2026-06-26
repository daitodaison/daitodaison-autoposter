# -*- coding: utf-8 -*-
"""
メイン記事生成エントリポイント（GitHub Actions用）
GAS版の「全ステップ_トリガー起動」を1回の実行で順番に行う形に統合：
  1. キーワード生成（待機中が少ない場合のみ）
  2. Yahoo判定（待機中キーワードを判定）
  3. 傾向分析（合格キーワードの上位サイトを分析）
  4. 記事生成（1本だけ。GitHub Actions実行時間制限を考慮）
  5. 画像生成
  6. キューに投入（既存のposters/run_all.pyが拾う）

既存の run_all.py からは `python generator/generate.py` として呼ばれる想定。
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_store
from keyword_finder import generate_keywords, yahoo_check, analyze_trend
from article_writer import write_article, determine_target_length
from image_generator import generate_image_for_article

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MAX_YAHOO_CHECKS_PER_RUN = 10      # 1回の実行で判定するキーワード数の上限
MAX_TREND_ANALYSIS_PER_RUN = 5     # 1回の実行で傾向分析するキーワード数の上限
MIN_WAITING_KEYWORDS = 15          # これより少ない場合は新規キーワードを生成


def step1_generate_keywords():
    log.info("▶️ STEP1: キーワード生成チェック")
    waiting = data_store.count_waiting_keywords()
    if waiting >= MIN_WAITING_KEYWORDS:
        log.info(f"待機中KWが十分あるためスキップ（{waiting}件）")
        return
    keywords = data_store.load_keywords()
    existing = [k["kw"] for k in keywords]
    new_kws = generate_keywords(existing)
    if new_kws:
        data_store.add_new_keywords(new_kws)
    else:
        log.warning("⚠️ 新規KWが見つかりませんでした")


def step2_yahoo_check():
    log.info("▶️ STEP2: Yahoo判定")
    waiting = data_store.get_keywords_by_status("待機中")
    processed = 0
    passed = 0
    for item in waiting[:MAX_YAHOO_CHECKS_PER_RUN]:
        kw = item["kw"]
        log.info(f"【判定中】{kw}")
        result = yahoo_check(kw)
        if "ランクイン" in result["理由"]:
            status = "判定済みランクイン"
        elif result["合格"]:
            status = "判定済み合格"
        else:
            status = "判定済み圏外"
        data_store.update_keyword_status(kw, status, result["理由"], result["urls"])
        if result["合格"]:
            passed += 1
        processed += 1
        import time
        time.sleep(2)
    log.info(f"✅ Yahoo判定完了: 処理{processed}件 / 合格{passed}件")


def step3_trend_analysis():
    log.info("▶️ STEP3: 傾向分析")
    targets = data_store.get_keywords_by_status("判定済み合格", "判定済みランクイン", "エラー再試行")
    targets = [t for t in targets if not t.get("trend") or t["trend"] in ("-", "")]
    processed = 0
    for item in targets[:MAX_TREND_ANALYSIS_PER_RUN]:
        kw = item["kw"]
        urls = item.get("urls", [])
        result = analyze_trend(kw, urls)
        data_store.update_keyword_trend(kw, result["傾向テキスト"], result["文字数平均"])
        processed += 1
        import time
        time.sleep(3)
    log.info(f"✅ 傾向分析完了: {processed}件")


def step4_write_one_article():
    log.info("▶️ STEP4: 記事生成（1本）")
    used_topics = data_store.get_used_topics()

    candidates = data_store.get_keywords_by_status("判定済み合格", "判定済みランクイン", "エラー再試行")
    candidates = [c for c in candidates if c["kw"] not in used_topics]

    if not candidates:
        log.warning("⚠️ 執筆対象のキーワードがありません（Yahoo判定・傾向分析が先に必要）")
        return None

    target_length = determine_target_length(
        [a["body"] for a in data_store.load_articles_db()] or None
    )

    for item in candidates:
        kw = item["kw"]
        try:
            article = write_article(kw, target_length)
        except Exception as e:
            log.error(f"❌ 記事生成エラー: {kw} - {e}")
            data_store.update_keyword_status(kw, "エラー再試行")
            continue

        if article is None:
            data_store.update_keyword_status(kw, "エラー再試行")
            continue

        data_store.save_article_to_db(kw, article["title"], article["body"], article["score"])
        data_store.update_keyword_status(kw, "執筆完了")
        return article

    log.warning("⚠️ 全候補で記事生成に失敗しました")
    return None


def step5_generate_image(kw):
    log.info("▶️ STEP5: 画像生成")
    try:
        return generate_image_for_article(kw)
    except Exception as e:
        log.error(f"❌ 画像生成エラー: {e}")
        return None


def step6_push_to_queue(article, image_path):
    log.info("▶️ STEP6: キューに投入")
    data_store.push_to_queue(article["title"], article["body"], article["topic"], image_path)


def run():
    log.info("=== 記事生成パイプライン 開始 ===")

    step1_generate_keywords()
    step2_yahoo_check()
    step3_trend_analysis()

    article = step4_write_one_article()
    if article is None:
        log.warning("今回は新しい記事を生成できませんでした（次回実行時にキーワード調査が進みます）")
        return

    image_path = step5_generate_image(article["topic"])
    step6_push_to_queue(article, image_path)

    log.info("=== 記事生成パイプライン 完了 ===")


if __name__ == "__main__":
    run()
