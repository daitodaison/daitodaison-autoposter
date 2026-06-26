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
    user = os.environ.get("NOTE_USER", "")
    pw = os.environ.get("NOTE_PASS", "")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto("https://note.com/login", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        for sel in ['input[type="email"]', 'input[name="email"]', 'input[placeholder*="mail"]']:
            try:
                await page.fill(sel, user, timeout=5000)
                log.info(f"メール入力OK: {sel}")
                break
            except Exception:
                continue
        for sel in ['input[type="password"]', 'input[name="password"]']:
            try:
                await page.fill(sel, pw, timeout=5000)
                log.info(f"パスワード入力OK: {sel}")
                break
            except Exception:
                continue
        await page.wait_for_timeout(1000)
        for sel in ['button[type="submit"]', 'button:has-text("ログイン")']:
            try:
                await page.click(sel, timeout=5000)
                log.info(f"ログインOK: {sel}")
                break
            except Exception:
                continue
        await page.wait_for_timeout(5000)
        log.info(f"ログイン後URL: {page.url}")
        await page.goto("https://note.com/notes/new")
        await page.wait_for_timeout(5000)
        for sel in ['textarea[placeholder*="タイトル"]', 'input[placeholder*="タイトル"]']:
            try:
                await page.fill(sel, title, timeout=5000)
                break
            except Exception:
                continue
        body_js = json.dumps(body)
        await page.evaluate(f"""() => {{
            const editor = document.querySelector('.ProseMirror');
            if (editor) {{
                editor.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, {body_js});
            }}
        }}""")
        await page.wait_for_timeout(2000)
        for sel in ['button:has-text("公開に進む")', 'button:has-text("投稿する")']:
            try:
                await page.click(sel, timeout=5000)
                break
            except Exception:
                continue
        await page.wait_for_timeout(5000)
        await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const btn = btns.find(b => ['公開する','投稿する'].some(k => b.textContent.includes(k)));
            if (btn) btn.click();
        }""")
        await page.wait_for_timeout(5000)
        await browser.close()
    log.info(f"note投稿完了: {title}")

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
