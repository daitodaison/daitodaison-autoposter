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
    cookies_json = os.environ.get("NOTE_COOKIES", "")
    if not cookies_json:
        log.error("NOTE_COOKIES が設定されていません")
        return
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
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(pw_cookies)
        page = await context.new_page()
        await page.goto("https://note.com/", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        log.info(f"アクセス後URL: {page.url}")
        await page.goto("https://note.com/notes/new")
        await page.wait_for_timeout(5000)
        log.info(f"投稿ページURL: {page.url}")
        for sel in ['textarea[placeholder*="タイトル"]', 'input[placeholder*="タイトル"]']:
            try:
                await page.fill(sel, title, timeout=5000)
                log.info("タイトル入力OK")
                break
            except Exception:
                continue
        body_escaped = json.dumps(body)
        js = "() => { const e = document.querySelector('.ProseMirror'); if(e){ e.focus(); document.execCommand('selectAll',false,null); document.execCommand('insertText',false," + body_escaped + "); } }"
        await page.evaluate(js)
        await page.wait_for_timeout(3000)
        # 下書き保存
        for sel in ['button:has-text("下書き保存")', 'button:has-text("保存")']:
            try:
                await page.click(sel, timeout=5000)
                log.info("下書き保存OK")
                break
            except Exception:
                continue
        await page.wait_for_timeout(3000)
        # ページ上のボタン一覧をログ出力
        buttons = await page.evaluate("() => Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim()).filter(t => t.length > 0)")
        log.info(f"ページ上のボタン: {buttons}")
        await browser.close()
    log.info(f"note下書き保存完了: {title}")

def run():
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    asyncio.run(post_note(article))
    os.makedirs(POSTED_DIR, exist_ok=True)
    shutil.move(files[0], f"{POSTED_DIR}/{Path(files[0]).name}")

if __name__ == "__main__":
    run()
