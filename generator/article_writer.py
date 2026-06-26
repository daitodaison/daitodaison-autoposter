# -*- coding: utf-8 -*-
"""
article_writer.py
generate.py が期待する関数:
  - write_article(kw, target_length) -> dict
  - determine_target_length(bodies) -> int
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
WRITER_BASE_PROMPT = """【禁止（最優先・絶対厳守）】
- 同じ意味の文を2回書くな。1つの事実は1文で終わらせろ。
- 「〜がわかりました」は1記事で3回まで。
- 「私は〜」で始まる文を連続して3文以上書くな。
- 「大きな影響を与える」「重要性を再認識」は使うな。
- 中国語・韓国語の漢字は絶対に使わないこと（例: 「检討」→「検討」「收入」→「収入」「市场」→「市場」）
- 架空の収益額・勝率・月収は絶対に書かないこと（「月収20万」「勝率70%」「月5万稼いだ」は禁止）
- 「合格した」「パスした」という表現は禁止
- 「FX歴○年」「トレード歴○年」などの架空経歴は書かないこと
- 「絶対に稼げる」「誰でも簡単」「リスクゼロ」は使わないこと
- 「お客様」は使わず「あなた」と書く
- プロンプトの指示文（【リライトの鉄則】【禁止】など）を本文に書き出すな

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
- 【】で囲まれた指示文・ルール文は絶対に本文に含めるな"""


# ================================================================
# プロンプト混入を除去するパターン
# ================================================================
PROMPT_LEAKAGE_PATTERNS = [
    r'【リライトの鉄則】.*?(?=\n##|\Z)',
    r'【禁止[^】]*】.*?(?=\n##|\Z)',
    r'【.*?の鉄則】.*?(?=\n##|\Z)',
    r'【ルール】.*?(?=\n##|\Z)',
    r'【厳守】.*?(?=\n##|\Z)',
    r'\[リライトの鉄則\].*?(?=\n##|\Z)',
]


def _remove_prompt_leakage(text: str) -> str:
    """プロンプト指示文が本文に混入している場合に除去"""
    for pattern in PROMPT_LEAKAGE_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.DOTALL)
    # 行単位でも除去
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        # 【〜の鉄則】【禁止〜】などで始まる行を除去
        if re.match(r'^【.*(鉄則|禁止|ルール|厳守|指示).*】', line.strip()):
            continue
        # 番号付きで「〜を変える」「〜書かないこと」のような指示文を除去
        if re.match(r'^\d+\.\s+「[^」]+」.*?(変える|書かないこと|使わないこと|維持|不要)', line.strip()):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def determine_target_length(bodies=None) -> int:
    """過去記事の平均文字数から目標文字数を算出"""
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
    """generate.py が呼ぶメイン関数"""
    log.info(f"***執筆中*** {kw}")
    log.info(f"  >> 目標文字数: {target_length}字")

    title = _generate_title(kw)
    template = select_template(kw)
    sections = template["構成"](kw, target_length)
    body = title + "\n\n"

    for section in sections:
        sec_name = section["名前"]
        min_chars = int(target_length * section["比率"])
        max_chars = int(min_chars * 1.2)
        instruction = section["指示"]
        emotion = section.get("感情設計", "")

        log.info(f"  >> {sec_name}（最低{min_chars}字）")
        sec_text = _generate_section(kw, sec_name, instruction, min_chars, body, max_chars, emotion)
        # プロンプト混入チェック
        sec_text = _remove_prompt_leakage(sec_text)
        body += sec_text + "\n\n"
        log.info(f"  >> 累計: {len(body)}文字")
        log.info(f"⏸ 次セクションまで待機...")
        time.sleep(15)

    goal_result = determine_goal(kw)
    goal = goal_result["goal"]
    log.info(f"  >> ゴール: ***{goal_result['key']}*** score={goal_result.get('score',1)} → {goal['url']}")

    # 抽象表現チェック・具体化
    abstract_hits = detect_abstract_phrases(body)
    if abstract_hits["count"] >= 3:
        log.warning(f"⚠️ 抽象表現を{abstract_hits['count']}箇所検出: {', '.join(abstract_hits['list'])}")
        log.info(f"    🔍 抽象表現を検出（{abstract_hits['count']}箇所）: 具体化リライトを実行")
        body = _rewrite_abstract(body, kw)
        body = _remove_prompt_leakage(body)  # リライト後も混入チェック
        log.info("✅ 抽象表現の具体化完了")

    body = post_process_article(body)

    cta = get_cta(kw, "末尾")
    body = body.strip() + "\n\n" + cta

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

キーワード: {kw}

形式: 「{kw} ｜ サブタイトル」（キーワードの後は半角スペース｜半角スペース）
サブタイトル: daitoの経歴（10の職を極めた・規律の聖域・引き止めを振り切った決断など）を感じさせる重みと信頼感のある言葉
文字数: 32〜45文字程度
禁止: 中国語・韓国語の漢字（检討・收入・市场など）・架空の数字・成果

出力例: {kw} ｜ 10の職を極めた私が辿り着いた「規律の聖域」

タイトルを1行だけ出力してください:"""

    result = call_ai_with_fallback(prompt, 200)
    if result:
        title = re.sub(r'^#+\s*', '', result.strip()).replace('"', '').replace("'", '')
        title = _remove_prompt_leakage(title)
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

    sentences = [s.strip()[:20] for s in prev_text.split("。") if len(s.strip()) > 15]
    forbidden = "、".join(sentences[-10:]) if sentences else "なし"

    prompt = f"""{WRITER_BASE_PROMPT}

前のセクションで使用済みのフレーズ（繰り返し禁止）: {forbidden}

キーワード: {kw}
セクション名: {section_name}
執筆指示: {instruction}
感情設計: {emotion}

文字数: 最低{min_chars}文字・上限{max_chars}文字（上限に達したら句点で終了）

直前の末尾（ここから自然につなげる）:
{prev_text[-200:] if prev_text else "（記事の書き出しです）"}

見出し「{section_name}」から書き始めて、記事本文のみ出力してください:"""

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
        candidate = _remove_prompt_leakage(candidate)
        candidate = _natural_cut(candidate, max_chars)

        if len(candidate) >= min_chars:
            log.info(f"    ✅ [{section_name}] {len(candidate)}字（試行{attempt}）")
            return candidate

        shortage = min_chars - len(candidate)
        log.warning(f"    ⚠️ [{section_name}] {len(candidate)}字 → 不足（試行{attempt}）")

        retry_prompt = f"""以下の文章の続きを{shortage}文字以上書き足してください。
記事の本文のみ出力してください。メタ発言・指示文は一切不要です。
「ます。」「です。」調を維持。架空の収益・勝率は書かないこと。
--- 末尾200字 ---
{candidate[-200:]}"""

        extra = call_ai_with_fallback(retry_prompt, 2000)
        if extra:
            extra = re.sub(r'^(はい[、。]?|続きを書きます[。]?)\s*', '', extra).strip()
            extra = _remove_prompt_leakage(extra)
            candidate = candidate + "\n\n" + extra

        text = _natural_cut(candidate, max_chars)

    return text


def _natural_cut(text: str, max_chars: int) -> str:
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
    """抽象表現を具体化（プロンプト混入対策済み）"""
    prompt = f"""以下の記事の抽象的な表現を具体化して書き直してください。

変更ルール:
- 「重要です」→「私は〇〇しています」のように具体的な行動に変える
- 架空の収益・勝率は書かない
- 中国語・韓国語の漢字は使わない
- 「ます。」「です。」調を維持

記事本文のみ出力してください（説明・コメント不要）:

{text[:3000]}"""

    result = call_ai_with_fallback(prompt, 4096)
    if result and len(result) > len(text) * 0.5:
        return result.strip()
    return text
