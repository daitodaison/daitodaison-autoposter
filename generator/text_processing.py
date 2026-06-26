"""
text_processing.py
記事品質チェック・後処理・文体統一
- 簡体字/繁体字 → 日本語正字に自動修正
- 架空数字・架空経歴を検出してスコアダウン
- 抽象表現検出
- 文体統一
"""

import re

# ================================================================
# 簡体字・繁体字 → 正字 変換辞書
# ================================================================
SIMPLIFIED_TO_JAPANESE = {
    # よく混入する簡体字
    "检討": "検討", "检讨": "検討", "檢討": "検討",
    "收入": "収入",   # 簡体字の收 → 日本語の収
    "资金": "資金", "资産": "資産",
    "损失": "損失", "損失": "損失",
    "规则": "ルール", "规律": "規律",
    "实践": "実践", "実践": "実践",
    "经験": "経験",   # 簡体字の经 → 日本語の経
    "时间": "時間",
    "战略": "戦略", "戦略": "戦略",
    "问题": "問題",
    "关键": "重要",
    "设定": "設定",
    "达成": "達成",
    "持続": "持続",
    "积累": "積み重ね",
    "选択": "選択",
    "対応": "対応",
    "実現": "実現",
    "继続": "継続",
    "发展": "発展",
    "进步": "進歩",
    "过程": "過程",
    "运用": "運用",
    "市场": "市場",
    "场合": "場合",
    "档案": "記録",
    "结果": "結果",
    "影响": "影響",
    "完整": "完全",
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
# 架空数字パターン（具体的な収支・成績の数字）
# ================================================================
FAKE_NUMBERS_PATTERNS = [
    r'毎月[0-9０-９]+万',
    r'月収[0-9０-９]+万',
    r'副業収入[0-9０-９]+万',
    r'収益率[0-9０-９]+[%％]',
    r'勝率[0-9０-９]+[%％]',       # ただし「40%」は daito の本物の実績なのでOK → 後で除外
    r'年収[0-9０-９]+万',
    r'月[0-9０-９]+万稼',
    r'[0-9０-９]+万円の利益',
    r'[0-9０-９]+万円を稼',
    r'損失[0-9０-９]+万',
    r'利益[0-9０-９]+万',
    r'[0-9０-９]+ドルの',
    r'資産[0-9０-９]+万',
]

# 本物の実績として使ってよい表現（これはスコアダウン対象外）
ALLOWED_FACTS = [
    "40%",          # daito の本物の勝率
    "2.3%",         # Fintokei 上位 2.3%
    "2587人中61位", # Fintokei 順位
    "61位",
]

# ================================================================
# 抽象表現（多用すると品質スコアダウン）
# ================================================================
ABSTRACT_PHRASES = [
    "〜することが重要です",
    "〜することが大切です",
    "しっかりと",
    "きちんと",
    "ちゃんと",
    "うまく",
    "適切に",
    "効果的に",
    "最適な",
    "様々な",
    "さまざまな",
    "多くの",
    "いろいろな",
    "など様々",
    "ポイントがあります",
    "重要なポイント",
    "大切なポイント",
]

# ================================================================
# 架空経歴パターン
# ================================================================
FAKE_CAREER_PATTERNS = [
    r'FX歴[0-9０-９]+年で',
    r'[0-9０-９]+年間のFX経験',
    r'[0-9０-９]+年以上のトレード',
    r'トレード歴[0-9０-９]+年',
    r'合格した',
    r'パスした',
    r'チャレンジを達成',
]


def fix_simplified_chinese(text: str) -> str:
    """簡体字・繁体字を日本語正字に修正"""
    for wrong, correct in SIMPLIFIED_TO_JAPANESE.items():
        text = text.replace(wrong, correct)
    return text


def detect_fake_numbers(text: str) -> list:
    """架空数字パターンを検出して該当箇所リストを返す"""
    hits = []
    for pattern in FAKE_NUMBERS_PATTERNS:
        matches = re.findall(pattern, text)
        for m in matches:
            # 許可された実績表現は除外
            if not any(allowed in m for allowed in ALLOWED_FACTS):
                hits.append(m)
    return hits


def detect_fake_career(text: str) -> list:
    """架空経歴パターンを検出"""
    hits = []
    for pattern in FAKE_CAREER_PATTERNS:
        matches = re.findall(pattern, text)
        hits.extend(matches)
    return hits


def count_abstract_phrases(text: str) -> int:
    """抽象表現の数を数える"""
    count = 0
    for phrase in ABSTRACT_PHRASES:
        count += text.count(phrase)
    return count


def calculate_quality_score(text: str) -> dict:
    """
    品質スコアを計算して辞書で返す
    score: 0〜100（60以上で合格）
    """
    score = 100
    issues = []

    # 簡体字チェック（修正前に検出）
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
    abstract_count = count_abstract_phrases(text)
    if abstract_count >= 5:
        deduct = min((abstract_count - 4) * 5, 20)
        score -= deduct
        issues.append(f"抽象表現多用: {abstract_count}件")

    # 文字数チェック（短すぎる記事はマイナス）
    char_count = len(text)
    if char_count < 500:
        score -= 20
        issues.append(f"文字数不足: {char_count}文字")
    elif char_count < 800:
        score -= 10
        issues.append(f"文字数やや不足: {char_count}文字")

    return {
        "score": max(score, 0),
        "issues": issues,
        "char_count": char_count,
        "passed": score >= 60,
    }


def unify_style(text: str) -> str:
    """文体を統一する（ですます調に統一など）"""
    # 「〜だ。」「〜である。」を「〜です。」に変換（基本的なもののみ）
    replacements = [
        (r'([^でしまたっだ])だ。', r'\1です。'),
        (r'([^でしまたっだ])である。', r'\1です。'),
        (r'([^でしまたっだ])だった。', r'\1でした。'),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    return text


def post_process_article(text: str) -> str:
    """
    記事の後処理をまとめて実行する
    1. 簡体字修正
    2. 文体統一
    3. 余分な空白・改行を整理
    """
    # Step 1: 簡体字修正
    text = fix_simplified_chinese(text)

    # Step 2: 文体統一
    text = unify_style(text)

    # Step 3: 余分な空白・改行を整理
    # 3行以上の連続する空行を2行に圧縮
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 行末の余分なスペース除去
    text = '\n'.join(line.rstrip() for line in text.split('\n'))

    return text.strip()


def is_article_acceptable(text: str) -> tuple:
    """
    記事が品質基準を満たすか判定
    Returns: (合格bool, スコア辞書)
    """
    # まず後処理（簡体字修正など）してからスコアを計算
    processed = post_process_article(text)
    result = calculate_quality_score(processed)
    return result["passed"], result


# ================================================================
# 動作確認用
# ================================================================
if __name__ == "__main__":
    sample = """
    资金管理 检討を始める前に、收入と支出を把握することが重要です。
    毎月20万の収入のうち、副業収入5万を加えて運用しています。
    私のFX歴5年の経験から、勝率70%を達成しました。
    しっかりと計画を立てることが大切です。様々なリスクを適切に管理する。
    Fintokeiで上位2.3%（2587人中61位）の実績があります。勝率40%でも勝てる。
    → https://dysonblog.org/propfarm-strategy/
    """

    print("=== 後処理前の品質スコア ===")
    ok, result = is_article_acceptable(sample)
    print(f"スコア: {result['score']}/100  合格: {ok}")
    print(f"問題点: {result['issues']}")

    print("\n=== 後処理後のテキスト ===")
    processed = post_process_article(sample)
    print(processed)

    print("\n=== 後処理後の品質スコア ===")
    ok2, result2 = is_article_acceptable(processed)
    print(f"スコア: {result2['score']}/100  合格: {ok2}")
    print(f"問題点: {result2['issues']}")
