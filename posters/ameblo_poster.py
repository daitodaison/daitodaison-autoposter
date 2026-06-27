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

        if "login" in page.url or "signin" in page.url:
            log.error("【STEP1】Cookieログイン失敗")
            await browser.close()
            return False

        # STEP2: ページ全体の詳細確認
        log.info("【STEP2】ページ詳細確認...")
        await page.screenshot(path="ameblo_02_editor.png")

        # 全ボタン一覧
        all_btns = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'))
            .map(el => ({
                tag: el.tagName,
                id: el.id||'',
                text: el.textContent.trim().substring(0,30),
                value: el.value||'',
                class: el.className.substring(0,40)
            }))
        """)
        log.info(f"【STEP2】全ボタン({len(all_btns)}個): {all_btns}")

        # 全入力要素
        all_inputs = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('input, textarea'))
            .map(el => ({
                tag: el.tagName,
                type: el.type||'',
                name: el.name||'',
                id: el.id||'',
                placeholder: el.placeholder||''
            }))
        """)
        log.info(f"【STEP2】全入力要素({len(all_inputs)}個): {all_inputs}")

        # フレーム確認
        frames = page.frames
        log.info(f"【STEP2】フレーム数: {len(frames)}")
        for i, frame in enumerate(frames):
            try:
                frame_btns = await frame.evaluate("""() =>
                    Array.from(document.querySelectorAll('button, input[type=submit]'))
                    .map(el => ({text: el.textContent.trim().substring(0,20), id: el.id||''}))
                """)
                frame_inputs = await frame.evaluate("""() =>
                    Array.from(document.querySelectorAll('input, textarea, [contenteditable]'))
                    .map(el => ({tag: el.tagName, type: el.type||'', name: el.name||'', id: el.id||'', ce: el.contentEditable}))
                """)
                log.info(f"【STEP2】フレーム{i}({frame.url[:60]}): ボタン{frame_btns} 入力{frame_inputs[:5]}")
            except Exception as e:
                log.info(f"【STEP2】フレーム{i}({frame.url[:60]}): アクセス不可 {e}")

        # STEP3: タイトル入力
        log.info("【STEP3】タイトル入力...")
        title_entered = False

        # メインページで試す
        for sel in ['input[name="title"]', 'input[id*="title"]', 'input[placeholder*="タイトル"]', '#entry-title']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.fill(title)
                    log.info(f"【STEP3】タイトルOK(メイン): {sel}")
                    title_entered = True
                    break
            except Exception as e:
                log.info(f"【STEP3】{sel} 失敗: {e}")

        # iframeで試す
        if not title_entered:
            for i, frame in enumerate(frames):
                for sel in ['input[name="title"]', '#entry-title', 'input[placeholder*="タイトル"]']:
                    try:
                        el = frame.locator(sel).first
                        if await el.is_visible(timeout=2000):
                            await el.fill(title)
                            log.info(f"【STEP3】タイトルOK(frame{i}): {sel}")
                            title_entered = True
                            break
                    except Exception:
                        continue
                if title_entered:
                    break

        log.info(f"【STEP3】タイトル入力結果: {title_entered}")
        await page.screenshot(path="ameblo_03_title.png")

        # STEP4: 本文入力
        log.info("【STEP4】本文入力...")
        body_text = md_to_text(body)
        entered = False

        # メインページのcontenteditable
        for sel in ['[contenteditable="true"]', '#editor', '.editor-content', 'textarea[name="body"]']:
            try:
                el = page.locator(sel).first
                count = await el.count()
                log.info(f"【STEP4】{sel}: count={count}")
                if count > 0 and await el.is_visible(timeout=3000):
                    await el.click()
                    await page.wait_for_timeout(500)
                    await el.fill(body_text)
                    log.info(f"【STEP4】本文OK(メイン): {sel}")
                    entered = True
                    break
            except Exception as e:
                log.info(f"【STEP4】{sel} 失敗: {e}")

        # iframeで試す
        if not entered:
            for i, frame in enumerate(frames):
                for sel in ['[contenteditable="true"]', '#editor', 'textarea']:
                    try:
                        el = frame.locator(sel).first
                        count = await el.count()
                        if count > 0 and await el.is_visible(timeout=2000):
                            await el.click()
                            await page.wait_for_timeout(500)
                            await el.fill(body_text)
                            log.info(f"【STEP4】本文OK(frame{i}): {sel}")
                            entered = True
                            break
                    except Exception:
                        continue
                if entered:
                    break

        log.info(f"【STEP4】本文入力結果: {entered}")
        await page.screenshot(path="ameblo_04_body.png")

        # STEP5: 画像アップロード
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP5】画像アップロード: {image_path}")
            try:
                # file inputを直接探す
                file_inputs = await page.evaluate("""() =>
                    Array.from(document.querySelectorAll('input[type="file"]'))
                    .map(el => ({id: el.id, name: el.name, accept: el.accept, class: el.className.substring(0,30)}))
                """)
                log.info(f"【STEP5】fileInput一覧: {file_inputs}")

                # 画像関連ボタン
                img_btns = await page.evaluate("""() =>
                    Array.from(document.querySelectorAll('button, label, a'))
                    .filter(el => {
                        const t = el.textContent + (el.title||'') + (el.getAttribute('aria-label')||'') + (el.id||'');
                        return t.includes('画像') || t.includes('image') || t.includes('photo');
                    })
                    .map(el => ({tag: el.tagName, text: el.textContent.trim().substring(0,20), id: el.id, class: el.className.substring(0,30)}))
                """)
                log.info(f"【STEP5】画像関連要素: {img_btns}")

                await page.screenshot(path="ameblo_05_before_image.png")

                # file inputに直接セット
                for sel in ['input[type="file"]', 'input[name*="image"]', 'input[accept*="image"]']:
                    try:
                        el = page.locator(sel).first
                        count = await el.count()
                        log.info(f"【STEP5】{sel}: count={count}")
                        if count > 0:
                            await el.set_input_files(image_path)
                            log.info(f"【STEP5】画像input直接セット: {sel}")
                            await page.wait_for_timeout(3000)
                            await page.screenshot(path="ameblo_05_after_image.png")
                            break
                    except Exception as e:
                        log.info(f"【STEP5】{sel} 失敗: {e}")

            except Exception as e:
                await page.screenshot(path="ameblo_05_error.png")
                log.warning(f"【STEP5】画像エラー: {e}")
        else:
            log.info(f"【STEP5】画像スキップ（{image_path}）")

        # STEP6: 下書き/公開
        log.info(f"【STEP6】モード: {publish_mode}")
        await page.screenshot(path="ameblo_06_before_action.png")

        # 現時点の全ボタン再確認
        current_btns = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'))
            .map(el => ({
                id: el.id||'',
                text: el.textContent.trim().substring(0,20),
                value: el.value||'',
                disabled: el.disabled
            }))
        """)
        log.info(f"【STEP6】現在のボタン: {current_btns}")

        if publish_mode == "publish":
            action_keywords = ['公開', '投稿']
        else:
            action_keywords = ['下書き', '一時保存', '保存']

        action_done = False

        # ボタンクリック試行
        for sel_text in action_keywords:
            for sel in [f'button:has-text("{sel_text}")', f'input[value*="{sel_text}"]']:
                try:
                    btn = page.locator(sel).first
                    count = await btn.count()
                    log.info(f"【STEP6】{sel}: count={count}")
                    if count > 0 and await btn.is_visible(timeout=3000):
                        await btn.click()
                        log.info(f"【STEP6】クリックOK: {sel}")
                        action_done = True
                        break
                except Exception as e:
                    log.info(f"【STEP6】{sel} 失敗: {e}")
            if action_done:
                break

        # JS直接クリック
        if not action_done:
            for keyword in action_keywords:
                result = await page.evaluate(f"""() => {{
                    const btns = Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'));
                    const b = btns.find(b =>
                        b.textContent.includes('{keyword}') ||
                        b.value?.includes('{keyword}')
                    );
                    if (b) {{ b.click(); return b.textContent || b.value; }}
                    return null;
                }}""")
                log.info(f"【STEP6】JS '{keyword}' クリック結果: {result}")
                if result:
                    action_done = True
                    break

        await page.wait_for_timeout(5000)
        await page.screenshot(path="ameblo_06_after_action.png")
        log.info(f"【STEP6】処理後URL: {page.url}")
        log.info(f"【STEP6】action_done: {action_done}")

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
