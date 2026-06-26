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
        await page.goto("https://note.com/login", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        # メールアドレス入力（複数セレクター試行）
        for sel in ['input[name="email"]', 'input[type="email"]', 'input[placeholder*="メール"]', 'input[placeholder*="mail"]']:
            try:
                await page.fill(sel, user, timeout=5000)
                log.info(f"メール入力成功: {sel}")
                break
            except:
                continue
        # パスワード入力
        for sel in ['input[name="password"]', 'input[type="password"]']:
            try:
                await page.fill(sel, pw, timeout=5000)
                log.info(f"パスワード入力成功: {sel}")
                break
            except:
                continue
        await page.wait_for_timeout(1000)
        # ログインボタン
        for sel in ['button[type="submit"]', 'button:has-text("ログイン")', 'button:has-text("Login")']:
            try:
                await page.click(sel, timeout=5000)
                log.info(f"ログインボタン押下: {sel}")
                break
            except:
                continue
        await page.wait_for_timeout(5000)
        log.info(f"ログイン後URL: {page.url}")
        await page.goto("https://note.com/notes/new")
        await page.wait_for_timeout(5000)
        # タイトル入力
        for sel in ['textarea[placeholder*="タイトル"]', 'input[placeholder*="タイトル"]', '.title-input']:
            try:
                await page.fill(sel, title, timeout=5000)
                break
            except:
                continue
        # 本文入力
        await page.evaluate(f"""() => {{
            const editor = document.querySelector('.ProseMirror');
            if (editor) {{
                editor.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, {json.dumps(body)});
            }}
        }}""")
        await page.wait_for_timeout(2000)
        for sel in ['button:has-text("公開に進む")', 'button:has-text("投稿する")']:
            try:
                await page.click(sel, timeout=5000)
                break
            except:
                continue
        await page.wait_for_timeout(5000)
        await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const btn = btns.find(b => ['公開する','投稿する'].some(k => b.textContent.includes(k)));
            if (btn) btn.click();
        }""")
        await page.wait_for_timeout(5000
