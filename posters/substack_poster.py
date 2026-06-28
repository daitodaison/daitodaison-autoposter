import json, os, asyncio, logging, glob, requests, re
from pathlib import Path
from urllib.parse import unquote

ARTICLES_DIR = "queue"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# ⚠️ 【Substack投稿仕様メモ 2026/06/28時点】
#
# 【方式】Playwright（ブラウザ操作）を廃止し、HTTPリクエスト直接送信方式に変更
#   理由: GitHub ActionsのIPアドレスをCloudflareがBot判定してブロックするため
#   解決: substack.sid Cookie を使ってAPIを直接叩く → Bot検知を完全回避
#
# 【認証】
#   - 環境変数 SUBSTACK_SID に substack.sid の値をセット
#   - Chrome DevTools → Application → Cookies → https://substack.com → substack.sid
#   - このCookieはサインアウトしない限り数ヶ月有効
#
# 【主要APIエンドポイント】
#   GET  https://substack.com/api/v1/user
#     → ログインユーザー情報・publication情報（subdomain, pub_id）を取得
#   POST https://{subdomain}.substack.com/api/v1/image
#     → カバー画像アップロード → 画像URLを返す
#   POST https://{subdomain}.substack.com/api/v1/drafts
#     → 下書き作成 → draft_id を返す
#     payload: { draft_title, draft_body(HTML), cover_image(URL), type="newsletter" }
#   POST https://{subdomain}.substack.com/api/v1/posts/{draft_id}/publish
#     → 下書きを公開
#     payload: { send: bool, share_automatically: bool }
#
# 【本文フォーマット】
#   - draft_body はHTML形式で送信
#   - Markdown → HTML変換は md_to_html() で処理
#   - <p>, <h2>, <h3>, <strong>, <em>, <hr> などが使用可能
#
# 【動作モード】
#   SUBSTACK_TEST_MODE=true  → 下書き作成まで（公開しない）テスト用
#   PUBLISH_MODE=draft       → 下書き保存（通常運用時のデフォルト）
#   PUBLISH_MODE=publish     → 即時公開（send_email=False でメール配信なし）
# ============================================================

SUBSTACK_BASE = "https://substack.com"


def md_to_html(text):
    """
    MarkdownテキストをSubstack API投稿用のHTMLに変換する。

    変換ルール:
      ### 見出し → <h3>
      ## 見出し  → <h2>
      # 見出し   → <h1>
      ---        → <hr>
      空行       → <p><br></p>
      **text**   → <strong>text</strong>
      *text*     → <em>text</em>
      それ以外   → <p>text</p>
    """
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
    """
    requests.Session に Substack 認証 Cookie をセットして返す。

    substack.sid は URL エンコードされている場合があるので unquote() で変換する。
    ヘッダーは通常のブラウザに近い形に設定し、API レスポンスが JSON で返るようにする。
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": "https://substack.com/",
        "Origin": "https://substack.com",
    })
    # URL エンコードされたまま Cookie にセットすると認証に失敗するためデコードする
    session.cookies.set("substack.sid", unquote(sid), domain=".substack.com")
    return session


def get_user_info(session):
    """
    GET /api/v1/user でログインユーザー情報と publication 情報を取得する。

    返却値（dict）:
      user_id   : Substack ユーザー ID
      pub_id    : publication の内部 ID
      subdomain : publication のサブドメイン（例: daitodaison）
      pub_name  : publication の表示名

    認証失敗（status != 200）または publication が存在しない場合は None を返す。
    """
    resp = session.get(f"{SUBSTACK_BASE}/api/v1/user", timeout=30)
    log.info(f"【STEP1】user API status: {resp.status_code}")

    if resp.status_code != 200:
        log.error(f"【STEP1】認証失敗 → SUBSTACK_SID を確認してください: {resp.text[:200]}")
        return None

    data = resp.json()
    log.info(f"【STEP1】ログインユーザー: {data.get('name', '?')} / {data.get('email', '?')}")

    # publicationUsers の最初のエントリを使用
    pubs = data.get("publicationUsers", [])
    if not pubs:
        log.error("【STEP1】publication が見つかりません（Substack にブログが存在するか確認）")
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
    """
    カバー画像を Substack にアップロードし、画像 URL を返す。

    POST https://{subdomain}.substack.com/api/v1/image
    multipart/form-data で送信する（Content-Type ヘッダーを上書き）。

    成功時: 画像 URL（文字列）を返す
    失敗時: None を返す
    """
    log.info(f"【STEP3】画像アップロード開始: {image_path}")
    upload_url = f"https://{subdomain}.substack.com/api/v1/image"

    with open(image_path, "rb") as f:
        files = {"image": (Path(image_path).name, f, "image/jpeg")}
        # multipart 送信のため Content-Type ヘッダーを除外して requests に自動設定させる
        headers = {k: v for k, v in session.headers.items() if k != "Content-Type"}
        resp = requests.post(
            upload_url,
            files=files,
            cookies=session.cookies,
            headers=headers,
            timeout=60
        )

    log.info(f"【STEP3】画像アップロード status: {resp.status_code}")

    if resp.status_code == 200:
        url = resp.json().get("url", "")
        log.info(f"【STEP3】画像URL取得成功: {url[:80]}...")
        return url
    else:
        log.error(f"【STEP3】画像アップロード失敗: {resp.text[:200]}")
        return None


def create_draft(session, subdomain, title, body_html, cover_image_url=None):
    """
    Substack に下書きを作成し、draft_id を返す。

    POST https://{subdomain}.substack.com/api/v1/drafts
    payload:
      draft_title    : 記事タイトル
      draft_body     : 本文（HTML形式）
      draft_subtitle : サブタイトル（空文字で送信）
      cover_image    : カバー画像URL（省略可）
      type           : "newsletter"（固定）

    成功時: draft_id（int）を返す
    失敗時: None を返す
    """
    log.info("【STEP4】下書き作成開始...")

    payload = {
        "draft_title": title,
        "draft_body": body_html,
        "draft_subtitle": "",
        "section_chosen": False,
        "type": "newsletter",
    }
    if cover_image_url:
        payload["cover_image"] = cover_image_url
        log.info(f"【STEP4】カバー画像をpayloadにセット: {cover_image_url[:60]}...")

    url = f"https://{subdomain}.substack.com/api/v1/drafts"
    resp = session.post(url, json=payload, timeout=30)
    log.info(f"【STEP4】下書き作成 status: {resp.status_code}")

    if resp.status_code in (200, 201):
        data = resp.json()
        draft_id = data.get("id")
        log.info(f"【STEP4】下書き作成成功: draft_id={draft_id}")
        log.info(f"【STEP4】編集URL: https://{subdomain}.substack.com/publish/post/{draft_id}/edit")
        return draft_id
    else:
        log.error(f"【STEP4】下書き作成失敗: {resp.text[:300]}")
        return None


def publish_draft(session, subdomain, draft_id, send_email=False):
    """
    作成済みの下書きを公開する。

    POST https://{subdomain}.substack.com/api/v1/posts/{draft_id}/publish
    payload:
      send              : メール配信するか（通常は False）
      share_automatically: SNS自動共有するか（False固定）

    成功時: True を返す
    失敗時: False を返す
    """
    log.info(f"【STEP5】公開処理開始: draft_id={draft_id}, send_email={send_email}")

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
    """
    Substack 投稿のメイン処理。

    引数:
      article   : { "title": str, "body": str, "image_path": str } の dict
      test_mode : True の場合は下書き作成まで行い公開しない

    動作フロー:
      STEP1: SUBSTACK_SID で認証 → ユーザー情報・subdomain 取得
      STEP2: テストモードの場合は認証確認ログを出して続行
      STEP3: 画像がある場合はアップロードして URL 取得
      STEP4: 下書き作成（draft_id 取得）
      STEP5: PUBLISH_MODE に応じて公開 or 下書き保存で終了

    返却値: 成功=True, 失敗=False
    """
    title = article.get("title", "無題")
    body = article.get("body", "")
    image_path = article.get("image_path", "")
    publish_mode = os.environ.get("PUBLISH_MODE", "draft")

    # ── 認証情報の取得 ────────────────────────────────────────
    sid_raw = os.environ.get("SUBSTACK_SID", "")
    if not sid_raw:
        log.error("SUBSTACK_SID が設定されていません")
        log.error("GitHub Secrets に SUBSTACK_SID を追加してください")
        return False

    # URL エンコードされている場合はデコード（Chrome からコピーすると %3A 等が含まれる）
    sid = unquote(sid_raw)
    log.info(f"【準備】sid 先頭20文字: {sid[:20]}...")

    body_html = md_to_html(body)
    log.info(f"【準備】タイトル: {title}")
    log.info(f"【準備】本文HTML文字数: {len(body_html)}")
    log.info(f"【準備】publish_mode: {publish_mode}, test_mode: {test_mode}")

    session = get_session(sid)

    # ── STEP1: ユーザー情報取得 ──────────────────────────────
    user_info = get_user_info(session)
    if not user_info:
        return False
    subdomain = user_info["subdomain"]

    # ── STEP2: テストモード確認ログ ──────────────────────────
    if test_mode:
        log.info("【テストモード】API認証OK・ユーザー情報取得成功")
        log.info(f"【テストモード】publication: {user_info['pub_name']} ({subdomain}.substack.com)")
        log.info("【テストモード】下書き作成まで実行します（公開はしません）")

    # ── STEP3: カバー画像アップロード ────────────────────────
    cover_image_url = None
    if image_path and os.path.exists(image_path):
        cover_image_url = upload_image(session, image_path, subdomain)
        if not cover_image_url:
            log.warning("【STEP3】画像アップロード失敗 → 画像なしで続行")
    else:
        log.info(f"【STEP3】画像スキップ（image_path='{image_path}'）")

    # ── STEP4: 下書き作成 ────────────────────────────────────
    draft_id = create_draft(session, subdomain, title, body_html, cover_image_url)
    if not draft_id:
        log.error("【STEP4】下書き作成失敗 → 処理中断")
        return False

    # ── STEP5: 公開 or 下書き保存 ───────────────────────────
    if test_mode:
        log.info("【テストモード】下書き作成完了（公開はしません）")
        log.info(f"【テストモード】Substackで確認: https://{subdomain}.substack.com/publish")
        log.info(
            f"【最終サマリー】"
            f"認証=OK, 画像={'あり' if cover_image_url else 'なし'}, "
            f"下書き=作成済み(id={draft_id}), 公開=スキップ"
        )
        return True

    if publish_mode == "publish":
        # 即時公開（メール配信なし）
        ok = publish_draft(session, subdomain, draft_id, send_email=False)
        log.info(
            f"【最終サマリー】"
            f"公開={'成功' if ok else '失敗'}, "
            f"画像={'あり' if cover_image_url else 'なし'}, "
            f"タイトル={title}"
        )
        return ok
    else:
        # draft モード: 下書き保存のまま終了（Substack の draft は作成時点で保存済み）
        log.info("【STEP5】PUBLISH_MODE=draft → 下書き保存のまま終了")
        log.info(f"【STEP5】編集URL: https://{subdomain}.substack.com/publish/post/{draft_id}/edit")
        log.info(
            f"【最終サマリー】"
            f"下書き保存=成功, "
            f"画像={'あり' if cover_image_url else 'なし'}, "
            f"タイトル={title}"
        )
        return True


def run():
    """
    エントリーポイント。

    SUBSTACK_TEST_MODE=true の場合:
      - images/ から画像を自動選択
      - queue/ に記事があれば使用、なければダミー記事を使用
      - 下書き作成まで実行して終了（公開しない）

    通常モード:
      - queue/ の最古の JSON ファイルを 1 件処理
      - PUBLISH_MODE に応じて下書き保存 or 即時公開
      - 処理したファイルは queue/ に残す（Save ステップで posted/ に移動）
    """
    test_mode = os.environ.get("SUBSTACK_TEST_MODE", "").lower() in ("1", "true", "yes")

    if test_mode:
        # テスト用画像の取得
        test_image_path = os.environ.get("SUBSTACK_TEST_IMAGE_PATH", "").strip()
        if not test_image_path:
            candidates = sorted(glob.glob("images/*.jpg") + glob.glob("images/*.png"))
            if candidates:
                test_image_path = candidates[0]
                log.info(f"【テストモード】画像自動選択: {test_image_path}")
            else:
                log.warning("【テストモード】images/ に画像が見つからない → 画像なしでテスト")

        # テスト用記事の取得
        files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
        if files:
            with open(files[0], encoding="utf-8") as f:
                article = json.load(f)
            log.info(f"【テストモード】queue/ の記事を使用: {files[0]}")
        else:
            article = {
                "title": "Substackテスト投稿（API方式）",
                "body": (
                    "これはSubstack自動投稿のテストです。\n\n"
                    "## テスト項目\n\n"
                    "HTTPリクエスト方式でCloudflare検知を回避しています。\n\n"
                    "本文と画像が正しく投稿されているか確認します。\n\n"
                    "---\n\n"
                    "**太字テスト** と *斜体テスト* も含みます。"
                ),
                "image_path": test_image_path,
            }
            log.info("【テストモード】ダミー記事を使用")

        if test_image_path:
            article["image_path"] = test_image_path

        result = post_substack(article, test_mode=True)
        log.info(f"【テストモード】完了: {'成功' if result else '失敗'}")
        return

    # ── 通常モード ────────────────────────────────────────────
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return

    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    log.info(f"処理対象: {files[0]}")

    result = post_substack(article)
    log.info(f"Substack完了: {'成功' if result else '失敗'}（ファイルはqueueに残します）")


if __name__ == "__main__":
    run()
