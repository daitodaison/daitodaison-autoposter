import json, os, datetime, glob, logging
from pathlib import Path

QUEUE_DIR = "queue"
os.makedirs(QUEUE_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TOPICS = [
    "FX prop trading 今週の相場振り返り",
    "Fintokeiプロップトレーダーへの道",
    "XAUUSD トレード戦略まとめ",
    "プロップファームで資金調達する方法",
    "FXトレーダーの1日ルーティン",
    "メンタル管理とトレード成績の関係",
    "Fintokei無料トライアル完全ガイド",
    "損切りの重要性とリスク管理",
    "テクニカル分析基礎：移動平均線",
    "プロップトレーダーが使うインジケーター",
]

def get_topic():
    used = set()
    posted = glob.glob("posted/*.json")
    for f in posted:
        try:
            with open(f, encoding="utf-8") as fp:
                d = json.load(fp)
                used.add(d.get("topic", ""))
        except:
            pass
    for t in TOPICS:
        if t not in used:
            return t
    return TOPICS[len(posted) % len(TOPICS)]

def generate_article(topic):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = f"""【{topic}】についての解説記事です。

■ はじめに
こんにちは、daitodaisonです。FXプロップトレーダーとして日々相場と向き合っています。
今回は「{topic}」についてお伝えします。

■ ポイント1
プロップトレーディングで重要なのはリスク管理です。
Fintokeiでは最大損失率が決まっているため、規律あるトレードが求められます。

■ ポイント2
XAUUSD（金）は流動性が高く、トレンドが出やすい銘柄です。
私自身も主戦場としており、Fintokei競技では上位2.3%（2587人中61位）を達成しました。

■ まとめ
プロップファームで稼ぐには技術だけでなく、メンタルと資金管理が鍵です。
Fintokeiの無料トライアルで練習してみてください。

投稿時刻: {now}
"""
    return body

def run():
    topic = get_topic()
    title = f"{topic}【daitodaison】"
    body = generate_article(topic)
    filename = f"{QUEUE_DIR}/{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"title": title, "body": body, "topic": topic}, f, ensure_ascii=False, indent=2)
    log.info(f"記事生成完了: {title}")

if __name__ == "__main__":
    run()