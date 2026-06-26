# -*- coding: utf-8 -*-
"""
記事の品質チェック・後処理ロジックの移植
GAS版: _記事品質スコアを計算 / _抽象表現を検出 / _文体チェック / _記事後処理 / _信頼品質フィルタ 等
"""
import re
import logging
from templates import determine_goal
from ai_client import call_ai_with_fallback, extract_text

log = logging.getLogger(__name__)

抽象ワード = [
    "重要です", "大切です", "必要です", "意識しましょう", "心がけてください", "注意が必要",
    "しっかりと", "きちんと", "十分に", "適切に", "効果的に", "積極的に", "不可欠です",
]


def detect_abstract_expressions(text):
    detected_count = 0
    detected_items = []
    for w in 抽象ワード:
        count = text.count(w)
        if count > 0:
            detected_count += count
            detected_items.append(f"{w}×{count}")
    return detected_count, detected_items


def concretize_abstract_text(text, kw):
    """抽象表現をAIに具体化させる"""
    count, items = detect_abstract_expressions(text)
    if count < 3:
        return text

    log.info(f"    🔍 抽象表現を検出（{count}箇所）: 具体化リライトを実行")
    prompt = f"""以下の文章に抽象的な表現があります。
「重要です」「大切です」「しっかりと」などを全て具体的な数字・手順・実体験に置き換えてください。

❌ 悪い例：「ロット管理が重要です」
✅ 良い例：「私は証拠金の2%以上を1回のトレードに使いません」

【ルール】
・内容・構成・文字数は変えない
・「ます。」「です。」調を維持
・URLはそのまま残す

【書き直す文章】
{text[:3000]}"""
    res = call_ai_with_fallback(prompt, 4096)
    rewritten = extract_text(res)
    if rewritten:
        log.info("✅ 抽象表現の具体化完了")
        return rewritten
    return text


def check_style_consistency(text):
    """です・ます調とだ・である調の混在チェック"""
    lines = [l for l in text.split("。") if len(l) > 10]
    desu_count = sum(1 for l in lines if "です" in l or "ます" in l)
    da_count = sum(1 for l in lines if re.search(r"[^ます]だ$|である$", l.strip()))
    if desu_count + da_count == 0:
        return True
    ratio = desu_count / (desu_count + da_count)
    return ratio >= 0.85


def unify_style(text):
    """文体統一をAIに依頼"""
    prompt = f"""以下の文章の文体が「ます・です調」と「だ・である調」で混在しています。
全て「ます・です調」に統一してください。
内容・構成・URLは変えないこと。

{text[:3000]}"""
    res = call_ai_with_fallback(prompt, 4096)
    rewritten = extract_text(res)
    if rewritten:
        log.info("✅ 文体統一完了")
        return rewritten
    return text


def improve_mobile_readability(text):
    """3文ごとに改行を入れてスマホ読みやすさを改善"""
    paragraphs = text.split("\n\n")
    improved = []
    for p in paragraphs:
        if p.startswith("#") or p.startswith(">") or p.startswith("→"):
            improved.append(p)
            continue
        sentences = [s for s in p.split("。") if s.strip()]
        if len(sentences) <= 3:
            improved.append(p)
            continue
        chunks = []
        for i in range(0, len(sentences), 3):
            chunk = "。".join(sentences[i:i + 3])
            if not chunk.endswith("。"):
                chunk += "。"
            chunks.append(chunk)
        improved.append("\n\n".join(chunks))
    return "\n\n".join(improved)


def natural_cut(text, limit, minimum=0):
    """上限文字数で自然な文末（句点）でカットする"""
    if len(text) <= limit:
        return text
    search_range = text[:limit]
    last_period = max(
        search_range.rfind("。"), search_range.rfind("！"), search_range.rfind("？")
    )
    if last_period > max(minimum, limit * 0.7):
        return text[: last_period + 1]
    return text[:limit]


def trust_quality_filter(text):
    """GAS版 _信頼品質フィルタ の移植：誇大表現や問題のある言い回しを置換"""
    replacements = [
        (r"Fintokei公式", "Fintokei（フィントケイ）"),
        (r"アフィリエイト", "紹介プログラム"),
        (r"絶対に稼げる", "利益を追求できる可能性がある"),
        (r"稼げる", "利益を追求できる"),
        (r"絶対に勝てる", "再現性のある手法で勝てる"),
        (r"絶対安全", "リスクを抑えた"),
        (r"簡単", "シンプル"),
        (r"放置", "自動化"),
        (r"裏ワザ", "効率的な手法"),
        (r"ご興味がある方は", "よければ"),
        (r"ぜひご覧ください", "読んでみてください"),
        (r"ご購読ください", "読んでみてください"),
        (r"確実に自己破産リスクを回避", "ドローダウンを抑える傾向にある"),
        (r"確実に.*?につなげ", "着実に取り組んでいきます"),
        (r"約\d+%カバー", "一定期間分"),
        (r"自己トレードアカウント", "自分のトレード記録"),
        (r"読者の皆様におかれましては", "読んでくれているあなたへ"),
        (r"読者の皆さまの役立てば幸いです", ""),
        (r"皆様とFintokeiの.*?探求していきたい", "引き続き検証を続けていきます"),
        (r"ぜひご購読ください", "よければ読んでみてください"),
        (r"20[0-9]{2}年[0-9]+月から20[0-9]{2}年[0-9]+月", "過去の一定期間"),
        (r"平均利益率.*?向上", "収支が改善傾向にある"),
        (r"大幅な向上にもつながりました", "改善につながっています"),
        (r"もしご興味がありましたら", "よければ"),
    ]
    result = text
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result)
    return result


def calculate_quality_score(body, kw, target_length):
    """記事品質スコアを計算（0〜100点）"""
    score = 100
    problems = []

    length = len(body)
    if length < target_length * 0.7:
        score -= 30
        problems.append(f"文字数不足: {length}字")
    if length > target_length * 1.5:
        score -= 20
        problems.append(f"文字数超過: {length}字")

    for w in kw.split(" "):
        count = body.count(w)
        if count < 2:
            score -= 10
            problems.append(f"{w}の出現回数が少ない")
        if count > 25:
            score -= 10
            problems.append(f"{w}の詰め込みすぎ")

    if "##" not in body:
        score -= 15
        problems.append("見出しなし")
    if "https://" not in body:
        score -= 10
        problems.append("URLなし")

    for w in ["絶対に稼げる", "誰でも簡単", "リスクゼロ", "申し訳ありません", "お応えできません"]:
        if w in body:
            score -= 30
            problems.append(f"禁止要素検出: {w}")

    架空経歴パターン = [
        r"\d+年間の経験", r"ウィニングレシオ.*向上", r"成績は劇的", r"大幅に向上",
        r"勝率.*%", r"平均.*\d+%.*改善", r"平均.*\d+%.*増加", r"平均.*\d+%.*達し",
        r"収益.*\d+%以上", r"約\d+%カバー",
    ]
    for pattern in 架空経歴パターン:
        if re.search(pattern, body):
            score -= 25
            problems.append(f"架空の成果・数字の可能性: {pattern}")

    title_line = body.split("\n")[0] if body else ""
    for phrase in ["引き止め", "10の職", "規律の聖域", "最低1年"]:
        if phrase in title_line and phrase not in body:
            score -= 20
            problems.append(f"タイトルの「{phrase}」が本文に未登場")

    if "お客様" in body:
        score -= 15
        problems.append("「お客様」という不自然な表現が含まれている")

    わかりました数 = body.count("がわかりました")
    if わかりました数 > 3:
        score -= (わかりました数 - 3) * 5
        problems.append(f"「がわかりました」が{わかりました数}回（上限3回）")

    return score, problems


def remove_duplicate_sentences(text):
    """同一文の重複を削除"""
    sentences = text.split("。")
    seen = set()
    result = []
    for s in sentences:
        key = s.strip().replace(" ", "").replace("\u3000", "")
        if len(key) < 10:
            result.append(s)
            continue
        prefix = key[:20]
        is_dup = any(k[:20] == prefix for k in seen)
        if is_dup:
            continue
        seen.add(key)
        result.append(s)
    return "。".join(result)


def post_process_article(kw, body):
    """GAS版 _記事後処理 の移植：クリーンアップ・誤字修正・CTA挿入"""
    result = determine_goal(kw)
    goal = result["goal"]

    clean = body
    simple_replacements = {
        "公式サイトはこちら": "詳細な解説記事はこちら",
        "公式ページへ": "ブログの攻略ロードマップへ",
        "公式サイトで必ず確認してください": "私のブログ記事で詳しく解説しています",
        "お客様": "あなた",
        "読者の皆様におかれましては": "読んでくれているあなたへ",
        "だ。": "です。",
        "である。": "です。",
        "であった。": "でした。",
        "できた。": "できました。",
        "わかった。": "わかりました。",
        "している。": "しています。",
        "考えている。": "考えています。",
        "市场": "市場",
        "了か": "たか",
    }
    for old, new in simple_replacements.items():
        clean = clean.replace(old, new)

    # 文字数表記などの除去
    regex_cleanups = [
        r"\d+文字に達しました[。]?",
        r"\d+[,，]?\d*文字[。]?",
        r"^\d+文字.*$",
        r"\d+文字です[。]?",
        r"\d+文字となりました[。]?",
        r"執筆を終了します[。]?",
        r"以上で.*?完了です[。]?",
    ]
    for pattern in regex_cleanups:
        flags = re.MULTILINE if pattern.startswith("^") else 0
        clean = re.sub(pattern, "", clean, flags=flags)

    clean = re.sub(r"\n{3,}", "\n\n", clean)

    # 重複段落の除去
    paragraphs = clean.split("\n\n")
    seen = set()
    deduped = []
    for p in paragraphs:
        trimmed = p.strip()
        if len(trimmed) < 10:
            continue
        key = trimmed[:50] + "|" + trimmed[-30:]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    clean = "\n\n".join(deduped)

    # 重複文除去
    clean = remove_duplicate_sentences(clean)

    # 抽象表現チェック
    count, items = detect_abstract_expressions(clean)
    if count >= 3:
        log.warning(f"⚠️ 抽象表現を{count}箇所検出: {', '.join(items)}")
        clean = concretize_abstract_text(clean, kw)

    # 文体チェック
    if not check_style_consistency(clean):
        log.warning("⚠️ 文体ブレを検出 → 統一処理を実行")
        clean = unify_style(clean)

    # スマホ可読性
    clean = improve_mobile_readability(clean)

    # 前半CTA挿入（5行目あたり）
    lines = clean.split("\n")
    if len(lines) > 5:
        lines.insert(5, goal["cta"].get("前半", ""))
        clean = "\n".join(lines)

    clean = clean.strip() + "\n\n" + goal["cta"].get("末尾", "")

    return trust_quality_filter(clean)
