# -*- coding: utf-8 -*-
"""
article_writer.py
generate.py が期待する関数名に合わせた実装:
  - write_article(kw, target_length) -> dict
  - determine_target_length(bodies) -> int
GAS版の _セクション生成 / _記事後処理 / 禁止ルールを完全移植
"""

import re
import time
import logging
from ai_client import call_ai_with_fallback, call_ai_section
from templates import select_template, determine_goal, get_cta
from text_processing import post_process_article, detect_abstract_phrases, is_article_acceptable

log = logging.getLogger(__name__)

# ================================================================
# GAS版ライター設定（禁止ルール完全移植）
# ================================================================
WRITER_BASE_PROMPT = """
【禁止（最優先・絶対厳守）】
- 同じ意味の文を2回書くな。1つの事実は1文で終わらせろ。
- 「〜がわかりました」は1記事で3回まで。
- 「私は〜」で始まる文を連続して3文以上書くな。
- 「大きな影響を与える」「重要性を再認識」は使うな。
- 中国語・韓国語の漢字は絶対に使わないこと（例: 「检討」→「検討」「收入」→「収入」「市场」→「市場」）
- 架空の収益額・勝率・月収は絶対に書かないこと（「月収20万」「勝率70%」「月5万稼いだ」は禁止）
- 「合格した」「パスした」という表現は禁止
- 「FX歴○年」「トレード歴○年」などの架空経歴は書かないこと
- 「絶対に稼げる」「誰でも簡単」「リスクゼロ」は使わないこと
- 数字はFintokeiの実際のルール（日次損失・最大損失・ロット制限）のみ使用可
- 「お客様」は使わず「あなた」と書く

【書く人物 daito のプロフィール】
- 製造現場・接客・音楽・輸出入・ライター・ゲーム開発など10以上の職を経験
- 現在Fintokeiに挑戦中。FXワールドチャレンジで2587人中61位（上位2.3%）達成
- 勝率40%でも利益を出せるスナイパー型スキャルピング手法を使用
- 口調は「です/ます」のみ。「だ。」「である。」は一切使わない
- 一人称は「私」。上から目線なし、対等な立場で語る

【文体ルール（絶対）】
- 「ます。」「です。」調で統一
- 「だ。」「である。」で終わる文は全て書き直すこと
- スマホで読みやすいよう2〜3文ごとに改行を入れる
- 専門用語は初出時に必ず1文で説明を添える
- セクション名の見出し（##）から書き始める
- 「はい」「続きを書きます」等のメタ発言は一切不要
"""


def determine_target_length(bodies=None) -> int:
    """
    generate.py が呼ぶ関数。過去記事の平均文字数から目標文字数を算出。
    bodies: list of str（過去記事の本文リスト）or None
    """
    default = 1800
    if not bodies:
        log.info(f"  >> 目標文字数: {default}字（デフォルト）")
        return default

    valid = [len(b) for b in bodies if b and len(b) > 200]
    if not valid:
        return default

    avg = sum(valid) // len(valid)
    target = max(1800, min(3000, avg))
    log.info(f"  >> 過去記事の平均文字数から目標を算出: {target}字")
    return target


def write_article(kw: str, target_length: int = 1800) -> dict:
    """
    generate.py が呼ぶメイン関数。
    戻り値: {"title": str, "body": str, "score": int, "topic": str} or None
    """
    log.info(f"***執筆中*** {kw}")
    log.info(f"  >> 目標文字数: {target_length}字")

    # タイトル生成
    title = _generate_title(kw)

    # テンプレート選択
    template = select_template(kw)

    # セクション構成取得
    sections = template["構成"](kw, target_length)
    body = title + "\n\n"

    # セクション生成ループ
    for section in sections:
        sec_name = section["名前"]
        min_chars = int(target_length * section["比率"])
        max_chars = int(min_chars * 1.2)
        instruction = section["指示"]
        emotion = section.get("感情設計", "")

        log.info(f"  >> {sec_name}（最低{min_chars}字）")
        sec_text = _generate_section(kw, sec_name, instruction, min_chars, body, max_chars, emotion)
        body += sec_text + "\n\n"
        log.info(f"  >> 累計: {len(body)}文字")
        log.info(f"⏸ 次セクションまで待機...")
        time.sleep(15)

    # ゴール判定・ログ
    goal_result = determine_goal(kw)
    goal = goal_result["goal"]
    log.info(f"  >> ゴール: ***{goal_result['key']}*** score={goal_result.get('score',1)} → {goal['url']}")

    # 抽象表現チェック・具体化
    abstract_hits = detect_abstract_phrases(body)
    if abstract_hits["count"] >= 3:
        log.warning(f"⚠️ 抽象表現を{abstract_hits['count']}箇所検出: {', '.join(abstract_hits['list'])}")
        log.info(f"    🔍 抽象表現を検出（{abstract_hits['count']}箇所）: 具体化リライトを実行")
        body = _rewrite_abstract(body, kw)
        log.info("✅ 抽象表現の具体化完了")

    # 後処理（簡体字修正・文体統一・重複除去）
    body = post_process_article(body)

    # 末尾CTA挿入
    cta = get_cta(kw, "末尾")
    body = body.strip() + "\n\n" + cta

    # 品質チェック
    passed, quality = is_article_acceptable(body)
    log.info(f"  >> 品質スコア: {quality['score']}点")

    if not passed:
        log.warning(f"  >> ⚠️ 品質基準未達（{quality['score']}点）: {quality['issues']}")
        return None

    log.info(f"  >> ✅ 完了: {kw}（{len(body)}字 / 品質{quality['score']}点）")

    return {
        "title": title,
        "body": body,
        "score": quality["score"],
        "topic": kw,
    }


def _generate_title(kw: str) -> str:
    """GAS版 _タイトル生成 の移植"""
    prompt = f"""あなたは魂を揺さぶるライターです。以下のキーワードを使い、読者が「これは自分のための記事だ」と確信するタイトルを1つ作ってください。

【キーワード】{kw}

【タイトルの鉄則】
1. 形式:「{kw} ｜ サブタイトル」
2. キーワードの後は必ず「半角スペース ｜ 半角スペース」を挟むこと
3. サブタイトルは、daitoの経歴（10の職を極めた・規律の聖域・引き止めを振り切った決断など）を感じさせる重みと信頼感のある言葉にすること
4. 32文字〜45文字程度にすること
5. 中国語・韓国語の漢字（检討・收入・市场など）は絶対に使わないこと
6. 架空の数字・成果を含めないこと

出力例: {kw} ｜ 10の職を極めた私が辿り着いた「規律の聖域」

タイトルを1行だけ出力してください（説明・コメント不要）:"""

    result = call_ai_with_fallback(prompt, 200)
    if result:
        title = re.sub(r'^#+\s*', '', result.strip()).replace('"', '').replace("'", '')
        title = post_process_article(title)
        return title

    log.warning("⚠️ タイトル生成失敗 → フォールバック使用")
    return f"{kw} ｜ 正直な現場目線でまとめました"


def _generate_section(kw: str, section_name: str, instruction: str,
                       min_chars: int, prev_text: str,
                       max_chars: int = None, emotion: str = "") -> str:
    """GAS版 _セクション生成 の完全移植"""
    if max_chars is None:
        max_chars = int(min_chars * 1.2)

    # 直前テキストから禁止フレーズを抽出
    sentences = [s.strip()[:20] for s in prev_text.split("。") if len(s.strip()) > 15]
    forbidden = "、".join(sentences[-10:]) if sentences else "なし"

    prompt = f"""{WRITER_BASE_PROMPT}

【以下のフレーズは前のセクションで使用済み・完全禁止】
{forbidden}

【今回書くセクション】
キーワード: {kw}
セクション名: {section_name}
執筆指示: {instruction}
感情設計: {emotion}

【文字数ルール（最重要・厳守）】
・最低: {min_chars}文字
・上限: {max_chars}文字
・{max_chars}文字に達したら、その文を「。」で終わらせて即終了すること

【直前の文章の末尾（ここから自然につなげること）】
{prev_text[-200:] if prev_text else "（記事の書き出しです）"}

それでは執筆してください（見出し「{section_name}」から書き始めること）:"""

    text = ""
    for attempt in range(1, 4):
        result = call_ai_section(prompt, 4096, section_name)
        if not result:
            log.warning(f"    [{section_name}] API失敗 試行{attempt}")
            continue

        candidate = re.sub(
            r'^(はい[、。]?|続きを書きます[。]?|承知しました[。]?|わかりました[。]?)\s*',
            '', result
        ).strip()
        candidate = _natural_cut(candidate, max_chars)

        if len(candidate) >= min_chars:
            log.info(f"    ✅ [{section_name}] {len(candidate)}字（試行{attempt}）")
            return candidate

        shortage = min_chars - len(candidate)
        log.warning(f"    ⚠️ [{section_name}] {len(candidate)}字 → 不足（試行{attempt}）")

        retry_prompt = f"""以下の文章はまだ{shortage}文字不足です。
具体的な解説を追加してあと{shortage}文字以上書き足してください。
【厳守】メタ発言不要。続きの本文のみ出力。「ます。」「です。」調を維持。
【禁止】架空の収益額・勝率は絶対に書かないこと。
【禁止】中国語・韓国語の漢字（检討・收入・市场など）は絶対に使わないこと。
--- 末尾200字 ---
{candidate[-200:]}"""

        extra = call_ai_with_fallback(retry_prompt, 2000)
        if extra:
            extra = re.sub(r'^(はい[、。]?|続きを書きます[。]?)\s*', '', extra).strip()
            candidate = candidate + "\n\n" + extra

        text = _natural_cut(candidate, max_chars)

    return text


def _natural_cut(text: str, max_chars: int) -> str:
    """GAS版 _自然にカット の移植"""
    if len(text) <= max_chars:
        return text
    search_range = text[:max_chars]
    last_punct = max(
        search_range.rfind("。"),
        search_range.rfind("！"),
        search_range.rfind("？"),
    )
    if last_punct > max_chars * 0.7:
        return text[:last_punct + 1]
    return text[:max_chars]


def _rewrite_abstract(text: str, kw: str) -> str:
    """GAS版 _具体化リライト の移植"""
    prompt = f"""以下の文章に含まれる抽象的な表現を、具体的な数字・手順・実体験に置き換えて書き直してください。

【リライトの鉄則】
1. 「重要です」「意識してください」→「私は〇〇しています」「具体的には△△をチェックします」に変える
2. 架空の収益・勝率は絶対に書かないこと
3. 中国語・韓国語の漢字（检討・收入・市场など）は絶対に使わないこと
4. 「ます。」「です。」調を維持
5. メタ発言不要。本文のみ出力

【対象の文章】
{text[:3000]}"""

    result = call_ai_with_fallback(prompt, 4096)
    if result and len(result) > len(text) * 0.5:
        return result.strip()
    return text
