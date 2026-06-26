# -*- coding: utf-8 -*-
"""
画像生成ロジックの移植
GAS版: 画像生成処理_ / _generateImagePollinations / _generateImagePicsum / _画像プロンプト生成
Google Driveの代わりにリポジトリ内 images/ フォルダに保存する
"""
import os
import re
import time
import random
import logging
import requests
from config import CONFIG
from ai_client import call_ai_with_fallback, extract_text

log = logging.getLogger(__name__)

IMAGES_DIR = "images"


def safe_filename(kw):
    """ファイル名に使えない文字を除去"""
    return re.sub(r'[\\/:*?"<>|]', "_", kw)[:50]


def template_prompt(kw):
    """AIが使えない場合のフォールバック：キーワードに応じた固定プロンプト"""
    suffix = ", grayscale, monochrome, high contrast, minimalist"
    if "在宅" in kw or "リモート" in kw:
        return "cozy home office setup with laptop, minimalist workspace" + suffix
    if any(w in kw for w in ["副業", "稼ぐ", "収入"]):
        return "freelance work concept with laptop and growth chart" + suffix
    if any(w in kw for w in ["仕事", "転職", "辞め"]):
        return "person at crossroads career choice concept, open door" + suffix
    if any(w in kw for w in ["お金", "節約", "貯金"]):
        return "money management concept piggy bank coins budget" + suffix
    if any(w in kw for w in ["スキル", "勉強", "資格"]):
        return "online learning concept open book laptop skills" + suffix
    return "modern digital workspace concept laptop notebook" + suffix


def generate_image_prompt(kw):
    """キーワードから画像生成用の英語プロンプトをAIで作る"""
    style = CONFIG["画像設定"]["画像スタイル"]

    if CONFIG["GEMINI_API_KEY"] or CONFIG["GROQ_API_KEY"]:
        translate_prompt = (
            "以下の日本語キーワードに合ったブログ記事のヘッダー画像用の英語プロンプトを1行で出力して。"
            f"スタイル: {style}\n"
            f'キーワード: 「{kw}」\n'
            "条件: 人物なし・テキストなし・ミニマルデザイン・16:9横長\n"
            "英語1行のみ出力（説明不要）:"
        )
        res = call_ai_with_fallback(translate_prompt, 200)
        ai_prompt = extract_text(res)
        if ai_prompt and len(ai_prompt) > 10:
            ai_prompt = ai_prompt.strip("\"'")
            return f"{ai_prompt}, {style}"

    return f"{template_prompt(kw)}, {style}"


def generate_image_pollinations(prompt):
    """Pollinations APIで画像生成（無料・APIキー不要）"""
    cfg = CONFIG["画像設定"]["Pollinations"]
    max_retry = 3
    base_wait = 10
    encoded_prompt = requests.utils.quote(prompt)

    for model in cfg["モデル候補"]:
        for attempt in range(1, max_retry + 1):
            try:
                url = (
                    f"https://image.pollinations.ai/prompt/{encoded_prompt}"
                    f"?width={cfg['幅']}&height={cfg['高さ']}"
                    f"&model={model}"
                    f"&enhance={'true' if cfg['強化'] else 'false'}"
                    f"&nologo=true"
                    f"&seed={random.randint(0, 999999)}"
                )
                res = requests.get(url, timeout=60)
                if res.status_code == 200:
                    content_type = res.headers.get("Content-Type", "image/jpeg")
                    ext = "png" if "png" in content_type else "jpg"
                    return res.content, ext, f"pollinations_{model}"
                if res.status_code == 429:
                    wait = base_wait * (2 ** (attempt - 1))
                    if attempt < max_retry:
                        time.sleep(wait)
                        continue
                    break
                break
            except Exception:
                break
        time.sleep(2)
    return None, None, None


def generate_image_picsum():
    """Picsumでランダム画像を取得（最終フォールバック）"""
    cfg = CONFIG["画像設定"]["Picsum"]
    try:
        url = f"https://picsum.photos/{cfg['幅']}/{cfg['高さ']}?random={random.randint(0, 99999)}"
        res = requests.get(url, timeout=30)
        if res.status_code == 200:
            return res.content, "jpg", "picsum"
    except Exception:
        pass
    return None, None, None


def generate_image_for_article(kw, output_dir=IMAGES_DIR):
    """記事1本分の画像を生成してファイルパスを返す。失敗時はNone"""
    os.makedirs(output_dir, exist_ok=True)

    prompt = generate_image_prompt(kw)
    content, ext, service = generate_image_pollinations(prompt)
    if not content:
        content, ext, service = generate_image_picsum()

    if not content:
        log.error(f"❌ 画像生成失敗: {kw}")
        return None

    from datetime import datetime
    filename = f"{safe_filename(kw)}_{datetime.now().strftime('%Y%m%d')}.{ext}"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    log.info(f"✅ 画像生成完了（{service}）: {filepath}")
    return filepath
