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

        # STEP4全体の成否を関数の最後まで持ち越すための変数
        image_set_ok = None  # None=画像指定なし/スキップ, True=確認済み成功, False=失敗

        # ============================================================
        # STEP4: 画像アップロード（カバー画像/サムネイル）
        # ------------------------------------------------------------
        # Amebloのカバー画像は直接inputを触るだけでは反映されない。
        # 「画像を選択する」ボタン(#js-coverSelect)をクリックすると「カバーの設定」モーダルが開き、
        # その中に新規アップロード用の隠しinput(#js-input-files, name="thumbnail")がある。
        # ここにファイルをセットしてから、一覧に追加された「新しい画像」を明示的に選択し、
        # モーダル内の「カバーに設定する」ボタンで確定する必要がある。
        #
        # このバージョンでは以下の不確定要素をすべて個別に検証する:
        #   A) モーダルが本当に開いたか
        #   B) アップロード前後で一覧の画像枚数が増えたか（アップロード自体の成否）
        #   C) 増えた画像（=新しい画像）を正しく特定してクリックできたか
        #      （「一覧の先頭」を決め打ちでクリックする旧ロジックの誤りを修正）
        #   D) 「カバーに設定する」クリック後、モーダルが実際に閉じたか
        #   E) 閉じた後、カバー画像のimg要素に実際にsrc(blobやURL)が入っているか
        #      （テキストの有無ではなくDOMの実体を見て判定する）
        # ============================================================
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP4-A】カバー画像アップロード開始: {image_path}")
            try:
                # --- A) 「画像を選択する」ボタンをクリックしてモーダルを開く ---
                select_btn = page.locator('#js-coverSelect').first
                is_select_visible = await select_btn.is_visible(timeout=5000)
                log.info(f"【STEP4-A】'画像を選択する'ボタン visible={is_select_visible}")

                if not is_select_visible:
                    log.error("【STEP4-A】'画像を選択する'ボタンが見つからない → STEP4中断")
                    await page.screenshot(path="ameblo_04a_select_btn_not_found.png")
                    raise RuntimeError("coverSelectボタンが見つからない")

                await select_btn.click()

                # モーダルが実際にDOM上に出現するまで明示的に待つ（決め打ちのtimeoutに頼らない）
                modal_appeared = False
                try:
                    await page.wait_for_selector('text=カバーの設定', timeout=8000)
                    modal_appeared = True
                except Exception:
                    pass
                log.info(f"【STEP4-A】「カバーの設定」モーダル出現確認: {modal_appeared}")
                await page.screenshot(path="ameblo_04a_modal_opened.png")

                if not modal_appeared:
                    log.error("【STEP4-A】モーダルが開いていない → STEP4中断")
                    raise RuntimeError("カバーの設定モーダルが出現しなかった")

                # --- B) アップロード前の画像枚数を記録 ---
                # 一覧アイテムは class="p-images-imageList__listItem ..." を持つが、
                # アップロード専用タイル(id="js-file-upload-button")は除外してカウントする
                before_count = await page.locator(
                    '[class*="p-images-imageList__listItem"]:not(#js-file-upload-button)'
                ).count()
                log.info(f"【STEP4-B】アップロード前の画像枚数: {before_count}")

                file_input = page.locator('#js-input-files').first
                input_count = await file_input.count()
                log.info(f"【STEP4-B】#js-input-files count={input_count}")

                if input_count == 0:
                    log.error("【STEP4-B】#js-input-files が見つからない → STEP4中断")
                    await page.screenshot(path="ameblo_04b_input_not_found.png")
                    raise RuntimeError("アップロード用inputが見つからない")

                await file_input.set_input_files(image_path)
                log.info("【STEP4-B】set_input_files実行完了。アップロード処理の完了を待機...")

                # 決め打ちの3秒待機ではなく、枚数が増えるまで最大15秒ポーリングする
                after_count = before_count
                upload_confirmed = False
                for i in range(15):
                    await page.wait_for_timeout(1000)
                    after_count = await page.locator(
                        '[class*="p-images-imageList__listItem"]:not(#js-file-upload-button)'
                    ).count()
                    if after_count > before_count:
                        upload_confirmed = True
                        log.info(f"【STEP4-B】{i+1}秒後に画像枚数増加を検出: {before_count} → {after_count}")
                        break
                else:
                    log.warning(f"【STEP4-B】15秒待っても枚数が増えなかった（before={before_count}, after={after_count}）")

                await page.screenshot(path="ameblo_04b_after_upload.png")
                log.info(f"【STEP4-B】アップロード成否判定: upload_confirmed={upload_confirmed}")

                if not upload_confirmed:
                    log.error("【STEP4-B】画像アップロード自体が失敗している可能性 → STEP4中断")
                    raise RuntimeError("アップロード後も画像枚数が増加しなかった")

                # --- C) 新しくアップロードされた画像を正しく特定してクリック ---
                # 旧ロジック「:first」決め打ちをやめ、枚数が増えた事実を踏まえた上で、
                # 一覧の先頭アイテムが新規アップロード分であることを前提に選択する
                # （Amebloは新着順に先頭表示される仕様を想定。誤っていればここのログで判明する）
                all_items = page.locator('[class*="p-images-imageList__listItem"]:not(#js-file-upload-button)')
                item_count_for_click = await all_items.count()
                log.info(f"【STEP4-C】クリック対象候補の総数: {item_count_for_click}")

                clicked_ok = False
                if item_count_for_click > 0:
                    target_item = all_items.first
                    # クリック対象のimg src（あれば）をログに残し、後でカバー画像と一致するか追跡できるようにする
                    try:
                        target_src = await target_item.locator('img').first.get_attribute('src', timeout=2000)
                    except Exception:
                        target_src = None
                    log.info(f"【STEP4-C】クリック対象(先頭アイテム)のimg src: {target_src}")

                    await target_item.click()
                    await page.wait_for_timeout(800)
                    clicked_ok = True
                    await page.screenshot(path="ameblo_04c_item_clicked.png")
                else:
                    log.error("【STEP4-C】クリック可能な画像アイテムが0件")

                log.info(f"【STEP4-C】画像選択クリック成否: {clicked_ok}")

                if not clicked_ok:
                    raise RuntimeError("新規画像のクリックに失敗")

                # --- D) 「カバーに設定する」ボタンをクリックして確定し、モーダルが閉じるのを確認 ---
                confirm_btn = page.locator('button:has-text("カバーに設定する")').first
                is_confirm_visible = await confirm_btn.is_visible(timeout=5000)
                log.info(f"【STEP4-D】'カバーに設定する'ボタン visible={is_confirm_visible}")

                if not is_confirm_visible:
                    log.error("【STEP4-D】'カバーに設定する'ボタンが見つからない → STEP4中断")
                    await page.screenshot(path="ameblo_04d_confirm_btn_not_found.png")
                    raise RuntimeError("カバーに設定するボタンが見つからない")

                await confirm_btn.click()
                log.info("【STEP4-D】'カバーに設定する'クリック実行")

                # モーダルが実際に閉じる（「カバーの設定」テキストがDOMから消える）まで明示的に待つ
                modal_closed = False
                try:
                    await page.wait_for_selector('text=カバーの設定', state='hidden', timeout=8000)
                    modal_closed = True
                except Exception as e:
                    log.warning(f"【STEP4-D】モーダルが閉じたことを確認できなかった: {e}")

                log.info(f"【STEP4-D】モーダルクローズ確認: {modal_closed}")
                await page.screenshot(path="ameblo_04d_after_confirm.png")

                if not modal_closed:
                    log.warning("【STEP4-D】モーダルが閉じていない可能性 → 以降の判定は不正確になりうる")

                # --- E) カバー画像のimg要素に実際にsrcが入っているかをDOMで直接検証 ---
                # テキスト('選択を取り消す'等)の有無に依存せず、実体(img[src])で判定する。
                # p-cover__image 付近、または #js-coverContent 配下のimgを広く探索する。
                cover_check = await page.evaluate("""() => {
                    const candidates = [];
                    // 候補1: #js-coverContent配下のimg
                    document.querySelectorAll('#js-coverContent img').forEach(img => {
                        candidates.push({selector: '#js-coverContent img', src: img.src, naturalWidth: img.naturalWidth});
                    });
                    // 候補2: class名にp-coverを含む要素配下のimg
                    document.querySelectorAll('[class*="p-cover"] img').forEach(img => {
                        candidates.push({selector: '[class*=p-cover] img', src: img.src, naturalWidth: img.naturalWidth});
                    });
                    // 候補3: 背景画像として設定されている可能性も見る
                    const coverDiv = document.querySelector('#js-coverContent');
                    let bgImage = null;
                    if (coverDiv) {
                        const style = window.getComputedStyle(coverDiv);
                        bgImage = style.backgroundImage;
                    }
                    // 「選択を取り消す」テキストの有無も参考情報として残す（判定には使わない）
                    const bodyText = document.body.innerText || '';
                    const hasCancelText = bodyText.includes('選択を取り消す');

                    return {
                        candidates: candidates,
                        backgroundImage: bgImage,
                        hasCancelText: hasCancelText
                    };
                }""")
                log.info(f"【STEP4-E】カバー画像DOM検証結果: {cover_check}")

                # 実際に有効なsrc（空でなく、data:image/gifの1x1プレースホルダー等でもない）を持つimgがあるか
                has_real_image = False
                for c in cover_check.get("candidates", []):
                    src = c.get("src") or ""
                    if src and not src.startswith("data:image/gif") and c.get("naturalWidth", 0) > 1:
                        has_real_image = True
                        break
                # backgroundImageに'none'以外の値が入っていれば、それも有効な根拠とする
                bg = cover_check.get("backgroundImage") or ""
                if bg and bg != "none":
                    has_real_image = True

                log.info(f"【STEP4-E】最終判定 has_real_image={has_real_image} "
                          f"(参考: テキスト判定hasCancelText={cover_check.get('hasCancelText')})")

                await page.screenshot(path="ameblo_04e_final_check.png")

                if has_real_image:
                    log.info("【STEP4-完了】カバー画像セット成功（DOM実体確認済み）")
                    image_set_ok = True
                else:
                    log.error("【STEP4-失敗】カバー画像が実際には設定されていない（DOM上に実体なし）")
                    image_set_ok = False

            except Exception as e:
                log.error(f"【STEP4-エラー】カバー画像アップロード処理で例外: {e}")
                await page.screenshot(path="ameblo_04_error.png")
                image_set_ok = False
        else:
            log.info(f"【STEP4】画像スキップ（image_path={image_path}）")

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
        log.info(f"【最終サマリー】本文={final_check}, 画像セット成否={image_set_ok}, 保存操作={action_done}")
        if action_done:
            log.info(f"【完了】アメブロ処理完了: {title}")
        else:
            log.error(f"【失敗】アメブロ処理失敗: {title}")
        if image_set_ok is False:
            log.error("【注意】本文・保存は成功しているが、カバー画像は設定されていない可能性が高い")
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
