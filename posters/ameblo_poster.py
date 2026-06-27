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
            "name": c["name"], "value": c["value"],
            "domain": c["domain"], "path": c.get("path", "/"),
        }
        if "expirationDate" in c: cookie["expires"] = int(c["expirationDate"])
        if "secure" in c: cookie["secure"] = c["secure"]
        if "httpOnly" in c: cookie["httpOnly"] = c["httpOnly"]
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
            headless=True, args=["--no-sandbox", "--disable-gpu"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        await context.add_cookies(pw_cookies)
        page = await context.new_page()

        # STEP1: 投稿ページへアクセス
        log.info("【STEP1】アメブロ投稿ページへアクセス...")
        await page.goto(
            "https://blog.ameba.jp/ucs/entry/srventryinsertinput.do",
            wait_until="networkidle", timeout=60000
        )
        await page.screenshot(path="ameblo_01_initial.png")
        log.info(f"【STEP1】URL: {page.url}")

        if "login" in page.url or "signin" in page.url:
            log.error("【STEP1】Cookieログイン失敗")
            await browser.close()
            return False

        # STEP2: タイトル入力
        log.info("【STEP2】タイトル入力...")
        try:
            await page.fill('input[name="entry_title"]', title, timeout=5000)
            log.info("【STEP2】タイトルOK: input[name='entry_title']")
        except Exception:
            try:
                await page.fill('input[id*="title"]', title, timeout=5000)
                log.info("【STEP2】タイトルOK: input[id*='title']")
            except Exception as e:
                log.warning(f"【STEP2】タイトル入力失敗: {e}")
        await page.screenshot(path="ameblo_02_title.png")

        # STEP3: 本文入力
# AmebloはCKEditorを使用。#amebloeditor(textarea)はCKEditorの裏データであり、
# 実際にユーザーに見えて送信時にバリデーションされるのは
# iframe.cke_wysiwyg_frame 内の <body contenteditable="true"> 。
# textarea.valueをJSで直接書き換えるだけではCKEditor本体に反映されないため、
# CKEDITOR公式APIの setData() を使ってエディタ本体にHTMLを注入する。
log.info("【STEP3】CKEditor APIで本文入力...")
body_html = md_to_html(body)[:8000]

try:
    # CKEditorインスタンスがロードされるまで待機
    await page.wait_for_function(
        """() => window.CKEDITOR && window.CKEDITOR.instances && window.CKEDITOR.instances['amebloeditor']""",
        timeout=15000
    )
    log.info("【STEP3】CKEDITOR.instances.amebloeditor 検出OK")

    set_result = await page.evaluate("""(html) => {
        try {
            const editor = CKEDITOR.instances['amebloeditor'];
            if (!editor) return {ok: false, reason: 'no instance'};
            editor.setData(html);
            // textarea側(裏データ)にも同期させておく
            editor.updateElement();
            return {ok: true, len: editor.getData().length};
        } catch (e) {
            return {ok: false, reason: String(e)};
        }
    }""", body_html)
    log.info(f"【STEP3】CKEDITOR.setData結果: {set_result}")

    await page.wait_for_timeout(1500)

    verify_len = await page.evaluate("""() => {
        try {
            const editor = CKEDITOR.instances['amebloeditor'];
            return editor ? editor.getData().length : -1;
        } catch (e) { return -2; }
    }""")
    log.info(f"【STEP3】本文反映確認 文字数: {verify_len}")

except Exception as e:
    log.warning(f"【STEP3】CKEditor経由の入力に失敗: {e}")
    log.info("【STEP3】フォールバック: textareaへの直接書き込みを試行")
    try:
        await page.evaluate("""(text) => {
            const ta = document.getElementById('amebloeditor');
            if (ta) {
                ta.value = text;
                ta.dispatchEvent(new Event('input', {bubbles: true}));
                ta.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }""", md_to_text(body)[:8000])
    except Exception as e2:
        log.warning(f"【STEP3】フォールバックも失敗: {e2}")

        await page.screenshot(path="ameblo_03_body.png")

        # STEP4: 画像アップロード（サムネイル）
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP4】画像アップロード: {image_path}")
            try:
                # name="thumbnail" id="js-input-files" のfile inputに直接セット
                el = page.locator('input[name="thumbnail"]').first
                count = await el.count()
                log.info(f"【STEP4】input[name='thumbnail'] count={count}")
                if count > 0:
                    await el.set_input_files(image_path)
                    await page.wait_for_timeout(4000)
                    log.info("【STEP4】サムネイル画像セットOK")
                    await page.screenshot(path="ameblo_04_image.png")
            except Exception as e:
                log.warning(f"【STEP4】画像スキップ: {e}")
        else:
            log.info(f"【STEP4】画像スキップ（{image_path}）")

        # STEP5: 下書き保存 or 投稿
        log.info(f"【STEP5】モード: {publish_mode}")
        await page.screenshot(path="ameblo_05_before_action.png")

        # 現在のtextarea値を最終確認
        final_val_len = await page.evaluate("() => { const ta = document.getElementById('amebloeditor'); return ta ? ta.value.length : 0; }")
        log.info(f"【STEP5】送信直前 #amebloeditor 文字数: {final_val_len}")

        if publish_mode == "publish":
            target_text = "投稿する"
        else:
            target_text = "下書き保存"

        action_done = False
        try:
            btn = page.locator(f'button:has-text("{target_text}")').first
            visible = await btn.is_visible(timeout=5000)
            log.info(f"【STEP5】'{target_text}' ボタン visible={visible}")
            if visible:
                await btn.click()
                log.info(f"【STEP5】'{target_text}' クリックOK")
                action_done = True
        except Exception as e:
            log.warning(f"【STEP5】ボタンクリック失敗: {e}")

        if not action_done:
            result = await page.evaluate(f"""(txt) => {{
                const btns = Array.from(document.querySelectorAll('button'));
                const b = btns.find(b => b.textContent.trim() === txt);
                if (b) {{ b.click(); return true; }}
                return false;
            }}""", target_text)
            log.info(f"【STEP5】JS '{target_text}' クリック: {result}")
            action_done = result

        await page.wait_for_timeout(5000)
        await page.screenshot(path="ameblo_05_after_action.png")
        final_url = page.url
        page_title = await page.title()
        log.info(f"【STEP5】処理後URL: {final_url}")
        log.info(f"【STEP5】ページタイトル: {page_title}")

        # 成否判定：URLが変わったか、確認ページに遷移したか
        if "srventryinsertinput" in final_url:
            # 同じページのまま → 下書き保存はURLが変わらないことがある
            # ページタイトルで確認
            log.info("【STEP5】URLは変わらず（下書き保存は正常な場合あり）")
        else:
            log.info(f"【STEP5】URLが変わった → 投稿成功の可能性")

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
