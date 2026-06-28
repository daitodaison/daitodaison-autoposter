import json, os, asyncio, logging, glob, requests, re
from pathlib import Path

ARTICLES_DIR = "queue"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# Substack非公式API方式（Playwright不使用）
# - connect.sid Cookie でHTTPリクエストを直接送信
# - Cloudflare Bot検知を完全回避
# - substack.sid は数ヶ月有効（サインアウトしない限り）
#
# 主要エンドポイント:
#   GET  /api/v1/user  → ユーザー情報・publication_id取得
#   POST /api/v1/drafts → 下書き作成
#   POST /api/v1/posts/{id}/publish → 公開
# ============================================================

SUBSTACK_BASE = "https://substack.com"

def md_to_html(text):
    """MarkdownをSubstack API用HTMLに変換"""
    lines = text.split('\n')
    result = []
    for line in lines:
        line = line.rstrip()
        if line.startswith('### '):
            result.append(f'<h3>{line[4:]}</h3>')
        elif line.startswith('## '):
            result.append(f'<h2>{line[3:]}</h2>')
        elif line.startswith('# '):
            result.append(f'<h1>{line[2:]}</h1>')
        elif line.startswith('---'):
            result.append('<hr>')
        elif line == '':
            result.append('<p><br></p>')
        else:
            # **bold** → <strong>
            line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            # *italic* → <em>
            line = re.sub(r'\*(.+?)\*', r'<em>\1</em>', line)
            result.append(f'<p>{line}</p>')
    return '\n'.join(result)

def get_session(sid):
    """requests.Session にSubstack認証Cookieをセット"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": "https://substack.com/",
        "Origin": "https://substack.com",
    })
    session.cookies.set("substack.sid", sid, domain=".substack.com")
    return session

def get_user_info(session):
    """ログインユーザー情報とpublication情報を取得"""
    resp = session.get(f"{SUBSTACK_BASE}/api/v1/user", timeout=30)
    log.info(f"【STEP1】user API status: {resp.status_code}")
    if resp.status_code != 200:
        log.error(f"【STEP1】認証失敗: {resp.text[:200]}")
        return None
    data = resp.json()
    log.info(f"【STEP1】ユーザー: {data.get('name', '?')} / {data.get('email', '?')}")

    # publication一覧を取得
    pubs = data.get("publicationUsers", [])
    if not pubs:
        log.error("【STEP1】publicationが見つかりません")
        return None

    pub = pubs[0]["publication"]
    log.info(f"【STEP1】publication: {pub.get('name')} / subdomain={pub.get('subdomain')}")
    return {
        "user_id": data.get("id"),
        "pub_id": pub.get("id"),
        "subdomain": pub.get("subdomain"),
        "pub_name": pub.get("name"),
    }

def upload_image(session, image_path, subdomain):
    """カバー画像をSubstackにアップロードしてURLを取得"""
    log.info(f"【STEP3】画像アップロード開始: {image_path}")
    upload_url = f"https://{subdomain}.substack.com/api/v1/image"
    with open(image_path, "rb") as f:
        files = {"image": (Path(image_path).name, f, "image/jpeg")}
        headers = {k: v for k, v in session.headers.items() if k != "Content-Type"}
        resp = requests.post(
            upload_url, files=files, cookies=session.cookies,
            headers=headers, timeout=60
        )
    log.info(f"【STEP3】画像アップロード status: {resp.status_code}")
    if resp.status_code == 200:
        url = resp.json().get("url", "")
        log.info(f"【STEP3】画像URL取得: {url[:80]}...")
        return url
    else:
        log.error(f"【STEP3】画像アップロード失敗: {resp.text[:200]}")
        return None

def create_draft(session, subdomain, title, body_html, cover_image_url=None):
    """下書きを作成してdraft_idを返す"""
    log.info("【STEP4】下書き作成...")

    # Substackの投稿本文はdraftbodyというHTMLフィールド
    payload = {
        "draft_title": title,
        "draft_body": body_html,
        "draft_subtitle": "",
        "section_chosen": False,
        "type": "newsletter",
    }
    if cover_image_url:
        payload["cover_image"] = cover_image_url

    url = f"https://{subdomain}.substack.com/api/v1/drafts"
    resp = session.post(url, json=payload, timeout=30)
    log.info(f"【STEP4】下書き作成 status: {resp.status_code}")

    if resp.status_code in (200, 201):
        data = resp.json()
        draft_id = data.get("id")
        log.info(f"【STEP4】下書き作成成功: draft_id={draft_id}")
        return draft_id
    else:
        log.error(f"【STEP4】下書き作成失敗: {resp.text[:300]}")
        return None

def publish_draft(session, subdomain, draft_id, send_email=False):
    """下書きを公開する"""
    log.info(f"【STEP5】公開処理: draft_id={draft_id}")
    payload = {
        "send": send_email,
        "share_automatically": False,
    }
    url = f"https://{subdomain}.substack.com/api/v1/posts/{draft_id}/publish"
    resp = session.post(url, json=payload, timeout=30)
    log.info(f"【STEP5】公開 status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        post_url = data.get("canonical_url", "")
        log.info(f"【STEP5】公開成功: {post_url}")
        return True
    else:
        log.error(f"【STEP5】公開失敗: {resp.text[:300]}")
        return False

def post_substack(article, test_mode=False):
    title = article.get("title", "無題")
    body = article.get("body", "")
    image_path = article.get("image_path", "")
    publish_mode = os.environ.get("PUBLISH_MODE", "draft")

    # substack.sidの取得（URL-decodeして渡す）
    sid_raw = os.environ.get("SUBSTACK_SID", "")
    if not sid_raw:
        log.error("SUBSTACK_SID が設定されていません")
        return False
    # URL-encodeされている場合はデコード
    from urllib.parse import unquote
    sid = unquote(sid_raw)
    log.info(f"【準備】sid先頭20文字: {sid[:20]}...")

    body_html = md_to_html(body)
    log.info(f"【準備】タイトル: {title}")
    log.info(f"【準備】本文HTML文字数: {len(body_html)}")

    session = get_session(sid)

    # STEP1: ユーザー情報取得
    user_info = get_user_info(session)
    if not user_info:
        return False
    subdomain = user_info["subdomain"]

    # STEP2: テストモードの場合はAPI疎通確認だけして終了
    if test_mode:
        log.info("【テストモード】API認証OK・ユーザー情報取得成功")
        log.info(f"【テストモード】publication: {user_info['pub_name']} ({subdomain}.substack.com)")

    # STEP3: 画像アップロード
    cover_image_url = None
    if image_path and os.path.exists(image_path):
        cover_image_url = upload_image(session, image_path, subdomain)
    else:
        log.info(f"【STEP3】画像スキップ（image_path='{image_path}'）")

    # STEP4: 下書き作成
    draft_id = create_draft(session, subdomain, title, body_html, cover_image_url)
    if not draft_id:
        return False

    log.info(f"【STEP4】下書きURL: https://{subdomain}.substack.com/publish/post/{draft_id}")

    if test_mode:
        log.info("【テストモード】下書き作成まで完了（公開はしません）")
        log.info(f"【テストモード】Substackの下書き一覧で確認: https://{subdomain}.substack.com/publish")
        return True

    # STEP5: 公開
    if publish_mode == "publish":
        ok = publish_draft(session, subdomain, draft_id, send_email=False)
        log.info(f"【最終サマリー】公開={'成功' if ok else '失敗'}, タイトル={title}")
        return ok
    else:
        log.info(f"【STEP5】PUBLISH_MODE=draft → 下書き保存のまま終了")
        log.info(f"【STEP5】確認URL: https://{subdomain}.substack.com/publish/post/{draft_id}/edit")
        log.info(f"【最終サマリー】下書き保存成功, タイトル={title}")
        return True

def run():
    test_mode = os.environ.get("SUBSTACK_TEST_MODE", "").lower() in ("1", "true", "yes")

    if test_mode:
        test_image_path = os.environ.get("SUBSTACK_TEST_IMAGE_PATH", "").strip()
        if not test_image_path:
            candidates = sorted(glob.glob("images/*.jpg") + glob.glob("images/*.png"))
            if candidates:
                test_image_path = candidates[0]
                log.info(f"【テストモード】画像自動選択: {test_image_path}")

        files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
        if files:
            with open(files[0], encoding="utf-8") as f:
                article = json.load(f)
            log.info(f"【テストモード】queue/の記事を使用: {files[0]}")
        else:
            article = {
                "title": "Substackテスト投稿（API方式）",
                "body": "これはSubstack自動投稿のテストです。\n\n## テスト項目\n\nHTTPリクエスト方式でCloudflare検知を回避しています。\n\n本文と画像が正しく投稿されているか確認します。",
                "image_path": test_image_path,
            }
            log.info("【テストモード】ダミー記事を使用")

        if test_image_path:
            article["image_path"] = test_image_path

        result = post_substack(article, test_mode=True)
        log.info(f"【テストモード】完了: {'成功' if result else '失敗'}")
        return

    # 通常モード
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    result = post_substack(article)
    log.info(f"Substack完了: {'成功' if result else '失敗'}")

if __name__ == "__main__":
    run()
