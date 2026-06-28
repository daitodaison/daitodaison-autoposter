import json, os, asyncio, logging, glob
from pathlib import Path
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# ⚠️ 【LinkedIn仕様メモ 2026/06/28時点】
# - 投稿フォームURL: https://www.linkedin.com/feed/
# - 「投稿を作成」ボタンをクリックしてモーダルを開く
# - テキストエリアにfocus → 本文を入力
# - 画像はクリップのアイコンボタンからアップロード
# - 「投稿する」ボタンで公開、下書き機能はない
# - Cookie方式: www.linkedin.com のCookieをJSONで設定
# ============================================================

def md_to_linkedin(text):
    """
    Markdown的なテキストをLinkedIn投稿用プレーンテキストに変換する。
    LinkedInはHTMLタグを受け付けないため、プレーンテキストで整形する。
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        line = line.rstrip()
        if line.startswith('### '): line = '■■ ' + line[4:]
        elif line.startswith('## '): line = '■ ' + line[3:]
        elif line.startswith('# '): line = line[2:]
        elif line.startswith('---'): line = '─' * 20
        result.append(line)
    return '\n'.join(result)

def parse_cookies(cookies_json):
    cookies = json.loads(cookies_json)
    pw_cookies = []
    for c in cookies:
        cookie = {
            "name": c["name"], "value": c["value"],
            "domain": c["domain"], "path": c.get("path", "/"),
        }
        if "expirationDate" in c: cookie["expires"] = int(c["expirationDate"])
        if "secure" in c: cookie["secure"] = c["secure"]
        if "httpOnly" in c: cookie["httpOnly"] = c["httpOnly"]
        if c.get("sameSite") in ["Strict", "Lax", "None"]:
            cookie["sameSite"] = c["sameSite"].capitalize()
        pw_cookies.append(cookie)
    return pw_cookies

async def post_linkedin(article, test_mode=False):
    """
    LinkedIn投稿のメイン処理。

    test_mode=True の場合:
      - 画像と本文テキストの両方を確認するテスト
      - 実際に投稿せず、投稿ボタンを押す直前のスクショで確認する
      - 環境変数 LINKEDIN_TEST_MODE=true で有効化
    """
    title = article.get("title", "無題")
    body = article.get("body", "")
    image_path = article.get("image_path", "")
    cookies_json = os.environ.get("LINKEDIN_COOKIES", "")
    publish_mode = os.environ.get("PUBLISH_MODE", "draft")

    if test_mode:
        log.info("【テストモード】LinkedIn投稿テストとして起動（投稿ボタンは押さない）")

    if not cookies_json:
        log.error("LINKEDIN_COOKIES が設定されていません")
        return False

    pw_cookies = parse_cookies(cookies_json)
    post_text = f"{title}\n\n{md_to_linkedin(body)}"
    # LinkedInの投稿文字数上限は3000文字
    post_text = post_text[:3000]
    log.info(f"【準備】投稿テキスト文字数: {len(post_text)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-gpu"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        await context.add_cookies(pw_cookies)
        page = await context.new_page()

        # STEP1: フィードページへアクセス
        log.info("【STEP1】LinkedInフィードページへアクセス...")
        await page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="networkidle", timeout=60000
        )
        await page.screenshot(path="linkedin_01_initial.png")
        log.info(f"【STEP1】URL: {page.url}")

        if "login" in page.url or "authwall" in page.url or "checkpoint" in page.url:
            log.error("【STEP1】Cookieログイン失敗")
            await browser.close()
            return False

        # STEP2: 「投稿を作成」ボタンをクリックしてモーダルを開く
        log.info("【STEP2】'投稿を作成'ボタンを探してクリック...")
        post_btn_selectors = [
            'button:has-text("投稿を作成")',
            'button:has-text("Start a post")',
            '[data-control-name="share.sharebox_open"]',
            '.share-box-feed-entry__trigger',
            'button.artdeco-button--muted',
        ]
        post_btn_clicked = False
        for sel in post_btn_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    post_btn_clicked = True
                    log.info(f"【STEP2】クリック成功: '{sel}'")
                    break
            except Exception:
                continue

        if not post_btn_clicked:
            # フォールバック: テキストで広く探す
            try:
                await page.click('text=投稿を作成', timeout=5000)
                post_btn_clicked = True
                log.info("【STEP2】フォールバック: 'text=投稿を作成' でクリック成功")
            except Exception:
                try:
                    await page.click('text=Start a post', timeout=5000)
                    post_btn_clicked = True
                    log.info("【STEP2】フォールバック: 'text=Start a post' でクリック成功")
                except Exception as e:
                    log.error(f"【STEP2】投稿ボタンが見つからない: {e}")

        await page.wait_for_timeout(2000)
        await page.screenshot(path="linkedin_02_modal.png")

        # STEP3: 本文テキストを入力
        log.info("【STEP3】本文入力...")
        editor_selectors = [
            '.ql-editor',
            '[data-placeholder="投稿内容を入力してください"]',
            '[data-placeholder="What do you want to talk about?"]',
            '.editor-content',
            'div[contenteditable="true"]',
        ]
        editor_found = False
        for sel in editor_selectors:
            try:
                editor = page.locator(sel).first
                if await editor.is_visible(timeout=3000):
                    await editor.click()
                    await page.wait_for_timeout(500)
                    # テキストを直接入力
                    await editor.fill(post_text)
                    editor_found = True
                    log.info(f"【STEP3】エディタ入力成功: '{sel}'")
                    break
            except Exception:
                continue

        if not editor_found:
            # フォールバック: evaluate でDOM直接操作
            try:
                await page.evaluate("""(text) => {
                    const editors = document.querySelectorAll('[contenteditable="true"]');
                    for (const ed of editors) {
                        if (ed.offsetHeight > 0) {
                            ed.focus();
                            document.execCommand('selectAll', false, null);
                            document.execCommand('insertText', false, text);
                            return true;
                        }
                    }
                    return false;
                }""", post_text)
                editor_found = True
                log.info("【STEP3】フォールバック: JS経由で本文入力")
            except Exception as e:
                log.error(f"【STEP3】本文入力失敗: {e}")

        await page.wait_for_timeout(1000)
        await page.screenshot(path="linkedin_03_body.png")
        log.info("【STEP3】★ linkedin_03_body.png 撮影（本文入力確認用）")

        # STEP4: 画像アップロード
        image_set_ok = None
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP4】画像アップロード開始: {image_path}")
            try:
                # 画像アップロードボタンを探す
                img_btn_selectors = [
                    'button[aria-label="画像を追加"]',
                    'button[aria-label="Add a photo"]',
                    'button[aria-label="メディアを追加"]',
                    'button[aria-label="Add media"]',
                    '[data-control-name="share.add_image"]',
                    'button.share-creation-state__footer-action--image',
                ]
                img_btn_clicked = False
                for sel in img_btn_selectors:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=3000):
                            async with page.expect_file_chooser(timeout=8000) as fc_info:
                                await btn.click()
                            file_chooser = await fc_info.value
                            await file_chooser.set_files(image_path)
                            img_btn_clicked = True
                            log.info(f"【STEP4】画像ボタンクリック成功: '{sel}'")
                            break
                    except Exception as e:
                        log.warning(f"【STEP4】'{sel}' 失敗: {e}")
                        continue

                if not img_btn_clicked:
                    # フォールバック: input[type=file]に直接セット
                    file_inputs = page.locator('input[type="file"]')
                    count = await file_inputs.count()
                    log.info(f"【STEP4】input[type=file] count={count}")
                    if count > 0:
                        await file_inputs.first.set_input_files(image_path)
                        img_btn_clicked = True
                        log.info("【STEP4】フォールバック: input[type=file]に直接セット")

                if img_btn_clicked:
                    # 画像がアップロードされるまで待つ（スピナー消滅 or 固定待機）
                    spinner_selectors = [
                        '.share-creation-state__loading',
                        '[class*="loading"]',
                        '.artdeco-spinner',
                    ]
                    spinner_found = False
                    for sel in spinner_selectors:
                        try:
                            if await page.locator(sel).count() > 0:
                                spinner_found = True
                                log.info(f"【STEP4】スピナー検出: '{sel}' → 消えるまで待機")
                                await page.wait_for_selector(sel, state='hidden', timeout=20000)
                                log.info("【STEP4】スピナー消滅 → 画像アップロード完了を自動検知")
                                break
                        except Exception as e:
                            log.warning(f"【STEP4】スピナー待機例外 ({sel}): {e}")

                    if not spinner_found:
                        log.info("【STEP4】スピナー未検出 → 固定3秒待機")
                        await page.wait_for_timeout(3000)

                    await page.screenshot(path="linkedin_04_after_image.png")
                    log.info("【STEP4】★ linkedin_04_after_image.png 撮影（画像アップロード確認用）")

                    # 画像がDOMに反映されているか確認
                    img_check = await page.evaluate("""() => {
                        const imgs = document.querySelectorAll(
                            '.share-creation-state img, [class*="share"] img, .feed-shared-image img'
                        );
                        return {
                            count: imgs.length,
                            src: imgs.length > 0 ? imgs[0].src : null
                        };
                    }""")
                    log.info(f"【STEP4】画像DOM確認: {img_check}")
                    image_set_ok = img_check.get("count", 0) > 0
                else:
                    log.error("【STEP4】画像ボタンが見つからなかった")
                    image_set_ok = False

            except Exception as e:
                log.error(f"【STEP4-エラー】画像アップロード処理で例外: {e}")
                await page.screenshot(path="linkedin_04_error.png")
                image_set_ok = False
        else:
            log.info(f"【STEP4】画像スキップ（image_path={image_path}）")

        # STEP5: 投稿ボタンを押す直前のスクショ（テストモードの確認用）
        await page.wait_for_timeout(1000)
        await page.screenshot(path="linkedin_05_before_post.png")
        log.info("【STEP5】★ linkedin_05_before_post.png 撮影（投稿前・最終確認用）")

        # STEP6: 投稿 or テストモードはここで終了
        action_done = False
        if test_mode:
            log.info("【STEP6】テストモードのため投稿ボタンは押しません")
            log.info("【STEP6】linkedin_05_before_post.png で本文・画像を確認してください")
            action_done = True  # テストとしては成功扱い
        elif publish_mode == "publish":
            log.info("【STEP6】投稿ボタンをクリック...")
            post_submit_selectors = [
                'button:has-text("投稿する")',
                'button:has-text("Post")',
                'button[aria-label="投稿する"]',
            ]
            for sel in post_submit_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=3000):
                        await btn.click()
                        action_done = True
                        log.info(f"【STEP6】投稿クリック成功: '{sel}'")
                        break
                except Exception:
                    continue

            await page.wait_for_timeout(5000)
            await page.screenshot(path="linkedin_06_after_post.png")
            log.info(f"【STEP6】処理後URL: {page.url}")
        else:
            log.info("【STEP6】PUBLISH_MODE=draft のため投稿スキップ（LinkedIn下書き機能なし）")
            log.info("【STEP6】linkedin_05_before_post.png で内容確認後、publish_mode=publish で本番実行してください")
            action_done = True

        await browser.close()
        log.info(
            f"【最終サマリー】"
            f"本文入力={editor_found}, 画像セット={image_set_ok}, 投稿操作={action_done}"
        )
        if action_done:
            log.info(f"【完了】LinkedIn処理完了: {title}")
        else:
            log.error(f"【失敗】LinkedIn処理失敗: {title}")
        return action_done

def run():
    test_mode = os.environ.get("LINKEDIN_TEST_MODE", "").lower() in ("1", "true", "yes")

    if test_mode:
        # テストモード: 画像と本文を確認、投稿ボタンは押さない
        test_image_path = os.environ.get("LINKEDIN_TEST_IMAGE_PATH", "").strip()
        if not test_image_path:
            candidates = sorted(glob.glob("images/*.jpg") + glob.glob("images/*.png"))
            if candidates:
                test_image_path = candidates[0]
                log.info(f"【テストモード】画像自動選択: {test_image_path}")
            else:
                log.warning("【テストモード】images/に画像が見つからない → 画像なしでテスト")

        # queue/の記事を使う、なければダミー
        files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
        if files:
            with open(files[0], encoding="utf-8") as f:
                article = json.load(f)
            log.info(f"【テストモード】queue/の記事を使用: {files[0]}")
        else:
            article = {
                "title": "LinkedInテスト投稿",
                "body": "これはLinkedIn自動投稿のテストです。\n\n## テスト項目\n\n本文と画像が正しく表示されているか確認します。",
                "image_path": test_image_path,
            }
            log.info("【テストモード】ダミー記事を使用")

        if test_image_path:
            article["image_path"] = test_image_path

        asyncio.run(post_linkedin(article, test_mode=True))
        log.info("【テストモード】完了")
        return

    # 通常モード
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    asyncio.run(post_linkedin(article))
    log.info("LinkedIn完了（ファイルはqueueに残します）")

if __name__ == "__main__":
    run()
