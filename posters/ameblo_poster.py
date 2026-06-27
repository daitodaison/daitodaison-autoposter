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

def md_to_html(text):
    # CKEditorに渡すための簡易HTML変換（改行を<p>で区切る）
    lines = md_to_text(text).split('\n')
    html = []
    for line in lines:
        if line.strip() == '':
            html.append('<p>&nbsp;</p>')
        else:
            # HTMLエスケープ（最低限）
            escaped = (line.replace('&', '&amp;')
                           .replace('<', '&lt;')
                           .replace('>', '&gt;'))
            html.append(f'<p>{escaped}</p>')
    return ''.join(html)

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

            # setData後、エディタ内部のレンダリングが追いつくまで少し待つ
            await page.wait_for_timeout(1500)

            # 検証：iframe内のbody(contenteditable)に実際に文字が入っているか確認
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

        # STEP4: 画像アップロード（カバー画像/サムネイル）
        # Amebloのカバー画像は直接inputを触るだけでは反映されない。
        # 「画像を選択する」ボタン(#js-coverSelect)をクリックすると「カバーの設定」モーダルが開き、
        # その中に新規アップロード用の隠しinput(#js-input-files, name="thumbnail")がある。
        # ここにファイルをセットしてから、モーダル内の「カバーに設定する」ボタンで確定する必要がある。
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP4】カバー画像アップロード開始: {image_path}")
            try:
                # 「画像を選択する」ボタンをクリックしてモーダルを開く
                select_btn = page.locator('#js-coverSelect').first
                is_select_visible = await select_btn.is_visible(timeout=5000)
                log.info(f"【STEP4】'画像を選択する'ボタン visible={is_select_visible}")

                if is_select_visible:
                    await select_btn.click()
                    await page.wait_for_timeout(1500)
                    await page.screenshot(path="ameblo_04_modal_opened.png")
                    log.info("【STEP4】カバーの設定モーダルを開いた")

                    # モーダル内の新規アップロード用inputにファイルをセット
                    file_input = page.locator('#js-input-files').first
                    input_count = await file_input.count()
                    log.info(f"【STEP4】#js-input-files count={input_count}")

                    if input_count > 0:
                        await file_input.set_input_files(image_path)
                        await page.wait_for_timeout(3000)
                        await page.screenshot(path="ameblo_04_after_upload.png")
                        log.info("【STEP4】画像アップロード完了、サムネイル生成待機後スクリーンショット保存")

                        # アップロードした画像が一覧の先頭に選択状態で出る想定。
                        # 念のため一覧の最初の画像タイルを明示的にクリックして選択状態にする。
                        try:
                            first_thumb = page.locator('[class*="p-images-imageList__listItem"]:not(#js-file-upload-button)').first
                            if await first_thumb.is_visible(timeout=3000):
                                await first_thumb.click()
                                await page.wait_for_timeout(500)
                        except Exception:
                            pass

                        # 「カバーに設定する」ボタンをクリックして確定
                        confirm_btn = page.locator('button:has-text("カバーに設定する")').first
                        is_confirm_visible = await confirm_btn.is_visible(timeout=5000)
                        log.info(f"【STEP4】'カバーに設定する'ボタン visible={is_confirm_visible}")

                        if is_confirm_visible:
                            await confirm_btn.click()
                            await page.wait_for_timeout(2000)
                            log.info("【STEP4】カバーに設定するクリックOK")
                        else:
                            log.warning("【STEP4】'カバーに設定する'ボタンが見つからない")
                    else:
                        log.warning("【STEP4】#js-input-files が見つかりません")
                else:
                    log.warning("【STEP4】'画像を選択する'ボタンが見つかりません")

                await page.screenshot(path="ameblo_04_image.png")

                # サムネイルが実際にセットされたか確認（「選択を取り消す」リンクの有無で判定）
                thumb_set = await page.locator('button:has-text("選択を取り消す"), a:has-text("選択を取り消す")').first.is_visible(timeout=3000)
                log.info(f"【STEP4】カバー画像セット確認（選択を取り消すボタンの有無）: {thumb_set}")

            except Exception as e:
                log.warning(f"【STEP4】カバー画像アップロードでエラー: {e}")
                await page.screenshot(path="ameblo_04_error.png")
        else:
            log.info(f"【STEP4】画像スキップ（{image_path}）")

        # STEP5: 下書き保存 or 投稿
        log.info(f"【STEP5】モード: {publish_mode}")
        await page.screenshot(path="ameblo_05_before_action.png")

        # 送信直前の本文文字数を最終確認（CKEditor優先、フォールバックでtextarea）
        final_check = await page.evaluate("""() => {
            try {
                const editor = CKEDITOR.instances['amebloeditor'];
                if (editor) return {source: 'ckeditor', len: editor.getData().length};
            } catch (e) {}
            const ta = document.getElementById('amebloeditor');
            return {source: 'textarea', len: ta ? ta.value.length : 0};
        }""")
        log.info(f"【STEP5】送信直前 本文状態: {final_check}")

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

        # バリデーションエラーモーダルが出ていないかチェック
        error_modal_text = await page.evaluate("""() => {
            const body = document.body.innerText || '';
            if (body.includes('本文を入力してください')) return '本文を入力してください エラーを検出';
            return null;
        }""")
        if error_modal_text:
            log.error(f"【STEP5】保存失敗: {error_modal_text}")
            # エラーモーダルを閉じる（「戻る」ボタン）
            try:
                back_btn = page.locator('button:has-text("戻る")').first
                if await back_btn.is_visible(timeout=3000):
                    await back_btn.click()
            except Exception:
                pass
            action_done = False

        final_url = page.url
        page_title = await page.title()
        log.info(f"【STEP5】処理後URL: {final_url}")
        log.info(f"【STEP5】ページタイトル: {page_title}")

        if "srventryinsertinput" in final_url:
            log.info("【STEP5】URLは変わらず（下書き保存は正常な場合あり）")
        else:
            log.info(f"【STEP5】URLが変わった → 投稿成功の可能性")

        await browser.close()
        if action_done:
            log.info(f"【完了】アメブロ処理完了: {title}")
        else:
            log.error(f"【失敗】アメブロ処理失敗: {title}")
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
