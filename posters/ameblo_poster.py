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

async def post_ameblo(article):
    title = article.get("title", "無題")
    body = article.get("body", "")
    image_path = article.get("image_path", "")
    user = os.environ.get("AMEBLO_USER", "")
    pw = os.environ.get("AMEBLO_PASS", "")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        page = await context.new_page()

        # STEP1: ログイン
        log.info("【STEP1】アメブロログインページへアクセス...")
        await page.goto("https://blog.ameba.jp/ucs/entry/srventryinsertinput.do", wait_until="domcontentloaded", timeout=60000)
        await page.screenshot(path="ameblo_01_initial.png")
        log.info(f"【STEP1】初期URL: {page.url}")

        if "login" in page.url or "signin" in page.url or "cyberagent" in page.url:
            log.info("【STEP1】ログイン画面を検出 → ログイン処理開始")
            await page.screenshot(path="ameblo_01_login_page.png")

            # ログインフォームのボタン一覧
            btns = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('button, input[type=submit], input[type=text], input[type=email], input[type=password]'))
                .map(el => ({tag: el.tagName, type: el.type, name: el.name, id: el.id, placeholder: el.placeholder}))
            """)
            log.info(f"【STEP1】フォーム要素: {btns[:10]}")

            # ID入力
            for sel in ['input[name="accountId"]', 'input[type="email"]', 'input[name="email"]', 'input[id*="account"]']:
                try:
                    await page.fill(sel, user, timeout=5000)
                    log.info(f"【STEP1】ID入力OK: {sel}")
                    break
                except Exception:
                    continue

            # パスワード入力
            for sel in ['input[name="password"]', 'input[type="password"]']:
                try:
                    await page.fill(sel, pw, timeout=5000)
                    log.info(f"【STEP1】パスワード入力OK: {sel}")
                    break
                except Exception:
                    continue

            await page.screenshot(path="ameblo_01_before_login.png")

            # ログインボタン
            for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("ログイン")', 'button:has-text("Login")']:
                try:
                    await page.click(sel, timeout=5000)
                    log.info(f"【STEP1】ログインボタンOK: {sel}")
                    break
                except Exception:
                    continue

            await page.wait_for_timeout(5000)
            await page.screenshot(path="ameblo_01_after_login.png")
            log.info(f"【STEP1】ログイン後URL: {page.url}")

            # 投稿ページへ
            await page.goto("https://blog.ameba.jp/ucs/entry/srventryinsertinput.do", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)

        await page.screenshot(path="ameblo_02_editor.png")
        log.info(f"【STEP2】エディタURL: {page.url}")

        # ページ上の要素確認
        inputs = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('input, textarea, [contenteditable]'))
            .map(el => ({tag: el.tagName, type: el.type||'', name: el.name||'', id: el.id||'', contenteditable: el.contentEditable}))
        """)
        log.info(f"【STEP2】入力要素: {inputs[:10]}")

        btns = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('button, input[type=submit]'))
            .map(el => ({text: el.textContent.trim().substring(0,20), value: el.value||'', id: el.id||''}))
        """)
        log.info(f"【STEP2】ボタン一覧: {btns[:10]}")

        # STEP3: タイトル入力
        log.info("【STEP3】タイトル入力...")
        for sel in ['input[name="title"]', 'input[id*="title"]', 'input[placeholder*="タイトル"]']:
            try:
                await page.fill(sel, title, timeout=5000)
                log.info(f"【STEP3】タイトルOK: {sel}")
                break
            except Exception:
                continue
        await page.screenshot(path="ameblo_03_title.png")

        # STEP4: 本文入力
        log.info("【STEP4】本文入力...")
        body_text = md_to_text(body)
        entered = False
        for sel in ['#editor', '.editor-content', '[contenteditable="true"]', 'textarea[name="body"]']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.click()
                    await el.fill(body_text)
                    log.info(f"【STEP4】本文入力OK: {sel}")
                    entered = True
                    break
            except Exception:
                continue

        if not entered:
            # iframeの中を確認
            frames = page.frames
            log.info(f"【STEP4】フレーム数: {len(frames)}")
            for i, frame in enumerate(frames):
                try:
                    el = frame.locator('[contenteditable="true"]').first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        await el.fill(body_text)
                        log.info(f"【STEP4】iframe{i}で本文入力OK")
                        entered = True
                        break
                except Exception:
                    continue

        await page.screenshot(path="ameblo_04_body.png")

        # STEP5: 画像アップロード
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP5】画像アップロード: {image_path}")
            try:
                # 画像挿入ボタンを探す
                for sel in ['button[title*="画像"]', 'button[aria-label*="画像"]', 'button:has-text("画像")']:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=3000):
                            await btn.click()
                            await page.wait_for_timeout(2000)
                            log.info(f"【STEP5】画像ボタンOK: {sel}")
                            break
                    except Exception:
                        continue

                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    await page.wait_for_timeout(500)
                file_chooser = await fc_info.value
                await file_chooser.set_files(image_path)
                await page.wait_for_timeout(5000)
                await page.screenshot(path="ameblo_05_image.png")
                log.info("【STEP5】画像アップロード完了")
            except Exception as e:
                await page.screenshot(path="ameblo_05_image_error.png")
                log.warning(f"【STEP5】画像スキップ: {e}")
        else:
            log.info(f"【STEP5】画像スキップ（パス:{image_path}）")

        # STEP6: 公開
        log.info("【STEP6】公開ボタンを押す...")
        await page.screenshot(path="ameblo_06_before_publish.png")
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
            log.info(f"【STEP6】JS公開クリック: {published}")

        await page.wait_for_timeout(5000)
        await page.screenshot(path="ameblo_06_after_publish.png")
        log.info(f"【STEP6】公開後URL: {page.url}")
        await browser.close()
        log.info(f"【完了】アメブロ処理完了: {title}")

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
