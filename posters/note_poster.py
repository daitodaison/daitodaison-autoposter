import json, os, asyncio, logging, shutil, glob, re
from pathlib import Path
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
POSTED_DIR = "posted"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def md_to_html(text):
    lines = text.split('\n')
    html_lines = []
    for line in lines:
        line = line.rstrip()
        if line.startswith('## '):
            html_lines.append(f'<h2>{line[3:]}</h2>')
        elif line.startswith('# '):
            html_lines.append(f'<h1>{line[2:]}</h1>')
        elif line.strip() == '':
            html_lines.append('<p><br></p>')
        else:
            html_lines.append(f'<p>{line}</p>')
    return '\n'.join(html_lines)

async def post_note(article):
    title = article.get("title", "無題")
    body = article.get("body", "")
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

        # タイトル入力欄が出るまで最大60秒待機
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

        # タイトル入力
        title_area = page.locator('textarea[placeholder*="タイトル"]').first
        await title_area.fill(title)
        log.info("タイトル入力OK")

        # 本文入力
        body_html = md_to_html(body)
        await page.evaluate("""(html) => {
            const editor = document.querySelector('.ProseMirror');
            if (editor) {
                editor.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('delete', false, null);
                document.execCommand('insertHTML', false, html);
            }
        }""", body_html)
        await asyncio.sleep(5)
        log.info("本文入力OK")

        # 公開に進む
        try:
            await page.locator('button:has-text("公開に進む")').first.click(timeout=10000)
            await asyncio.sleep(8)
            log.info("公開設定画面へ移動OK")
        except Exception as e:
            log.error(f"公開ボタンエラー: {e}")
            await browser.close()
            return False

        # 公開する
        published = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const btn = btns.find(b => ['公開する','投稿する','保存して公開'].some(kw => b.textContent.includes(kw)));
            if (btn) { btn.click(); return true; }
            return false;
        }""")

        await asyncio.sleep(10)
        if published:
            log.info(f"note投稿完了: {title}")
        else:
            log.error("公開ボタンが見つかりませんでした")
        await browser.close()
        return published

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
