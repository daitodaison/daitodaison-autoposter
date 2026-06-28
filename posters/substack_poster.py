import json, os, asyncio, logging, glob
from pathlib import Path
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# ⚠️ 【Substack仕様メモ 2026/06/28時点】
# - 投稿URL: https://substack.com/publish/post/new
# - タイトル: div[data-testid="post-title-input"]
# - 本文: div.ProseMirror または div[contenteditable="true"]
# - 下書き保存: 自動保存される（明示的な下書きボタンなし）
# - 公開: 「Publish」ボタン → 「Publish now」で確定
# - Cookie方式でログイン済みセッションを使用
# ============================================================

def md_to_substack(text):
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

async def post_substack(article, test_mode=False):
    title = article.get("title", "無題")
    body = article.get("body", "")
    image_path = article.get("image_path", "")
    cookies_json = os.environ.get("SUBSTACK_COOKIES", "")
    publish_mode = os.environ.get("PUBLISH_MODE", "draft")

    if test_mode:
        log.info("【テストモード】Substack投稿テストとして起動（公開ボタンは押さない）")

    if not cookies_json:
        log.error("SUBSTACK_COOKIES が設定されていません")
        return False

    pw_cookies = parse_cookies(cookies_json)
    body_text = md_to_substack(body)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"}
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        await context.add_cookies(pw_cookies)
        page = await context.new_page()

        # ── STEP1: 新規投稿ページへアクセス ──────────────────────────
        log.info("【STEP1】Substack新規投稿ページへアクセス...")
        await page.goto(
            "https://substack.com/publish/post/new",
            wait_until="domcontentloaded", timeout=60000
        )
        await page.wait_for_timeout(3000)
        await page.screenshot(path="substack_01_initial.png")
        log.info(f"【STEP1】URL: {page.url}")

        if "login" in page.url or "signin" in page.url or "sign-in" in page.url:
            log.error("【STEP1】Cookieログイン失敗")
            await browser.close()
            return False

        log.info("【STEP1】ログイン確認OK")

        # ── STEP2: タイトル入力 ───────────────────────────────────────
        log.info("【STEP2】タイトル入力...")
        title_selectors = [
            'div[data-testid="post-title-input"]',
            'textarea[placeholder*="Title"]',
            'input[placeholder*="Title"]',
            'div[contenteditable="true"][data-placeholder*="Title"]',
            'h1[contenteditable="true"]',
        ]
        title_ok = False
        for sel in title_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=5000):
                    await el.click()
                    await el.fill(title)
                    title_ok = True
                    log.info(f"【STEP2】タイトル入力OK: '{sel}'")
                    break
            except Exception:
                continue

        if not title_ok:
            log.warning("【STEP2】タイトル入力失敗 → JS経由フォールバック")
            try:
                await page.evaluate("""(text) => {
                    const els = document.querySelectorAll('[contenteditable="true"]');
                    if (els.length > 0) { els[0].focus(); els[0].innerText = text; }
                }""", title)
                title_ok = True
                log.info("【STEP2】JS経由タイトル入力OK")
            except Exception as e:
                log.error(f"【STEP2】タイトル入力完全失敗: {e}")

        await page.wait_for_timeout(1000)
        await page.screenshot(path="substack_02_title.png")
        log.info("【STEP2】★ substack_02_title.png 撮影")

        # ── STEP3: 本文入力 ───────────────────────────────────────────
        log.info("【STEP3】本文入力...")
        body_selectors = [
            'div.ProseMirror',
            'div[contenteditable="true"].ProseMirror',
            'div[data-testid="post-body-input"]',
            'div.editor-content[contenteditable="true"]',
        ]
        body_ok = False
        for sel in body_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=5000):
                    await el.click()
                    await page.wait_for_timeout(500)
                    await page.keyboard.type(body_text, delay=5)
                    body_ok = True
                    log.info(f"【STEP3】本文入力OK: '{sel}'")
                    break
            except Exception as e:
                log.warning(f"【STEP3】'{sel}' 失敗: {e}")
                continue

        if not body_ok:
            try:
                await page.evaluate("""(text) => {
                    const editors = document.querySelectorAll('[contenteditable="true"]');
                    for (const ed of editors) {
                        if (ed.classList.contains('ProseMirror') || ed.offsetHeight > 100) {
                            ed.focus();
                            document.execCommand('selectAll', false, null);
                            document.execCommand('insertText', false, text);
                            return true;
                        }
                    }
                    return false;
                }""", body_text)
                body_ok = True
                log.info("【STEP3】JS execCommand経由で本文入力OK")
            except Exception as e:
                log.error(f"【STEP3】本文入力完全失敗: {e}")

        await page.wait_for_timeout(2000)
        await page.screenshot(path="substack_03_body.png")
        log.info("【STEP3】★ substack_03_body.png 撮影")

        # ── STEP4: カバー画像アップロード ────────────────────────────
        image_set_ok = None
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP4】カバー画像アップロード開始: {image_path}")
            try:
                cover_selectors = [
                    'button:has-text("Add cover image")',
                    'button:has-text("カバー画像を追加")',
                    'button[aria-label="Add cover image"]',
                    'button.cover-image-button',
                    '[data-testid="cover-image-button"]',
                ]
                cover_clicked = False
                for sel in cover_selectors:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=3000):
                            async with page.expect_file_chooser(timeout=8000) as fc_info:
                                await btn.click()
                            file_chooser = await fc_info.value
                            await file_chooser.set_files(image_path)
                            cover_clicked = True
                            log.info(f"【STEP4】カバー画像ボタンクリック成功: '{sel}'")
                            break
                    except Exception as e:
                        log.warning(f"【STEP4】'{sel}' 失敗: {e}")

                if not cover_clicked:
                    file_inputs = page.locator('input[type="file"]')
                    count = await file_inputs.count()
                    log.info(f"【STEP4】フォールバック input[type=file] count={count}")
                    if count > 0:
                        await file_inputs.first.set_input_files(image_path)
                        cover_clicked = True
                        log.info("【STEP4】フォールバック: input[type=file]に直接セット")

                if cover_clicked:
                    # ① スピナー自動検知（消えた瞬間に即座に次へ）
                    spinner_selectors = [
                        '[class*="loading"]', '.spinner', '[class*="spinner"]',
                    ]
                    spinner_found = False
                    for sel in spinner_selectors:
                        try:
                            if await page.locator(sel).count() > 0:
                                spinner_found = True
                                log.info(f"【STEP4】スピナー検出: '{sel}' → 消えるまで待機")
                                await page.wait_for_selector(sel, state='hidden', timeout=20000)
                                log.info("【STEP4】スピナー消滅 → 完了を自動検知")
                                break
                        except Exception as e:
                            log.warning(f"【STEP4】スピナー待機例外: {e}")

                    # ② スピナーが見つからない場合は3秒固定待機（保険）
                    if not spinner_found:
                        log.info("【STEP4】スピナー未検出 → 固定3秒待機")
                        await page.wait_for_timeout(3000)

                    await page.screenshot(path="substack_04_after_image.png")
                    log.info("【STEP4】★ substack_04_after_image.png 撮影（画像アップロード確認）")
                    image_set_ok = True
                else:
                    log.error("【STEP4】カバー画像ボタンが見つからなかった")
                    await page.screenshot(path="substack_04_error.png")
                    image_set_ok = False

            except Exception as e:
                log.error(f"【STEP4-エラー】画像アップロード処理で例外: {e}")
                await page.screenshot(path="substack_04_error.png")
                image_set_ok = False
        else:
            log.info(f"【STEP4】画像スキップ（image_path='{image_path}'）")

        # ── STEP5: 公開前の最終確認スクショ ─────────────────────────
        await page.wait_for_timeout(1000)
        await page.screenshot(path="substack_05_before_publish.png")
        log.info("【STEP5】★ substack_05_before_publish.png 撮影（公開前・最終確認）")

        # ── STEP6: 公開処理 ──────────────────────────────────────────
        action_done = False
        if test_mode:
            log.info("【STEP6】テストモードのため公開ボタンは押しません")
            log.info("【STEP6】substack_05_before_publish.png で本文・画像を確認してください")
            action_done = True
        elif publish_mode == "publish":
            log.info("【STEP6】公開ボタンをクリック...")
            publish_selectors = [
                'button:has-text("Publish")',
                'button:has-text("公開")',
                'button[data-testid="publish-button"]',
            ]
            for sel in publish_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=5000):
                        await btn.click()
                        log.info(f"【STEP6】公開ボタンクリック: '{sel}'")
                        await page.wait_for_timeout(3000)
                        await page.screenshot(path="substack_06_publish_modal.png")
                        confirm_selectors = [
                            'button:has-text("Publish now")',
                            'button:has-text("今すぐ公開")',
                        ]
                        for csel in confirm_selectors:
                            try:
                                cbtn = page.locator(csel).first
                                if await cbtn.is_visible(timeout=5000):
                                    await cbtn.click()
                                    action_done = True
                                    log.info(f"【STEP6】公開確定クリック: '{csel}'")
                                    break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue

            await page.wait_for_timeout(5000)
            await page.screenshot(path="substack_06_after_publish.png")
            log.info(f"【STEP6】処理後URL: {page.url}")
        else:
            # draft: Substackは自動保存されるのでそのまま終了
            log.info("【STEP6】PUBLISH_MODE=draft → 自動保存のまま終了")
            action_done = True

        await browser.close()
        log.info(
            f"【最終サマリー】"
            f"タイトル={title_ok}, 本文={body_ok}, 画像={image_set_ok}, 公開={action_done}"
        )
        if action_done:
            log.info(f"【完了】Substack処理完了: {title}")
        else:
            log.error(f"【失敗】Substack処理失敗: {title}")
        return action_done

def run():
    test_mode = os.environ.get("SUBSTACK_TEST_MODE", "").lower() in ("1", "true", "yes")

    if test_mode:
        test_image_path = os.environ.get("SUBSTACK_TEST_IMAGE_PATH", "").strip()
        if not test_image_path:
            candidates = sorted(glob.glob("images/*.jpg") + glob.glob("images/*.png"))
            if candidates:
                test_image_path = candidates[0]
                log.info(f"【テストモード】画像自動選択: {test_image_path}")
            else:
                log.warning("【テストモード】images/に画像が見つからない → 画像なしでテスト")

        files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
        if files:
            with open(files[0], encoding="utf-8") as f:
                article = json.load(f)
            log.info(f"【テストモード】queue/の記事を使用: {files[0]}")
        else:
            article = {
                "title": "Substackテスト投稿",
                "body": "これはSubstack自動投稿のテストです。\n\n## テスト項目\n\n本文と画像が正しく表示されているか確認します。",
                "image_path": test_image_path,
            }
            log.info("【テストモード】ダミー記事を使用")

        if test_image_path:
            article["image_path"] = test_image_path

        asyncio.run(post_substack(article, test_mode=True))
        log.info("【テストモード】完了")
        return

    # 通常モード
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    asyncio.run(post_substack(article))
    log.info("Substack完了（ファイルはqueueに残します）")

if __name__ == "__main__":
    run()
