import json, os, asyncio, logging, shutil, glob
from pathlib import Path
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
POSTED_DIR = "posted"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

async def post_note(article):
    title = article.get("title", "無題")
    body = article.get("body", "")
    image_path = article.get("image_path", "")
    cookies_json = os.environ.get("NOTE_COOKIES", "")
    if not cookies_json:
        log.error("NOTE_COOKIES が設定されていません")
        return False
    cookies = json.loads(cookies_json)
    pw_cookies = []
    for c in cookies:
        cookie = {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c.get("path", "/")}
        if "expirationDate" in c:
            cookie["expires"] = int(c["expirationDate"])
        if "secure" in c:
            cookie["secure"] = c["secure"]
        if "httpOnly" in c:
            cookie["httpOnly"] = c["httpOnly"]
        if c.get("sameSite") in ["Strict", "Lax", "None"]:
            cookie["sameSite"] = c["sameSite"].capitalize()
        pw_cookies.append(cookie)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        await context.add_cookies(pw_cookies)
        page = await context.new_page()

        # STEP1: エディタへアクセス
        log.info("【STEP1】noteエディタへアクセス中...")
        await page.goto("https://note.com/notes/new", wait_until="domcontentloaded", timeout=60000)
        await page.screenshot(path="debug_01_after_goto.png")
        log.info(f"【STEP1】URL: {page.url}")

        # STEP2: エディタ起動待機
        log.info("【STEP2】エディタ起動を最大60秒待機...")
        for i in range(60):
            if "login" in page.url:
                await page.screenshot(path="debug_02_login_error.png")
                log.error("【STEP2】ログインが外れています")
                await browser.close()
                return False
            found = await page.evaluate("""() => {
                const el = document.querySelector('textarea[placeholder*="タイトル"]');
                return el && el.getBoundingClientRect().height > 0;
            }""")
            if found:
                log.info(f"【STEP2】エディタ起動完了（{i+1}秒）")
                break
            await asyncio.sleep(1)
        else:
            await page.screenshot(path="debug_02_timeout.png")
            log.error("【STEP2】エディタが起動しませんでした")
            await browser.close()
            return False

        await page.screenshot(path="debug_02_editor_ready.png")
        log.info("【STEP2】エディタ起動後のスクリーンショット保存")

        # STEP3: 画像アップロード
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP3】画像アップロード開始: {image_path}")
            try:
                # 画像追加ボタンを探す
                btns = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('button')).map(b => ({
                        text: b.textContent.trim(),
                        aria: b.getAttribute('aria-label'),
                        class: b.className.substring(0, 50)
                    }))
                }""")
                log.info(f"【STEP3】ページ上のボタン一覧: {btns[:10]}")

                await page.screenshot(path="debug_03_before_img_btn.png")

                # aria-labelで画像ボタンを探す
                img_btn = page.locator('button[aria-label="画像を追加"]').first
                is_visible = await img_btn.is_visible(timeout=5000)
                log.info(f"【STEP3】画像ボタン表示状態: {is_visible}")

                if is_visible:
                    await img_btn.click()
                    await page.wait_for_timeout(2000)
                    await page.screenshot(path="debug_03_after_img_btn_click.png")
                    log.info("【STEP3】画像ボタンクリック後のスクリーンショット保存")

                    # メニューの「画像をアップロード」をクリック
                    upload_text = page.locator('text=画像をアップロード').first
                    is_upload_visible = await upload_text.is_visible(timeout=5000)
                    log.info(f"【STEP3】「画像をアップロード」表示状態: {is_upload_visible}")

                    if is_upload_visible:
                        async with page.expect_file_chooser(timeout=10000) as fc_info:
                            await upload_text.click()
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(image_path)
                        await page.wait_for_timeout(5000)
                        await page.screenshot(path="debug_03_after_upload.png")
                        log.info("【STEP3】ファイル選択後のスクリーンショット保存")

                        # 保存ボタン
                        save_btn = page.locator('button:has-text("保存")').first
                        is_save_visible = await save_btn.is_visible(timeout=10000)
                        log.info(f"【STEP3】保存ボタン表示状態: {is_save_visible}")
                        if is_save_visible:
                            # モーダル内の保存ボタンをJSで直接クリック
                            await page.wait_for_timeout(2000)
                            clicked = await page.evaluate("""() => {
                                const modal = document.querySelector('.ReactModal__Overlay');
                                if (modal) {
                                    const btns = Array.from(modal.querySelectorAll('button'));
                                    const saveBtn = btns.find(b => b.textContent.trim() === '保存');
                                    if (saveBtn) { saveBtn.click(); return true; }
                                }
                                return false;
                            }""")
                            log.info(f"【STEP3】JS保存クリック結果: {clicked}")
                            await page.wait_for_timeout(5000)
                            await page.screenshot(path="debug_03_after_save.png")
                            log.info("【STEP3】画像保存後のスクリーンショット保存")
                    else:
                        log.warning("【STEP3】「画像をアップロード」メニューが見つかりません")
                else:
                    log.warning("【STEP3】画像追加ボタンが見つかりません")
            except Exception as e:
                await page.screenshot(path="debug_03_error.png")
                log.warning(f"【STEP3】画像アップロードエラー: {e}")
        else:
            log.info(f"【STEP3】画像スキップ（パス:{image_path} 存在:{os.path.exists(image_path) if image_path else False}）")

        # STEP4: タイトル入力
        log.info("【STEP4】タイトル入力...")
        title_area = page.locator('textarea[placeholder*="タイトル"]').first
        await title_area.fill(title)
        await page.screenshot(path="debug_04_title.png")
        log.info("【STEP4】タイトル入力OK")

        # STEP5: 本文入力
        log.info("【STEP5】本文入力...")
        body_html = body.replace('\n', '<br>')
        await page.evaluate("""(html) => {
            const editor = document.querySelector('.ProseMirror');
            if (editor) {
                editor.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('delete', false, null);
                document.execCommand('insertHTML', false, html);
            }
        }""", body_html)
        await asyncio.sleep(3)
        await page.screenshot(path="debug_05_body.png")
        log.info("【STEP5】本文入力OK")

        # STEP6: 下書き保存
        log.info("【STEP6】下書き保存...")
        try:
            draft_btn = page.locator('button:has-text("下書き保存")').first
            is_draft_visible = await draft_btn.is_visible(timeout=5000)
            log.info(f"【STEP6】下書き保存ボタン表示状態: {is_draft_visible}")
            if is_draft_visible:
                await draft_btn.click()
                await page.wait_for_timeout(3000)
                await page.screenshot(path="debug_06_draft_saved.png")
                log.info("【STEP6】下書き保存OK")
        except Exception as e:
            await page.screenshot(path="debug_06_error.png")
            log.warning(f"【STEP6】下書き保存エラー: {e}")

        await browser.close()
        log.info(f"【完了】note処理完了: {title}")
        return True

def run():
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    result = asyncio.run(post_note(article))
    if result:
        os.makedirs(POSTED_DIR, exist_ok=True)
        shutil.move(files[0], f"{POSTED_DIR}/{Path(files[0]).name}")

if __name__ == "__main__":
    run()
