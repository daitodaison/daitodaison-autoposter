import json, os, asyncio, logging, glob
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

async def post_linkedin(article):
    title = article.get("title", "")
    body = article.get("body", "")
    text = f"{title}\n\n{body}"
    user = os.environ.get("LINKEDIN_USER", "")
    pw = os.environ.get("LINKEDIN_PASS", "")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto("https://www.linkedin.com/login")
        await page.fill('input[name="session_key"]', user)
        await page.fill('input[name="session_password"]', pw)
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(4000)
        await page.goto("https://www.linkedin.com/feed/")
        await page.wait_for_timeout(3000)
        await page.click('button:has-text("投稿を始める"), button:has-text("Start a post")')
        await page.wait_for_timeout(2000)
        await page.keyboard.type(text)
        await page.wait_for_timeout(2000)
        await page.click('button:has-text("投稿する"), button:has-text("Post")')
        await page.wait_for_timeout(5000)
        await browser.close()
    log.info(f"LinkedIn投稿完了: {title}")

def run():
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    asyncio.run(post_linkedin(article))

if __name__ == "__main__":
    run()