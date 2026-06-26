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
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
        }
        if "expirationDate" in c:
            cookie["expires"] = int(c["expirationDate"])
        if "secure" in c:
            cookie["secure"] = c["secure"]
        if "httpOnly" in c:
            cookie["httpOnly"] = c["httpOnly"]
        if "sameSite" in c and c["sameSite"] in ["Strict", "Lax", "None"]:
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
        body_js = json.dumps(body)
        await page.evaluate(f"""() => {{
            const editor = document.querySelector('.ProseMirror');
            if (editor) {{
                editor.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText',
