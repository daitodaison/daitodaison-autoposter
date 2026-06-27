import json, os, asyncio, logging, glob
from pathlib import Path
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def md_to_text(text):
    lines = text.split('\n')
    result = []
    for line in lines:
        line = line.rstrip()
        if line.startswith('### '): line = '■■ ' + line[4:]
        elif line.startswith('## '): line = '■ ' + line[3:]
        elif line.startswith('# '): line = line[2:]
        elif line.startswith('---'): line = '─' * 20
        result.append(line)
    return '\n'.join(result)

def parse_cookies(cookies_json):
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
        if c.get("sameSite") in ["Strict", "Lax", "None"]:
            cookie["sameSite"] = c["sameSite"].capitalize()
        pw_cookies.append(cookie)
    return pw_cookies

async def post_ameblo(article):
    title = article.get("title", "無題")
    body = article.get("body", "")
    image_path = article.get("image_path", "")
    cookies_json = os.environ.get("AMEBLO_COOKIES", "")
    publish_mode = os.environ.get("PUBLISH_MODE", "draft")

    if not cookies_json:
        log.error("AMEBLO_COOKIES が設定されていません")
        return False

    pw_cookies = parse_cookies(cookies_json)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        await context.add_cookies(pw_cookies)
        page = await context.new_page()

        # STEP1: 投稿ページへアクセス
        log.info("【STEP1】アメブロ投稿ページへアクセス...")
        await page.goto("https://blog.ameba.jp/ucs/entry/srventryinsertinput.do", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        await page.screenshot(path="ameblo_01_initial.png")
        log.info(f"【STEP1】URL: {page.url}")

        # ログイン確認
        if "login" in page.url or "signin" in page.url:
            log.error("【STEP1】Cookieログイン失敗 - ログイン画面のまま")
            await browser.close()
            return False

        # STEP2: ページ要素確認
        await page.screenshot(path="ameblo_02_editor.png")
        inputs = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('input, textarea, [contenteditable]'))
            .map(el => ({tag: el.tagName, type: el.type||'', name: el.name||'', id: el.id||''}))
        """)
        log.info(f"【STEP2】入力要素: {inputs[:10]}")

        btns = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('button, input[type=submit]'))
            .map(el => ({text: el.textContent.trim().substring(0,20), value: el.value||'', id: el.id||''}))
        """)
        log.info(f"【STEP2】ボタン一覧: {btns[:10]}")

        frames = page.frames
        log.info(f"【STEP2】フレーム数: {len(frames)}")
        for i, frame in enumerate(frames):
            log.info(f"【STEP2】フレーム{i}: {frame.url[:80]}")

        # STEP3: タイトル入力
        log.info("【STEP3】タイトル入力...")
        title_entered = False
        for sel in ['input[name="title"]', 'input[id*="title"]', 'input[placeholder*="タイトル"]']:
            try:
                await page.fill(sel, title, timeout=5000)
                log.info(f"【STEP3】タイトルOK: {sel}")
                title_entered = True
                break
            except Exception:
                continue
        if not title_entered:
            log.warning("【STEP3】タイトル入力失敗")
        await page.screenshot(path="ameblo_03_title.png")

        # STEP4: 本文入力
        log.info("【STEP4】本文入力...")
        body_text = md_to_text(body)
        entered = False

        # メインページで試す
        for sel in ['#editor', '.editor-content', 'textarea[name="body"]']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.fill(body_text)
                    log.info(f"【STEP4】本文OK: {sel}")
                    entered = True
                    break
            except Exception:
                continue

        # iframeの中を試す
        if not entered:
            for i, frame in enumerate(frames):
                try:
                    for sel in ['[contenteditable="true"]', '#editor', 'textarea']:
                        try:
                            el = frame.locator(sel).first
                            if await el.is_visible(timeout=2000):
                                await el.click()
                                await el.fill(body_text)
                                log.info(f"【STEP4】iframe{i} {sel}で本文OK")
                                entered = True
                                break
                        except Exception:
                            continue
                    if entered:
                        break
                except Exception:
                    continue

        if not entered:
            log.warning("【STEP4】本文入力失敗")
        await page.screenshot(path="ameblo_04_body.png")

        # STEP5: 画像アップロード
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP5】画像アップロード: {image_path}")
            try:
                img_btns = await page.evaluate("""() =>
                    Array.from(document.querySelectorAll('button, input[type=file], label'))
                    .filter(el => el.textContent.includes('画像') || el.title?.includes('画像') || el.getAttribute('aria-label')?.includes('画像'))
                    .map(el => ({tag: el.tagName, text: el.textContent.trim().substring(0,20), id: el.id}))
                """)
                log.info(f"【STEP5】画像関連要素: {img_btns}")

                for sel in ['input[type="file"]', 'input[name*="image"]', 'input[accept*="image"]']:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0:
                            await el.set_input_files(image_path)
                            log.info(f"【STEP5】画像input直接: {sel}")
                            await page.wait_for_timeout(3000)
                            break
                    except Exception:
                        continue

                await page.screenshot(path="ameblo_05_image.png")
            except Exception as e:
                await page.screenshot(path="ameblo_05_image_error.png")
                log.warning(f"【STEP5】画像スキップ: {e}")
        else:
            log.info(f"【STEP5】画像スキップ（パス:{image_path}）")

        # STEP6: 投稿/下書き
        log.info(f"【STEP6】モード: {publish_mode}")
        await page.screenshot(path="ameblo_06_before_publish.png")

        if publish_mode == "publish":
            published = False
            for sel in ['button:has-text("公開")', 'input[value*="公開"]', 'button:has-text("投稿")', 'input[value*="投稿"]']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=5000):
                        await btn.click()
                        log.info(f"【STEP6】公開ボタンOK: {sel}")
                        published = True
                        break
                except Exception:
                    continue

            if not published:
                published = await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button, input[type=submit]'));
                    const b = btns.find(b =>
                        b.textContent.includes('公開') ||
                        b.value?.includes('公開') ||
                        b.textContent.includes('投稿') ||
                        b.value?.includes('投稿')
                    );
                    if (b) { b.click(); return true; }
                    return false;
                }""")
                log.info(f"【STEP6】JS公開: {published}")
        else:
            # 下書き保存
            saved = False
            for sel in ['button:has-text("下書き")', 'input[value*="下書き"]', 'button:has-text("保存")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=5000):
                        await btn.click()
                        log.info(f"【STEP6】下書きボタンOK: {sel}")
                        saved = True
                        break
                except Exception:
                    continue

            if not saved:
                saved = await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button, input[type=submit]'));
                    const b = btns.find(b =>
                        b.textContent.includes('下書き') ||
                        b.value?.includes('下書き')
                    );
                    if (b) { b.click(); return true; }
                    return false;
                }""")
                log.info(f"【STEP6】JS下書き: {saved}")

        await page.wait_for_timeout(5000)
        await page.screenshot(path="ameblo_06_after_publish.png")
        log.info(f"【STEP6】処理後URL: {page.url}")
        await browser.close()
        log.info(f"【完了】アメブロ処理完了: {title}")
        return True

def run():
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    asyncio.run(post_ameblo(article))
    log.info("アメブロ完了（ファイルはqueueに残します）")

if __name__ == "__main__":
    run()
