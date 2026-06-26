# -*- coding: utf-8 -*-
"""
text_processing.py
記事品質チェック・後処理・文体統一
article_writer.py が期待する関数名:
  - post_process_article(text) -> str
  - detect_abstract_phrases(text) -> {"count": int, "list": list}
  - is_article_acceptable(text) -> (bool, dict)
"""

import re

# ================================================================
# 簡体字・繁体字 → 日本語正字 変換辞書
# ================================================================
SIMPLIFIED_TO_JAPANESE = {
    "检討": "検討", "检讨": "検討", "檢討": "検討",
    "收入": "収入",
    "资金": "資金", "资産": "資産",
    "损失": "損失",
    "规则": "ルール", "规律": "規律",
    "实践": "実践",
    "经験": "経験",
    "时间": "時間",
    "战略": "戦略",
    "问题": "問題",
    "关键": "重要",
    "设定": "設定",
    "达成": "達成",
    "积累": "積み重ね",
    "选択": "選択",
    "继続": "継続",
    "发展": "発展",
    "进步": "進歩",
    "过程": "過程",
    "运用": "運用",
    "市场": "市場",
    "场合": "場合",
    "结果": "結果",
    "影响": "影響",
    "准备": "準備",
    "计划": "計画",
    "决定": "決定",
    "目标": "目標",
    "财産": "財産",
    "财务": "財務",
    "长期": "長期",
    "短期": "短期",
    "风险": "リスク",
    "机会": "機会",
}

# ================================================================
# 架空数字パターン
# ================================================================
FAKE_NUMBERS_PATTERNS = [
    r'毎月[0-9０-９]+万',
    r'月収[0-9０-９]+万',
    r'副業収入[0-9０-９]+万',
    r'収益率[0-9０-９]+[%％]',
    r'年収[0-9０-９]+万',
    r'月[0-9０-９]+万稼',
    r'[0-9０-９]+万円の利益',
    r'[0-9０-９]+万円を稼',
    r'損失[0-9０-９]+万',
    r'利益[0-9０-９]+万',
    r'資産[0-9０-９]+万',
    r'勝率[0-9０-９]+[%％]',  # 40%・2.3%は除外（下記ALLOWED_FACTSで管理）
]

ALLOWED_FACTS = ["40%", "2.3%", "2587人中61位", "61位"]

# ================================================================
# 架空経歴パターン
# ================================================================
FAKE_CAREER_PATTERNS = [
    r'FX歴[0-9０-９]+年',
    r'[0-9０-９]+年間のFX経験',
    r'[0-9０-９]+年以上のトレード',
    r'トレード歴[0-9０-９]+年',
    r'合格した',
    r'パスした',
    r'チャレンジを達成',
]

# ================================================================
# 抽象表現リスト（GAS版 _抽象表現を検出 と同一）
# ================================================================
ABSTRACT_PHRASES = [
    "重要です", "大切です", "必要です", "意識しましょう",
    "心がけてください", "注意が必要", "しっかりと", "きちんと",
    "十分に", "適切に", "効果的に", "積極的に", "不可欠です",
]


def fix_simplified_chinese(text: str) -> str:
    """簡体字・繁体字を日本語正字に修正"""
    for wrong, correct in SIMPLIFIED_TO_JAPANESE.items():
        text = text.replace(wrong, correct)
    return text


def detect_abstract_phrases(text: str) -> dict:
    """
    article_writer.py が呼ぶ関数。
    GAS版 _抽象表現を検出 の移植。
    戻り値: {"count": int, "list": list}
    """
    count = 0
    found = []
    for phrase in ABSTRACT_PHRASES:
        n = text.count(phrase)
        if n > 0:
            count += n
            found.append(f"{phrase}×{n}")
    return {"count": count, "list": found}


def detect_fake_numbers(text: str) -> list:
    """架空数字パターンを検出して該当箇所リストを返す"""
    hits = []
    for pattern in FAKE_NUMBERS_PATTERNS:
        matches = re.findall(pattern, text)
        for m in matches:
            if not any(allowed in m for allowed in ALLOWED_FACTS):
                hits.append(m)
    return hits


def detect_fake_career(text: str) -> list:
    """架空経歴パターンを検出"""
    hits = []
    for pattern in FAKE_CAREER_PATTERNS:
        hits.extend(re.findall(pattern, text))
    return hits


def calculate_quality_score(text: str) -> dict:
    """品質スコアを計算（0〜100、60以上で合格）"""
    score = 100
    issues = []

    # 簡体字チェック
    simplified_hits = [k for k in SIMPLIFIED_TO_JAPANESE if k in text]
    if simplified_hits:
        deduct = min(len(simplified_hits) * 5, 30)
        score -= deduct
        issues.append(f"簡体字・繁体字: {simplified_hits[:5]}")

    # 架空数字チェック
    fake_nums = detect_fake_numbers(text)
    if fake_nums:
        deduct = min(len(fake_nums) * 15, 45)
        score -= deduct
        issues.append(f"架空数字: {fake_nums[:3]}")

    # 架空経歴チェック
    fake_career = detect_fake_career(text)
    if fake_career:
        deduct = min(len(fake_career) * 20, 40)
        score -= deduct
        issues.append(f"架空経歴: {fake_career[:3]}")

    # 抽象表現チェック
    abstract = detect_abstract_phrases(text)
    if abstract["count"] >= 5:
        deduct = min((abstract["count"] - 4) * 5, 20)
        score -= deduct
        issues.append(f"抽象表現多用: {abstract['count']}件")

    # 文字数チェック
    char_count = len(text)
    if char_count < 500:
        score -= 20
        issues.append(f"文字数不足: {char_count}文字")
    elif char_count < 800:
        score -= 10
        issues.append(f"文字数やや不足: {char_count}文字")

    # 禁止ワードチェック（GAS版と同一）
    for w in ["絶対に稼げる", "誰でも簡単に", "リスクゼロ", "申し訳ありません", "お応えできません"]:
        if w in text:
            score -= 30
            issues.append(f"禁止語: {w}")

    return {
        "score": max(score, 0),
        "issues": issues,
        "char_count": char_count,
        "passed": score >= 60,
    }


def unify_style(text: str) -> str:
    """文体を「です・ます」調に統一（GAS版 _文体を統一 に相当）"""
    replacements = [
        (r'([^でしまたっだ])だ。', r'\1です。'),
        (r'([^でしまたっだ])である。', r'\1です。'),
        (r'([^でしまたっだ])だった。', r'\1でした。'),
        (r'できた。', 'できました。'),
        (r'わかった。', 'わかりました。'),
        (r'している。', 'しています。'),
        (r'考えている。', '考えています。'),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    return text


def post_process_article(text: str) -> str:
    """
    article_writer.py が呼ぶ後処理関数。
    GAS版 _記事後処理 の後半処理を移植:
      1. 簡体字修正
      2. 信頼品質フィルタ（GAS版 _信頼品質フィルタ）
      3. 文体統一
      4. 余分な改行整理
    """
    # Step1: 簡体字修正
    text = fix_simplified_chinese(text)

    # Step2: 信頼品質フィルタ（GAS版と同一の置換）
    replacements = [
        ("Fintokei公式", "Fintokei（フィントケイ）"),
        ("アフィリエイト", "紹介プログラム"),
        ("稼げる", "利益を追求できる"),
        ("絶対に稼げる", "利益を追求できる"),
        ("絶対に勝てる", "再現性のある手法で勝てる"),
        ("絶対安全", "リスクを抑えた"),
        ("簡単", "シンプル"),
        ("放置", "自動化"),
        ("裏ワザ", "効率的な手法"),
        ("ご興味がある方は", "よければ"),
        ("ぜひご覧ください", "読んでみてください"),
        ("ご購読ください", "読んでみてください"),
        ("お客様", "あなた"),
        ("読者の皆様におかれましては", "読んでくれているあなたへ"),
        ("市场", "市場"),
        ("了か", "たか"),
    ]
    for wrong, correct in replacements:
        text = text.replace(wrong, correct)

    # Step3: 文体統一
    text = unify_style(text)

    # Step4: 余分な改行整理
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = '\n'.join(line.rstrip() for line in text.split('\n'))

    return text.strip()


def is_article_acceptable(text: str) -> tuple:
    """
    article_writer.py が呼ぶ品質判定関数。
    戻り値: (合格bool, スコア辞書)
    """
    processed = post_process_article(text)
    result = calculate_quality_score(processed)
    return result["passed"], result
