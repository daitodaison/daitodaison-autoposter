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

        # ================================================================
        # STEP1: 投稿ページへアクセス
        # ================================================================
        log.info("【STEP1】アメブロ投稿ページへアクセス...")
        await page.goto(
            "https://blog.ameba.jp/ucs/entry/srventryinsertinput.do",
            wait_until="domcontentloaded", timeout=60000
        )
        await page.wait_for_timeout(5000)
        await page.screenshot(path="ameblo_01_initial.png")
        log.info(f"【STEP1】URL: {page.url}")

        if "login" in page.url or "signin" in page.url:
            log.error("【STEP1】Cookieログイン失敗 - ログイン画面のまま")
            await browser.close()
            return False

        # ================================================================
        # STEP2: ページ構造を徹底調査
        # ================================================================
        log.info("【STEP2】ページ構造を徹底調査...")
        await page.screenshot(path="ameblo_02_editor.png")

        # フレーム全リスト
        frames = page.frames
        log.info(f"【STEP2】フレーム数: {len(frames)}")
        for i, frame in enumerate(frames):
            log.info(f"【STEP2】フレーム{i} URL: {frame.url[:100]}")

        # メインページの全ボタン
        all_btns = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'))
            .map(el => ({
                idx: Array.from(document.querySelectorAll('button, input[type=submit]')).indexOf(el),
                id: el.id||'',
                text: el.textContent.trim().substring(0,30),
                value: el.value||'',
                name: el.name||'',
                disabled: el.disabled,
                visible: el.getBoundingClientRect().height > 0
            }))
        """)
        log.info(f"【STEP2】メインページボタン({len(all_btns)}個): {all_btns}")

        # メインページの全input/textarea
        all_inputs = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('input, textarea, [contenteditable]'))
            .map(el => ({
                tag: el.tagName,
                type: el.type||'',
                name: el.name||'',
                id: el.id||'',
                placeholder: el.placeholder||'',
                contenteditable: el.contentEditable,
                visible: el.getBoundingClientRect().height > 0
            }))
        """)
        log.info(f"【STEP2】メインページ入力要素({len(all_inputs)}個): {all_inputs}")

        # 各フレームを詳細調査
        for i, frame in enumerate(frames[1:], 1):  # フレーム0はメインなのでスキップ
            try:
                frame_btns = await frame.evaluate("""() =>
                    Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'))
                    .map(el => ({
                        id: el.id||'',
                        text: el.textContent.trim().substring(0,30),
                        value: el.value||'',
                        visible: el.getBoundingClientRect().height > 0
                    }))
                """)
                frame_inputs = await frame.evaluate("""() =>
                    Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]'))
                    .map(el => ({
                        tag: el.tagName,
                        type: el.type||'',
                        name: el.name||'',
                        id: el.id||'',
                        contenteditable: el.contentEditable,
                        visible: el.getBoundingClientRect().height > 0
                    }))
                """)
                log.info(f"【STEP2】フレーム{i}ボタン: {frame_btns}")
                log.info(f"【STEP2】フレーム{i}入力: {frame_inputs}")
            except Exception as e:
                log.info(f"【STEP2】フレーム{i}アクセス不可: {e}")

        # ================================================================
        # STEP3: タイトル入力
        # ================================================================
        log.info("【STEP3】タイトル入力...")
        title_entered = False

        title_selectors = [
            'input[name="title"]',
            'input[id="entry-title"]',
            'input[id*="title"]',
            'input[placeholder*="タイトル"]',
            '#entry-title',
            '.editor-title input',
        ]

        for sel in title_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible(timeout=3000):
                    await el.click()
                    await el.fill(title)
                    val = await el.input_value()
                    log.info(f"【STEP3】タイトルOK(メイン) {sel} → 入力値: {val[:20]}")
                    title_entered = True
                    break
            except Exception as e:
                log.info(f"【STEP3】{sel} 失敗: {e}")

        if not title_entered:
            for i, frame in enumerate(frames):
                for sel in title_selectors:
                    try:
                        el = frame.locator(sel).first
                        if await el.count() > 0 and await el.is_visible(timeout=2000):
                            await el.click()
                            await el.fill(title)
                            log.info(f"【STEP3】タイトルOK(frame{i}) {sel}")
                            title_entered = True
                            break
                    except Exception:
                        continue
                if title_entered:
                    break

        await page.screenshot(path="ameblo_03_title.png")
        log.info(f"【STEP3】タイトル入力結果: {title_entered}")

        # ================================================================
        # STEP4: 本文入力
        # ================================================================
        log.info("【STEP4】本文入力...")
        body_text = md_to_text(body)[:2000]  # アメブロの文字数制限を考慮
        entered = False

        body_selectors = [
            '[contenteditable="true"]',
            '#editor',
            '.editor-content',
            'textarea[name="body"]',
            'textarea[id*="body"]',
            'textarea[id*="editor"]',
        ]

        for sel in body_selectors:
            try:
                el = page.locator(sel).first
                count = await el.count()
                log.info(f"【STEP4】メイン {sel}: count={count}")
                if count > 0 and await el.is_visible(timeout=3000):
                    await el.click()
                    await page.wait_for_timeout(500)
                    await el.fill(body_text)
                    log.info(f"【STEP4】本文OK(メイン): {sel}")
                    entered = True
                    break
            except Exception as e:
                log.info(f"【STEP4】{sel} 失敗: {e}")

        if not entered:
            for i, frame in enumerate(frames):
                for sel in body_selectors:
                    try:
                        el = frame.locator(sel).first
                        count = await el.count()
                        log.info(f"【STEP4】frame{i} {sel}: count={count}")
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

        await page.screenshot(path="ameblo_04_body.png")
        log.info(f"【STEP4】本文入力結果: {entered}")

        # ================================================================
        # STEP5: 画像アップロード
        # ================================================================
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP5】画像アップロード: {image_path}")
            try:
                # ファイルinputを探す（全フレーム含む）
                file_inputs = await page.evaluate("""() =>
                    Array.from(document.querySelectorAll('input[type="file"]'))
                    .map(el => ({id: el.id, name: el.name, accept: el.accept}))
                """)
                log.info(f"【STEP5】fileInput(メイン): {file_inputs}")

                # 画像ボタンを探す
                img_elements = await page.evaluate("""() =>
                    Array.from(document.querySelectorAll('*'))
                    .filter(el => {
                        const t = (el.textContent||'') + (el.title||'') + (el.getAttribute('aria-label')||'') + (el.id||'') + (el.className||'');
                        return (t.includes('画像') || t.includes('image') || t.includes('photo') || t.includes('img'))
                            && (el.tagName === 'BUTTON' || el.tagName === 'LABEL' || el.tagName === 'A' || el.tagName === 'INPUT');
                    })
                    .map(el => ({tag: el.tagName, text: el.textContent.trim().substring(0,20), id: el.id, class: el.className.substring(0,30)}))
                """)
                log.info(f"【STEP5】画像関連要素: {img_elements}")

                await page.screenshot(path="ameblo_05_before_image.png")

                # file inputに直接セット
                file_set = False
                for sel in ['input[type="file"]', 'input[name*="image"]', 'input[accept*="image"]']:
                    try:
                        el = page.locator(sel).first
                        count = await el.count()
                        log.info(f"【STEP5】{sel}: count={count}")
                        if count > 0:
                            await el.set_input_files(image_path)
                            log.info(f"【STEP5】画像セットOK: {sel}")
                            await page.wait_for_timeout(4000)
                            await page.screenshot(path="ameblo_05_after_image.png")
                            file_set = True
                            break
                    except Exception as e:
                        log.info(f"【STEP5】{sel} 失敗: {e}")

                # フレーム内のfile inputも試す
                if not file_set:
                    for i, frame in enumerate(frames):
                        try:
                            el = frame.locator('input[type="file"]').first
                            if await el.count() > 0:
                                await el.set_input_files(image_path)
                                log.info(f"【STEP5】frame{i}で画像セットOK")
                                await page.wait_for_timeout(4000)
                                await page.screenshot(path="ameblo_05_frame_image.png")
                                file_set = True
                                break
                        except Exception:
                            continue

                log.info(f"【STEP5】画像セット結果: {file_set}")

            except Exception as e:
                await page.screenshot(path="ameblo_05_error.png")
                log.warning(f"【STEP5】画像エラー: {e}")
        else:
            log.info(f"【STEP5】画像スキップ（{image_path}）")

        # ================================================================
        # STEP6: 下書き保存 or 公開
        # ================================================================
        log.info(f"【STEP6】モード: {publish_mode}")
        await page.screenshot(path="ameblo_06_before_action.png")

        # 現時点の全ボタン再確認（実際のテキストを全部ログに出す）
        current_btns = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'))
            .map(el => ({
                id: el.id||'',
                text: el.textContent.trim().substring(0,30),
                value: el.value||'',
                name: el.name||'',
                disabled: el.disabled,
                visible: el.getBoundingClientRect().height > 0
            }))
        """)
        log.info(f"【STEP6】現在のボタン全リスト: {current_btns}")

        # フレームのボタンも確認
        for i, frame in enumerate(frames):
            try:
                fb = await frame.evaluate("""() =>
                    Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'))
                    .map(el => ({
                        id: el.id||'',
                        text: el.textContent.trim().substring(0,30),
                        value: el.value||'',
                        visible: el.getBoundingClientRect().height > 0
                    }))
                """)
                if fb:
                    log.info(f"【STEP6】frame{i}ボタン: {fb}")
            except Exception:
                pass

        if publish_mode == "publish":
            action_keywords = ['公開する', '公開', '投稿する', '投稿']
        else:
            action_keywords = ['下書き保存', '下書き', '一時保存', '保存']

        action_done = False

        # Playwright locatorで試す
        for keyword in action_keywords:
            for sel in [f'button:has-text("{keyword}")', f'input[value="{keyword}"]', f'input[value*="{keyword}"]']:
                try:
                    btn = page.locator(sel).first
                    count = await btn.count()
                    log.info(f"【STEP6】locator '{sel}': count={count}")
                    if count > 0:
                        visible = await btn.is_visible(timeout=3000)
                        log.info(f"【STEP6】'{sel}' visible={visible}")
                        if visible:
                            await btn.click()
                            log.info(f"【STEP6】クリックOK: {sel}")
                            action_done = True
                            break
                except Exception as e:
                    log.info(f"【STEP6】{sel} 失敗: {e}")
            if action_done:
                break

        # JSで直接クリック（全ボタンのテキスト・valueを検索）
        if not action_done:
            for keyword in action_keywords:
                result = await page.evaluate(f"""(kw) => {{
                    const btns = Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'));
                    const b = btns.find(b =>
                        (b.textContent||'').includes(kw) ||
                        (b.value||'').includes(kw)
                    );
                    if (b) {{
                        b.click();
                        return `${{b.tagName}}:${{b.textContent.trim()||b.value}}`;
                    }}
                    return null;
                }}""", keyword)
                log.info(f"【STEP6】JS '{keyword}' クリック結果: {result}")
                if result:
                    action_done = True
                    break

        # フレーム内のボタンも試す
        if not action_done:
            for i, frame in enumerate(frames):
                for keyword in action_keywords:
                    try:
                        result = await frame.evaluate(f"""(kw) => {{
                            const btns = Array.from(document.querySelectorAll('button, input[type=submit], input[type=button]'));
                            const b = btns.find(b =>
                                (b.textContent||'').includes(kw) ||
                                (b.value||'').includes(kw)
                            );
                            if (b) {{
                                b.click();
                                return `${{b.tagName}}:${{b.textContent.trim()||b.value}}`;
                            }}
                            return null;
                        }}""", keyword)
                        log.info(f"【STEP6】frame{i} JS '{keyword}' クリック: {result}")
                        if result:
                            action_done = True
                            break
                    except Exception:
                        continue
                if action_done:
                    break

        await page.wait_for_timeout(5000)
        await page.screenshot(path="ameblo_06_after_action.png")
        log.info(f"【STEP6】処理後URL: {page.url}")
        log.info(f"【STEP6】action_done: {action_done}")

        # STEP7: 結果確認（ページが変わったか・確認ダイアログがあるか）
        log.info("【STEP7】結果確認...")
        await page.screenshot(path="ameblo_07_result.png")
        final_url = page.url
        log.info(f"【STEP7】最終URL: {final_url}")

        # ページタイトルで成否判定
        page_title = await page.title()
        log.info(f"【STEP7】ページタイトル: {page_title}")

        await browser.close()
        log.info(f"【完了】アメブロ処理完了: {title}")
        return action_done

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
