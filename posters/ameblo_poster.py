import json, os, asyncio, logging, shutil, glob
from pathlib import Path
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

async def post_ameblo(article):
    title = article.get("title", "無題")
    body = article.get("body", "")
    user = os.environ.get("AMEBLO_USER", "")
    pw = os.environ.get("AMEBLO_PASS", "")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto("https://blog.ameba.jp/ucs/entry/srventryinsertinput.do")
        await page.wait_for_timeout(3000)
        if "login" in page.url or "signin" in page.url:
            await page.fill('input[name="accountId"]', user)
            await page.fill('input[name="password"]', pw)
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(3000)
            await page.goto("https://blog.ameba.jp/ucs/entry/srventryinsertinput.do")
        await page.wait_for_timeout(3000)
        await page.fill('input[name="title"]', title)
        await page.evaluate(f"""() => {{
            const ed = document.querySelector('#editor, .editor, [contenteditable]');
            if (ed) ed.innerText = {json.dumps(body)};
        }}""")
        await page.wait_for_timeout(2000)
        await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button,input[type=submit]'));
            const b = btns.find(b => b.textContent.includes('公開') || b.value?.includes('公開'));
            if (b) b.click();
        }""")
        await page.wait_for_timeout(5000)
        await browser.close()
    log.info(f"アメブロ投稿完了: {title}")

def run():
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    asyncio.run(post_ameblo(article))

if __name__ == "__main__":
    run()