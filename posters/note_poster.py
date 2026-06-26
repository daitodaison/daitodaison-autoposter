import json, os, asyncio, logging, shutil, glob, re
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

        log.info("noteエディタへアクセス中...")
        await page.goto("https://note.com/notes/new", wait_until="domcontentloaded", timeout=60000)

        log.info("エディタ起動を最大60秒待機...")
        for i in range(60):
            if "login" in page.url:
                log.error("ログインが外れています")
                await browser.close()
                return False
            found = await page.evaluate("""() => {
                const el = document.querySelector('textarea[placeholder*="タイトル"]');
                return el && el.getBoundingClientRect().height > 0;
            }""")
            if found:
                log.info(f"エディタ起動完了（{i+1}秒）")
                break
            await asyncio.sleep(1)
        else:
            log.error("エディタが起動しませんでした")
            await browser.close()
            return False

 # 画像アップロード
        if image_path and os.path.exists(image_path):
            try:
                log.info(f"画像アップロード開始: {image_path}")
                img_icon = page.locator('button[aria-label="画像を追加"]').first
                await img_icon.click(timeout=10000)
                await page.wait_for_timeout(2000)
                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    await page.click('text=画像をアップロード')
                file_chooser = await fc_info.value
                await file_chooser.set_files(image_path)
                await page.wait_for_timeout(5000)
                await page.click('button:has-text("保存")', timeout=10000)
                await page.wait_for_timeout(3000)
                log.info("画像アップロード完了")
            except Exception as e:
                log.warning(f"画像アップロードスキップ: {e}")

        # タイトル入力
        title_area = page.locator('textarea[placeholder*="タイトル"]').first
        await title_area.fill(title)
        log.info("タイトル入力OK")

        # 本文入力
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
        log.info("本文入力OK")

        # 下書き保存
        try:
            await page.click('button:has-text("下書き保存")', timeout=10000)
            await page.wait_for_timeout(3000)
            log.info("下書き保存OK")
        except Exception as e:
            log.warning(f"下書き保存ボタンエラー: {e}")

        await browser.close()
        log.info(f"note処理完了: {title}")
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
