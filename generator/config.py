# -*- coding: utf-8 -*-
"""
共通設定ファイル
GAS版の CONFIG / CONVERSION_GOALS / ARTICLE_TEMPLATES を移植
APIキーは環境変数（GitHub Secrets）から読み込む方式に変更
"""
import os

CONFIG = {
    "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", ""),
    "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
    "GROQ_API_KEY": os.environ.get("GROQ_API_KEY", ""),

    "攻略URL": "https://dysonblog.org/fintokei-strategy/",
    "トップURL": "https://dysonblog.org/",
    "目標記事数": 3,

    "画像設定": {
        "ドライブフォルダ": "images",
        "画像スタイル": "black and white minimalist photography, cinematic lighting, dramatic shadows, professional workspace",
        "Pollinations": {
            "有効": True,
            "幅": 1280,
            "高さ": 720,
            "モデル候補": ["flux", "flux-realism", "flux-pro", "any-dark"],
            "強化": True,
        },
        "Picsum": {"幅": 1280, "高さ": 720},
    },

    "OpenRouterモデル候補_高品質": ["anthropic/claude-3-5-haiku"],
    "OpenRouterモデル候補": ["anthropic/claude-3-haiku", "mistralai/mistral-7b-instruct"],
    "Geminiモデル候補": [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
    ],
    "Groqモデル候補": [
        "gemma2-9b-it",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ],
}

SUBSTACK_URL = "https://daitodaison.substack.com/"

除外キーワード = ["ログイン", "解約", "退会", "マイページ", "ログインできない", "MT4 ダウンロード", "MT5 ダウンロード"]
除外サイト = ["note.com", "ameblo.jp", "facebook.com", "instagram.com", "twitter.com", "x.com"]
SNSサイト = ["youtube.com", "twitter.com", "x.com", "instagram.com", "tiktok.com"]
掲示板サイト = ["chiebukuro.yahoo.co.jp", "oshiete.goo.ne.jp", "okwave.jp", "5ch.net", "2ch.net", "reddit.com"]
強豪サイト = ["fintokei.com", "fintokei.jp", "gemforex.com", "xmtrading.com", "titanfx.com"]
MAIN_WORDS = ["Fintokei", "フィントケイ", "プロップファーム", "MT5", "funded", "FX 合格", "FX チャレンジ", "プロップトレーダー"]

FORMATS = [
    {"type": "ノウハウ型", "instruction": "具体的な手順、設定値、計算式を含める。現場の安全基準のように明確な指示。"},
    {"type": "考察・検証型", "instruction": "ネットの噂ではなく、自分の目で確かめた事実、違和感、仮説をベースにする。"},
    {"type": "マインドセット型", "instruction": "精神論ではなく、規律を守るための物理的な環境作りやルーティンに焦点を当てる。"},
    {"type": "初心者ガイド型", "instruction": "専門用語を現場言葉で噛み砕き、つまづきやすいポイントを先回りして解説する。"},
]


def get_current_year_context():
    import datetime
    return f"{datetime.datetime.now().year}年最新"


def generate_dynamic_categories():
    return [
        "FXの規律とリスク管理", "Fintokeiでの資金管理術", "10の職を経て辿り着いたトレードの聖域",
        "製造現場の目線で見るMT4/MT5", "プロップファームという新しい自立の形",
        "感情を排除する計数管理トレード", "輸出実務の経験から学ぶ相場の違和感",
    ]


def get_marketing_strategy():
    return {
        "targetPersona": "daito",
        "tone": "正直、誠実、現場主義、対等な目線、少しの厳しさ、大きなエール",
        "bestWords": ["Fintokei", "プロップファーム", "規律", "検証", "資金管理", "自立"],
    }


# =================================================================
# 成約ゴール別誘導システム（CONVERSION_GOALS の移植）
# =================================================================
CONVERSION_GOALS = {
    "book_testa": {
        "priority": 0, "url": "https://dysonblog.org/book-testa/",
        "label": "テスタさんに学ぶ投資の基礎と守りの思考",
        "triggers": ["株", "初心者", "投資", "マンガ", "負けない", "リテラシー"],
        "cta": {
            "前半": "\n> 【投資初心者の方へ】難しい分析よりも、まずは「負けない思考」を身につけましょう。テスタさんのマンガ本が一番の近道です。\n> →『マンガでわかるテスタの株式投資』解説を読む\n",
            "末尾": "\n---\nまずはこの本で投資の「守りの型」を完成させてください。\n\n→『マンガでわかるテスタの株式投資』解説を読む\n\n※本書で学べる「負けない思考」は、私が取り組んでいるプロップファームでの運用においても、最も重要視している考え方です。基礎が固まれば、あなたのトレードスキルは確実に一段階上のステージへ進めます。",
        },
    },
    "book_shokunin": {
        "priority": 0, "url": "https://dysonblog.org/book-shokunin/",
        "label": "匠FXに学ぶ、職人トレードの思考法",
        "triggers": ["国内FX", "匠FX", "メンタル", "損切り", "ルール"],
        "cta": {
            "前半": "\n> 【信頼できるトレードの基礎】FXは才能のゲームではありません。まずはこの本で「稼ぐための型」を学んでください。\n> → https://dysonblog.org/book-shokunin/\n",
            "末尾": "\n---\nトレードで迷子になっているなら、まずは本書で土台を整えることが一番の近道です。\n\n→ https://dysonblog.org/book-shokunin/\n\n※本書で学んだ基礎は、私が取り組んでいるプロップファーム（資金提供）の環境でもそのまま通用する「最強の武器」になります。基礎が固まったら、ぜひ挑戦してみてください。",
        },
    },
    "fintokei": {
        "priority": 1, "url": "https://dysonblog.org/fintokei/",
        "label": "Fintokei（フィントケイ）",
        "triggers": ["Fintokei", "フィントケイ", "ふぃんとけい", "プロップファーム", "プロップ", "チャレンジ", "審査",
                     "資金提供", "funded", "合格", "失格", "合格率", "評判", "口コミ", "怪しい", "安全", "始め方",
                     "登録方法", "コンテスト", "デモ大会", "無料大会", "速攻プロ", "入門プラン", "チャレンジプラン",
                     "出金", "payout", "ペイアウト", "出金拒任", "スケーリング", "税金 プロップ", "日次損失", "最大損失"],
        "cta": {
            "前半": "> **Fintokei（フィントケイ）の評判・始め方・プランを全部まとめたページがあります。**\n> → https://dysonblog.org/fintokei/\n",
            "末尾": "\n---\nFintokeiについての最新情報や詳細なルールは、以下の解説記事にすべてまとめています。\n→ https://dysonblog.org/fintokei/\n",
        },
    },
    "fintokei_strategy": {
        "priority": 1, "url": "https://dysonblog.org/fintokei-strategy/",
        "label": "Fintokei攻略ロードマップ",
        "triggers": ["Fintokei 攻略", "フィントケイ 攻略", "Fintokei 手法", "Fintokei ルール", "Fintokei VWAP",
                     "Fintokei ロット", "Fintokei 失格", "Fintokei 勝率", "ロット管理 プロップ", "ドローダウン 管理",
                     "失格 回避", "プロップ 手法", "プロップ 戦略", "プロップ ロット", "プロップ 攻略"],
        "cta": {
            "前半": "> Fintokeiの具体的な攻略手順（ロット管理・失格回避・手法）をまとめています。\n> → https://dysonblog.org/fintokei-strategy/\n",
            "末尾": "\n---\nFintokeiコンテストで2587人中61位（上位2.3%）を達成した戦略を含め、攻略の全手順をまとめています。\n\n→ https://dysonblog.org/fintokei-strategy/\n\n→ 無料メール登録（最新情報）\n",
        },
    },
    "fintokei_vwap": {
        "priority": 1, "url": "https://dysonblog.org/vwaprsi/",
        "label": "Fintokei VWAP攻略バイブル",
        "triggers": ["VWAP", "vwap", "VWAP プロップ", "VWAP Fintokei", "VWAP RSI", "VWAP スキャルピング"],
        "cta": {"末尾": "\n---\nVWAPを軸にFintokeiで勝つための戦略を全部まとめたページがあります。\n\n→ https://dysonblog.org/vwaprsi/\n\n→ 無料メール登録\n"},
    },
    "funded7": {
        "priority": 2, "url": "https://dysonblog.org/funded7/",
        "label": "Funded7",
        "triggers": ["Funded7", "ファンデット7", "funded7", "スキャルOK プロップ", "スキャルピング OK プロップ",
                     "狭スプレッド プロップ", "安い プロップ"],
        "cta": {"末尾": "\n---\nFunded7についての詳細（スキャルOK・低コスト・スプレッドの実態）をまとめています。\n\n→ https://dysonblog.org/funded7/\n\n→ 無料メール登録\n"},
    },
    "ftmo": {
        "priority": 2, "url": "https://dysonblog.org/ftmo/",
        "label": "FTMO",
        "triggers": ["FTMO", "エフティーエムオー", "FTMO チャレンジ", "FTMO ルール", "FTMO 合格", "FTMO 失敗",
                     "FTMO 出金", "FTMO 評判", "FTMO ドローダウン"],
        "cta": {"末尾": "\n---\nFTMOのルール・合格率・私の見解をまとめています。\n\n→ https://dysonblog.org/ftmo/\n\n→ 無料メール登録\n"},
    },
    "prop_comparison": {
        "priority": 2, "url": "https://dysonblog.org/best-prop-trading-firms/",
        "label": "プロップファーム比較",
        "triggers": ["プロップ 比較", "プロップ おすすめ", "プロップ 選び方", "プロップ どれ", "プロップ どこ",
                     "FTMO 以外", "Fintokei 比較", "プロップ ランキング", "プロップ 違い", "プロップ 日本語"],
        "cta": {"末尾": "\n---\nFintokei・FTMO・Funded7など複数のプロップファームを比較した記事があります。\n\n→ https://dysonblog.org/best-prop-trading-firms/\n\n→ 無料メール登録\n"},
    },
    "prop_what": {
        "priority": 2, "url": "https://dysonblog.org/prop-trader/",
        "label": "プロップトレーダーとは",
        "triggers": ["プロップトレーダーとは", "プロップファームとは", "プロップ なり方", "プロップ 仕組み",
                     "プロップ 副業", "プロップ 税金", "プロップ 将来性", "プロップ デメリット", "プロップ リスク"],
        "cta": {"末尾": "\n---\nプロップトレーダーの仕組み・なり方・税金・副業としての可能性をまとめています。\n\n→ https://dysonblog.org/prop-trader/\n\n→ 無料メール登録\n"},
    },
    "fx_account": {
        "priority": 3, "url": "https://dysonblog.org/fx-domestic-or-overseas/",
        "label": "FX口座選び・始め方",
        "triggers": ["FX 始め", "FX 口座", "口座開設", "海外FX", "国内FX", "FX 選び方", "FX おすすめ", "FX どれ",
                     "Land Prime", "ランドプライム", "XM", "IS6FX", "Exness", "AXIORY", "FXGT", "スプレッド 比較",
                     "レバレッジ 比較", "証拠金", "ハイレバ", "レバレッジ 無制限", "FXの始め方", "FX 入門", "FX 登録"],
        "cta": {"末尾": "\n---\nFX口座の選び方（国内・海外の違い、スキャルピング向きの選択肢）をまとめています。\n\n→ https://dysonblog.org/fx-domestic-or-overseas/\n\n→ 無料メール登録\n"},
    },
    "scalping": {
        "priority": 3, "url": "https://dysonblog.org/prop-farm-scalping/",
        "label": "スキャルピング×プロップ攻略",
        "triggers": ["スキャルピング", "スキャルプ", "スキャル", "1分足", "5分足", "15分足", "秒スキャ", "高速スキャル",
                     "損切り", "利確", "エントリー", "ロット", "勝率", "リスクリワード", "手法", "ストラテジー",
                     "ロジック", "プライスアクション", "ボリンジャーバンド", "アリゲーター", "フラクタル"],
        "cta": {
            "前半": "> スキャルピング手法をプロップファームで実践的に使う戦略をまとめています。\n> → https://dysonblog.org/prop-farm-scalping/\n",
            "末尾": "\n---\nこのスキャルピング手法をプロップファームのチャレンジで使う場合の\n具体的な設定・戦略をまとめています。\n\n→ https://dysonblog.org/prop-farm-scalping/\n\n→ 無料メール登録\n",
        },
    },
    "channel_line": {
        "priority": 3, "url": "https://dysonblog.org/channel-line-fx-guide/",
        "label": "チャネルライン完全ガイド",
        "triggers": ["チャネルライン", "センターライン", "チャネルライン 引き方", "チャネルライン 手法",
                     "チャネルライン 使い方", "チャネルライン 勝てない"],
        "cta": {"末尾": "\n---\nチャネルラインを活用した具体的なトレード手法をまとめています。\n\n→ https://dysonblog.org/channel-line-fx-guide/\n\n→ 無料メール登録\n"},
    },
    "prop_beginner": {
        "priority": 2, "url": "https://dysonblog.org/how-to-become-a-prop-trader/",
        "label": "完全未経験からのプロップトレーダー",
        "triggers": ["勉強", "独学", "トレーダー なるには", "プロップ 未経験", "プロップ 初心者", "プロップ ゼロから"],
        "cta": {"末尾": "\n---\nFXをゼロから始めてプロップトレーダーを目指す完全ロードマップをまとめています。\n\n→ https://dysonblog.org/how-to-become-a-prop-trader/\n\n→ 無料メール登録\n"},
    },
    "fx_knowledge": {
        "priority": 3, "url": "https://dysonblog.org/propfarm-strategy/",
        "label": "プロップ攻略・合格率・失格しない秘訣",
        "triggers": ["テクニカル", "ファンダメンタル", "トレンド", "レンジ", "エリオット波動", "フィボナッチ",
                     "損小利大", "資金管理", "負けパターン", "メンタル トレード", "待つ 技術", "ルール 作り方",
                     "三尊", "ダブルトップ", "確信度", "トレード 反省", "勝てない", "負け理由"],
        "cta": {"末尾": "\n---\nこのノウハウをプロップファームのチャレンジで実践するための戦略をまとめています。\n\n→ https://dysonblog.org/propfarm-strategy/\n\n→ 無料メール登録\n"},
    },
    "mental": {
        "priority": 3, "url": "https://dysonblog.org/prop-trader-side-job/",
        "label": "副業・在宅・自由→プロップ",
        "triggers": ["孤独", "仕事 辛い", "仕事 嫌", "会社 辞め", "会社 嫌", "副業", "在宅", "在宅ワーク", "引きこもり",
                     "人間関係", "理不尽", "評価 されない", "自由", "場所 選ばない", "フリーランス", "独立", "メンタル",
                     "疲れ", "ストレス", "一人 仕事", "会社員 限界"],
        "cta": {
            "前半": "\n> 正直に言います。私も10以上の職場で同じことを感じてきました。\n> その経験からトレードに入り、今はプロップファームの攻略を発信しています。\n> → https://dysonblog.org/prop-trader-side-job/\n",
            "末尾": "\n---\n最後まで読んでくださってありがとうございます。\n\n私がトレードの世界に入ったのも、同じような「理不尽さ」がきっかけでした。\n副業・在宅としてのプロップトレーダーの実態を正直にまとめています。\n\n→ https://dysonblog.org/prop-trader-side-job/\n\n→ 無料メール登録\n",
        },
    },
    "currency_pair": {
        "priority": 3, "url": "https://dysonblog.org/scalping-currency-pairs/",
        "label": "スキャルピング通貨ペア・ゴールド攻略",
        "triggers": ["ゴールド", "XAUUSD", "XAU", "通貨ペア 選び", "通貨ペア おすすめ", "USDJPY", "EURUSD", "GBPUSD",
                     "日経225", "日経 スキャル", "Exness ゴールド", "スプレッド ゴールド"],
        "cta": {"末尾": "\n---\nスキャルピングに向いている通貨ペアの選び方とゴールド攻略をまとめています。\n\n→ https://dysonblog.org/scalping-currency-pairs/\n\n→ 無料メール登録\n"},
    },
    "default": {
        "priority": 99, "url": "https://dysonblog.org/fintokei/",
        "label": "Fintokei（フィントケイ）完全ガイド",
        "triggers": [],
        "cta": {"末尾": "\n---\n最後まで読んでくださってありがとうございます。\n\n私がまとめているFintokei解説ページも、よければ読んでみてください。\n\n→ https://dysonblog.org/fintokei/\n\n→ 無料メール登録\n"},
    },
}
