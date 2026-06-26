import json, os, asyncio, logging, glob
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

async def post_substack(article):
    title = article.get("title", "")
    body = article.get("body", "")
    user = os.environ.get("SUBSTACK_USER", "")
    pw = os.environ.get("SUBSTACK_PASS", "")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto("https://substack.com/sign-in")
        await page.wait_for_timeout(2000)
        await page.fill('input[name="email"]', user)
        await page.click('button:has-text("Continue"), button:has-text("次へ")')
        await page.wait_for_timeout(2000)
        await page.fill('input[name="password"]', pw)
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(4000)
        await page.goto("https://substack.com/publish/post/new")
        await page.wait_for_timeout(4000)
        await page.fill('div[data-testid="post-title-input"], input[placeholder*="Title"]', title)
        await page.wait_for_timeout(1000)
        await page.click('div.editor-content, div[contenteditable="true"]')
        await page.keyboard.type(body)
        await page.wait_for_timeout(2000)
        await page.click('button:has-text("Publish"), button:has-text("公開")')
        await page.wait_for_timeout(3000)
        await page.click('button:has-text("Publish now"), button:has-text("今すぐ公開")')
        await page.wait_for_timeout(5000)
        await browser.close()
    log.info(f"Substack投稿完了: {title}")

def run():
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    asyncio.run(post_substack(article))

if __name__ == "__main__":
    run()