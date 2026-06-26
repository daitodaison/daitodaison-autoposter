# -*- coding: utf-8 -*-
"""
記事執筆のメインロジック
GAS版: _タイトル生成 / _セクション生成 / _セクション構成を決定 / 記事生成処理_ の移植
"""
import time
import logging
from templates import select_template
from text_processing import natural_cut, post_process_article, calculate_quality_score
from ai_client import call_ai_section, call_ai_with_fallback, extract_text

log = logging.getLogger(__name__)

SECTION_WAIT_SECONDS = 15  # セクション間の待機（GAS版は15秒）


def generate_title(kw):
    """キーワードからタイトルを生成"""
    prompt = f"""
あなたは魂を揺さぶるライターです。以下のキーワードを使い、読者が「これは自分のための記事だ」と確信するタイトルを1つ作ってください。

【キーワード】{kw}

【タイトルの鉄則】
1. 形式:「{kw} ｜ サブタイトル」
2. キーワードの後は必ず「半角スペース ｜ 半角スペース」を挟むこと。
3. サブタイトルは、daitoの経歴（10の職を極めた、規律の聖域、引き止めを振り切った決断など）を感じさせる、重みと信頼感のある言葉にすること。
4. 32文字〜45文字程度で、スマホで見た時に最も美しく見えるバランスにすること。

出力例: {kw} ｜ 10の職を極めた私が辿り着いた「規律の聖域」
"""
    try:
        res = call_ai_with_fallback(prompt, 200)
        text = extract_text(res)
        if text:
            return text.lstrip("#").strip()
        log.warning("⚠️ タイトル生成：AIの応答が空でした → フォールバックタイトルを使用")
    except Exception as e:
        log.warning(f"⚠️ タイトル生成エラー: {e}")
    return f"{kw} ｜ 正直な真実"


def determine_target_length(existing_bodies=None):
    """過去記事の平均文字数から目標文字数を算出（DBがなければデフォルト3000字）"""
    target = 3000
    if existing_bodies:
        avg_len = sum(len(b) for b in existing_bodies) // max(len(existing_bodies), 1)
        target = min(3000, max(1800, avg_len))
    log.info(f"  >> 目標文字数: {target}字")
    return target


def determine_section_structure(kw, target_length):
    """テンプレートからセクション構成（最低/上限文字数つき）を決定"""
    template = select_template(kw)
    raw_sections = template["構成"](kw, target_length)

    sections = []
    for s in raw_sections:
        if "最低文字数" in s:
            # HTRI型などは既に最低文字数が指定済み
            min_len = s["最低文字数"]
            max_len = int(min_len * 1.4)
        else:
            ratio = s.get("比率", 0.2)
            min_len = int(target_length * ratio)
            max_len = int(min_len * 1.2)
        sections.append({
            "名前": s["名前"],
            "感情設計": s.get("感情設計", ""),
            "指示": s["指示"],
            "最低文字数": min_len,
            "上限文字数": max_len,
        })
    return sections


def _extract_forbidden_phrases(previous_text):
    """直前の文章から使用済みフレーズを抽出（重複防止用）"""
    if not previous_text:
        return "なし"
    sentences = [s.strip() for s in previous_text.split("。") if len(s.strip()) > 15][-10:]
    return "、".join(s[:20] for s in sentences)


def generate_section(kw, section_name, instruction, min_length, previous_text="",
                      max_length=None, emotional_design=""):
    """1セクションを生成。不足分は追記で補う（最大3試行）"""
    max_length = max_length or int(min_length * 1.2)

    writer_persona = """
【禁止（最優先）】
- 同じ意味の文を2回書くな。1つの事実は1文で終わらせろ。
- 「〜がわかりました」「〜ことがわかりました」は1記事で3回まで。
- 「私は〜」で始まる文を連続して3文以上書くな。
- 「大きな影響を与える」「重要性を再認識」は使うな。

【書く人物】
製造現場出身・Fintokei挑戦中のdaito。口調は「です/ます」のみ。

【1セクションの構造】
①結論1文 → ②理由か事実1〜2文 → ③自分の実践1文 → ④次につながる問い1文
この4ステップのみ。それ以上書くな。
"""
    forbidden_phrases = _extract_forbidden_phrases(previous_text)

    prompt = f"""{writer_persona}
【以下のフレーズは前のセクションで使用済み・完全禁止】
{forbidden_phrases}

【今回書くセクション】
キーワード: {kw}
セクション名: {section_name}
執筆指示: {instruction}

【文字数ルール（最重要・厳守）】
・最低: {min_length}文字
・上限: {max_length}文字
・{max_length}文字に達したら、その文を「。」で終わらせて即終了すること
・長く書けば良いわけではない。上限を超えたら減点

【直前の文章の末尾（ここから自然につなげること）】
{previous_text[-200:] if previous_text else "（記事の書き出しです）"}

【文体ルール】
・「私は〜」「私が〜」という一人称で書く
・「ます。」「です。」調で統一（「だ。」「である。」は使わない）
・スマホで読みやすいよう、2〜3文ごとに改行を入れる
・専門用語を使う場合は必ず1文で説明を添える
・セクション名の見出し（##）から書き始める
・「はい」「続きを書きます」等のメタ発言は一切不要

【出力形式の絶対ルール】
・文末は必ず「ます。」または「です。」で終わること
・「だ。」「である。」「である」で終わる文は全て書き直すこと
・中国語・韓国語の漢字は使わないこと（例：「市场」→「市場」）
・数字は実際の取引ルールのみ（「20件」など架空の件数禁止）
・誤字脱字がないか確認してから出力すること

それでは執筆してください："""

    text = ""
    for attempt in range(1, 4):
        res = call_ai_section(prompt, 4096, section_name)
        candidate = extract_text(res)
        if not candidate:
            log.warning(f"    [{section_name}] API失敗 試行{attempt}")
            time.sleep(3)
            continue

        candidate = candidate.lstrip()
        for prefix in ["はい、", "はい。", "続きを書きます。", "承知しました。", "わかりました。", "はい", "続きを書きます", "承知しました", "わかりました"]:
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):].lstrip()

        candidate = natural_cut(candidate, max_length, min_length)

        if len(candidate) >= min_length:
            text = candidate
            log.info(f"    ✅ [{section_name}] {len(text)}字（試行{attempt}）")
            break

        log.warning(f"    ⚠️ [{section_name}] {len(candidate)}字 → 不足（試行{attempt}）")
        shortage = min_length - len(candidate)
        append_prompt = f"""以下の文章はまだ{shortage}文字不足です。
具体的な解説を追加してあと{shortage}文字以上書き足してください。
【厳守】メタ発言不要。続きの本文のみ出力。「ます。」「です。」調を維持。
【禁止】架空の収益額・勝率・pipsは絶対に書かないこと。
--- 末尾200字 ---
{candidate[-200:]}"""
        append_res = call_ai_with_fallback(append_prompt, 2000)
        addition = extract_text(append_res)
        for prefix in ["はい、", "はい。", "続きを書きます。"]:
            if addition.startswith(prefix):
                addition = addition[len(prefix):].lstrip()
        if addition:
            candidate = candidate + "\n\n" + addition
        text = natural_cut(candidate, max_length, min_length)
        time.sleep(2)

    return text


def get_reinforcement_instructions(kw):
    """文字数不足時の補強セクション指示リスト"""
    return [
        {"名前": "## よくある質問に、正直に答えます",
         "指示": f"{kw}について読者が不安に思うことをQ&A形式で5つ以上。各回答は200字以上で回答する"},
        {"名前": "## 私が現在検証していること｜現場の規律をトレードへ",
         "指示": "製造現場や輸出実務で培った「規律」と「計数管理」を、現在どのようにトレードのリスク管理（資金管理やドローダウン対策など）に当てはめて検証しているか、チャレンジャーの視点で詳述する。※架空の収益や勝率は絶対に出さないこと。"},
        {"名前": "## これだけは知っておいてほしい注意点",
         "指示": "初心者が見落としがちなリスク・落とし穴を5つ以上、詳述する"},
        {"名前": "## 向いている人・向いていない人",
         "指示": f"{kw}が向いている人・いない人の特徴。向いていない人への代替案も提示する"},
    ]


def write_article(kw, target_length=3000):
    """記事1本をまるごと生成する（タイトル＋本文）。戻り値: (title, body, score, problems)"""
    log.info(f"[執筆中] {kw}")

    title_line = generate_title(kw)
    body = title_line + "\n\n"

    sections = determine_section_structure(kw, target_length)
    for s in sections:
        log.info(f"  >> {s['名前']}（最低{s['最低文字数']}字）")
        section_text = generate_section(
            kw, s["名前"], s["指示"], s["最低文字数"], body,
            s["上限文字数"], s.get("感情設計", ""),
        )
        body += section_text + "\n\n"
        log.info(f"  >> 累計: {len(body)}文字")
        log.info("⏸ 次セクションまで待機...")
        time.sleep(SECTION_WAIT_SECONDS)

    # 文字数が足りない場合は補強
    reinforce_min = int(target_length * 0.80)
    reinforce_count = 0
    while len(body) < reinforce_min and reinforce_count < 1:
        reinforce_count += 1
        shortage = reinforce_min - len(body)
        reinforcements = get_reinforcement_instructions(kw)
        chosen = reinforcements[(reinforce_count - 1) % len(reinforcements)]
        log.info(f"  >> 補強{reinforce_count}回目: {chosen['名前']} (不足{shortage}字)")
        reinforce_text = generate_section(kw, chosen["名前"], chosen["指示"], max(shortage, 500), body)
        body += reinforce_text + "\n\n"
        log.info("⏸ 次セクションまで待機...")
        time.sleep(SECTION_WAIT_SECONDS)

    body = post_process_article(kw, body)

    if not body or len(body) < 200:
        log.error(f"  >> ❌ 本文が短すぎるため保存スキップ: {kw}")
        return None

    if "申し訳ありません" in body or "お応えできません" in body:
        log.error(f"⛔ AI生成拒否のため保存スキップ: {kw}")
        return None

    score, problems = calculate_quality_score(body, kw, target_length)
    log.info(f"  >> 品質スコア: {score}点")
    if score < 40:
        log.warning(f"  >> ⚠️ 低品質判定（{score}点）のため保存をスキップ。問題点: {', '.join(problems)}")
        return None

    log.info(f"  >> ✅ 完了: {kw}（{len(body)}字 / 品質{score}点）")
    return {"title": title_line, "body": body, "score": score, "topic": kw}
