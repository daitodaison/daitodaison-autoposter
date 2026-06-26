# -*- coding: utf-8 -*-
"""
AIフォールバック呼び出し
GAS版の _callAI_withFallback / _callAI_セクション別 を移植
優先順位: Groq（無料・高速） → Gemini（バックアップ） → OpenRouter（高品質セクション用）
"""

import time
import json
import logging
import requests
from config import CONFIG

log = logging.getLogger(__name__)

# gemma2-9b-it は2025年に廃止済み → 削除
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]


def call_ai_section(prompt_text, max_tokens, section_name):
    """高品質が必要なセクション（考察・まとめ等）はOpenRouterを優先"""
    高品質セクション = ["考察", "まとめ", "Insight", "Message", "Result"]
    高品質が必要 = any(s in section_name for s in 高品質セクション)

    if 高品質が必要 and CONFIG.get("OPENROUTER_API_KEY"):
        log.info(f"  >> 高品質モデルを使用: {section_name}")
        for model in CONFIG.get("OpenRouterモデル候補_高品質", []):
            try:
                res = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {CONFIG['OPENROUTER_API_KEY']}",
                        "HTTP-Referer": CONFIG.get("トップURL", ""),
                        "X-Title": "Note Article Generator",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt_text}],
                        "max_tokens": max_tokens,
                    },
                    timeout=60,
                )
                if res.status_code == 200:
                    data = res.json()
                    if data.get("choices"):
                        text = data["choices"][0]["message"]["content"]
                        log.info(f"✅ 高品質モデル成功: {model}")
                        return text
            except Exception as e:
                log.warning(f"⚠️ 高品質モデル失敗: {e}")

    return call_ai_with_fallback(prompt_text, max_tokens)


def call_ai_with_fallback(prompt_text, max_tokens=2000):
    """
    GAS版 _callAI_withFallback の完全移植
    Groq → Gemini → OpenRouter の順でフォールバック
    戻り値: テキスト文字列 or None
    """

    # 1. Groq（最優先・無料・高速）
    if CONFIG.get("GROQ_API_KEY"):
        for model in GROQ_MODELS:
            try:
                res = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {CONFIG['GROQ_API_KEY']}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt_text}],
                        "max_tokens": max_tokens,
                    },
                    timeout=60,
                )
                code = res.status_code

                if code == 200:
                    data = res.json()
                    if data.get("choices"):
                        text = data["choices"][0]["message"]["content"]
                        log.info(f"✅ Groq ({model}) 成功")
                        return text

                if code in (429, 402):
                    log.warning(f"⏳ Groq制限 → 90秒待機して次モデルへ ({model})")
                    time.sleep(90)
                    continue

                err = res.text[:200]
                log.warning(f"⚠️ Groq ({model}) 失敗: {code} | {err}")

            except Exception as e:
                log.warning(f"⚠️ Groq例外 ({model}): {e}")

    # 2. Gemini（バックアップ）
    if CONFIG.get("GEMINI_API_KEY"):
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        for attempt in range(3):
            for model in GEMINI_MODELS:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={CONFIG['GEMINI_API_KEY']}"
                )
                try:
                    res = requests.post(
                        url,
                        json={
                            "contents": [{"parts": [{"text": prompt_text}]}],
                            "generationConfig": {"maxOutputTokens": max_tokens},
                            "safetySettings": safety_settings,
                        },
                        timeout=60,
                    )
                    code = res.status_code

                    if code == 200:
                        data = res.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            text = candidates[0]["content"]["parts"][0]["text"]
                            log.info(f"✅ Gemini ({model}) 成功")
                            return text

                    if code == 429:
                        log.warning(f"⏳ Geminiレート制限 → 70秒待機 (試行{attempt+1}/3)")
                        time.sleep(70)
                        break  # モデルループを抜けてリトライ

                    if code == 404:
                        log.warning(f"⚠️ Gemini ({model}) モデル非対応 → スキップ")
                        continue

                    log.warning(f"⚠️ Gemini ({model}) 失敗: {code}")

                except Exception as e:
                    log.warning(f"⚠️ Gemini例外 ({model}): {e}")

    # 3. OpenRouter（最終フォールバック）
    if CONFIG.get("OPENROUTER_API_KEY"):
        for model in CONFIG.get("OpenRouterモデル候補", []):
            try:
                res = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {CONFIG['OPENROUTER_API_KEY']}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt_text}],
                        "max_tokens": max_tokens,
                    },
                    timeout=60,
                )
                if res.status_code == 200:
                    data = res.json()
                    if data.get("choices"):
                        text = data["choices"][0]["message"]["content"]
                        log.info(f"✅ OpenRouter ({model}) 成功")
                        return text
                log.warning(f"⚠️ OpenRouter ({model}) 失敗: {res.status_code}")
            except Exception as e:
                log.warning(f"⚠️ OpenRouter例外 ({model}): {e}")

    log.error("❌ 全API失敗")
    return None
