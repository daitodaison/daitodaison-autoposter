# -*- coding: utf-8 -*-
"""
データ管理ロジック
GAS版のスプレッドシート（KWマスタ・記事生成・記事本文DB）と
PropertiesService（進捗保存）の代替。すべてJSONファイルで管理する。

ディレクトリ構成:
  data/keywords.json      … キーワードマスタ（GAS: KWマスタシート）
  data/articles_db.json   … 生成済み記事の本文DB（GAS: 記事本文DBシート）
  queue/                  … 投稿待ちJSON（既存のrun_all.py構成と互換）
  posted/                 … 投稿済みJSON（既存のrun_all.py構成と互換）
"""
import os
import json
import glob
import logging
from datetime import datetime

log = logging.getLogger(__name__)

DATA_DIR = "data"
KEYWORDS_FILE = os.path.join(DATA_DIR, "keywords.json")
ARTICLES_DB_FILE = os.path.join(DATA_DIR, "articles_db.json")
QUEUE_DIR = "queue"
POSTED_DIR = "posted"


def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(QUEUE_DIR, exist_ok=True)
    os.makedirs(POSTED_DIR, exist_ok=True)


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"⚠️ JSON読み込み失敗 ({path}): {e}")
        return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =================================================================
# キーワードマスタ（GAS: KWマスタシート）
# =================================================================
def load_keywords():
    """戻り値: [{kw, status, reason, urls, trend, updated_at}, ...]"""
    _ensure_dirs()
    return _load_json(KEYWORDS_FILE, [])


def save_keywords(keywords):
    _ensure_dirs()
    _save_json(KEYWORDS_FILE, keywords)


def add_new_keywords(new_kws):
    """新規キーワードを「待機中」状態でキーワードマスタに追加"""
    keywords = load_keywords()
    existing = {k["kw"] for k in keywords}
    added = 0
    for kw in new_kws:
        if kw not in existing:
            keywords.append({
                "kw": kw, "status": "待機中", "reason": "-",
                "urls": [], "trend": "-", "avg_length": 0,
                "updated_at": datetime.now().isoformat(),
            })
            existing.add(kw)
            added += 1
    save_keywords(keywords)
    log.info(f"✅ {added}件のキーワードを追加完了")
    return added


def update_keyword_status(kw, status, reason=None, urls=None):
    keywords = load_keywords()
    for k in keywords:
        if k["kw"] == kw:
            k["status"] = status
            if reason is not None:
                k["reason"] = reason
            if urls is not None:
                k["urls"] = urls
            k["updated_at"] = datetime.now().isoformat()
            break
    save_keywords(keywords)


def update_keyword_trend(kw, trend_text, avg_length):
    keywords = load_keywords()
    for k in keywords:
        if k["kw"] == kw:
            k["trend"] = trend_text
            k["avg_length"] = avg_length
            break
    save_keywords(keywords)


def get_keywords_by_status(*statuses):
    keywords = load_keywords()
    return [k for k in keywords if k["status"] in statuses]


def count_waiting_keywords():
    return len(get_keywords_by_status("待機中"))


# =================================================================
# 記事本文DB（GAS: 記事本文DBシート）
# =================================================================
def load_articles_db():
    """戻り値: [{kw, title, body, score, created_at}, ...]"""
    _ensure_dirs()
    return _load_json(ARTICLES_DB_FILE, [])


def save_article_to_db(kw, title, body, score):
    db = load_articles_db()
    db.append({
        "kw": kw, "title": title, "body": body, "score": score,
        "created_at": datetime.now().isoformat(),
    })
    _save_json(ARTICLES_DB_FILE, db)
    log.info(f"✅ 記事本文DBに保存: {title}")


def get_used_topics():
    """重複防止用：DBおよびposted/に存在するトピック一覧"""
    topics = {a["kw"] for a in load_articles_db()}
    for filepath in glob.glob(f"{POSTED_DIR}/*.json"):
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
                if data.get("topic"):
                    topics.add(data["topic"])
        except Exception:
            pass
    return topics


def get_all_body_lengths():
    """目標文字数算出用：DB内の全本文の文字数リスト"""
    return [len(a["body"]) for a in load_articles_db()]


# =================================================================
# キュー（既存のrun_all.py構成と互換）
# =================================================================
def push_to_queue(title, body, topic, image_path=None):
    _ensure_dirs()
    filename = f"{QUEUE_DIR}/{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    payload = {"title": title, "body": body, "topic": topic}
    if image_path:
        payload["image_path"] = image_path
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info(f"📥 キューに追加: {filename}")
    return filename
