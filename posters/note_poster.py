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
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://note.com/login")
        await page.fill('input[name="email"]', user)
        await page.fill('input[name="password"]', pw)
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(3000)
        await page.goto("https://note.com/notes/new")
        await page.wait_for_timeout(5000)
        await page.fill('textarea[placeholder*="タイトル"]', title)
        await page.evaluate(f"""() => {{
            const editor = document.querySelector('.ProseMirror');
            if (editor) {{
                editor.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, {json.dumps(body)});
            }}
        }}""")
        await page.wait_for_timeout(2000)
        await page.click('button:has-text("公開に進む")')
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
    log.info("完了")

if __name__ == "__main__":
    run()