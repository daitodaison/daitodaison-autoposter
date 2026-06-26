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


def call_ai_section(prompt_text, max_tokens, section_name):
    """高品質が必要なセクション（考察・まとめ等）はOpenRouterを優先"""
    高品質セクション = ["考察", "まとめ", "Insight", "Message", "Result"]
    高品質が必要 = any(s in section_name for s in 高品質セクション)

    if 高品質が必要 and CONFIG["OPENROUTER_API_KEY"]:
        log.info(f"  >> 高品質モデルを使用: {section_name}")
        for model in CONFIG["OpenRouterモデル候補_高品質"]:
            try:
                res = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {CONFIG['OPENROUTER_API_KEY']}",
                        "HTTP-Referer": CONFIG["トップURL"],
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
                        log.info(f"✅ 高品質モデル成功: {model}")
                        return {"text": data["choices"][0]["message"]["content"]}
            except Exception as e:
                log.warning(f"⚠️ 高品質モデル失敗: {e}")

    return call_ai_with_fallback(prompt_text, max_tokens)


def call_ai_with_fallback(prompt_text, max_tokens):
    """Groq → Gemini の順でフォールバック。戻り値: {"text": "..."} または None"""

    # 1. Groq（最優先・無料・高速）
    if CONFIG["GROQ_API_KEY"]:
        for model in CONFIG["Groqモデル候補"]:
            try:
                res = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {CONFIG['GROQ_API_KEY']}"},
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
                        log.info(f"✅ Groq ({model}) 成功")
                        return {"text": data["choices"][0]["message"]["content"]}
                if code in (429, 402):
                    log.info(f"⏳ Groq制限 → 90秒待機して次モデルへ ({model})")
                    time.sleep(90)
                    continue
                log.warning(f"⚠️ Groq ({model}) 失敗: {code} | {res.text[:200]}")
            except Exception as e:
                log.warning(f"⚠️ Groq例外: {e}")

    # 2. Gemini（バックアップ）
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    if CONFIG["GEMINI_API_KEY"]:
        for attempt in range(3):
            for model in CONFIG["Geminiモデル候補"]:
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
                        log.info(f"✅ Gemini ({model}) 成功")
                        data = res.json()
                        text = (
                            data.get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [{}])[0]
                            .get("text", "")
                        )
                        return {"text": text}
                    if code == 429:
                        log.info(f"⏳ Geminiレート制限 → 70秒待機 (試行{attempt + 1}/3)")
                        time.sleep(70)
                        break  # モデルループを抜けてリトライ
                    if code == 404:
                        log.warning(f"⚠️ Gemini ({model}) モデル非対応 → スキップ")
                        continue
                    log.warning(f"⚠️ Gemini ({model}) 失敗: {code}")
                except Exception as e:
                    log.warning(f"⚠️ Gemini例外: {e}")

    log.error("❌ 全API失敗")
    return None


def extract_text(result):
    """call_ai_with_fallback / call_ai_section の戻り値からテキストを取り出す"""
    if not result:
        return ""
    return (result.get("text") or "").strip()
