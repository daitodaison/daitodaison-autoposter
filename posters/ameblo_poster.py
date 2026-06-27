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
    lines = md_to_text(text).split('\n')
    html = []
    for line in lines:
        if line.strip() == '':
            html.append('<p>&nbsp;</p>')
        else:
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

async def post_ameblo(article, test_image_only=False):
    title = article.get("title", "無題")
    body = article.get("body", "")
    image_path = article.get("image_path", "")
    cookies_json = os.environ.get("AMEBLO_COOKIES", "")
    publish_mode = "draft" if test_image_only else os.environ.get("PUBLISH_MODE", "draft")

    if test_image_only:
        log.info("【テストモード】画像アップロード(STEP4)単体テストとして起動")
        title = f"[画像アップロードテスト] {title}"
        body = "これはAmeblo画像アップロード機能の単体テスト用ダミー本文です。"

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

        # STEP3: 本文入力（CKEditor API）
        log.info("【STEP3】CKEditor APIで本文入力...")
        body_html = md_to_html(body)[:8000]

        try:
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

        image_set_ok = None

        # STEP4: 画像アップロード（カバー画像）
        if image_path and os.path.exists(image_path):
            log.info(f"【STEP4-A】カバー画像アップロード開始: {image_path}")
            try:
                # A) モーダルを開く
                select_btn = page.locator('#js-coverSelect').first
                is_select_visible = await select_btn.is_visible(timeout=5000)
                log.info(f"【STEP4-A】'画像を選択する'ボタン visible={is_select_visible}")

                if not is_select_visible:
                    log.error("【STEP4-A】'画像を選択する'ボタンが見つからない → STEP4中断")
                    await page.screenshot(path="ameblo_04a_select_btn_not_found.png")
                    raise RuntimeError("coverSelectボタンが見つからない")

                await select_btn.click()

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

                # B) アップロード前の枚数を記録
                before_count = await page.locator(
                    'li.p-images-imageList__listItem:not(#js-file-upload-button)'
                ).count()
                log.info(f"【STEP4-B】アップロード前の画像枚数: {before_count}")

                # B-0) ファイル存在確認
                abs_image_path = os.path.abspath(image_path)
                file_exists = os.path.exists(image_path)
                file_size = os.path.getsize(image_path) if file_exists else -1
                cwd = os.getcwd()
                log.info(
                    f"【STEP4-B0】ファイル事前検証: "
                    f"渡されたpath='{image_path}', 絶対path='{abs_image_path}', "
                    f"存在={file_exists}, サイズ={file_size}bytes, cwd='{cwd}'"
                )
                if not file_exists:
                    try:
                        parent_dir = os.path.dirname(image_path) or "."
                        siblings = os.listdir(parent_dir)
                        log.info(f"【STEP4-B0】参考: '{parent_dir}' 内のファイル一覧（先頭20件）: {siblings[:20]}")
                    except Exception as e:
                        log.warning(f"【STEP4-B0】ディレクトリ一覧取得失敗: {e}")
                    raise RuntimeError(f"画像ファイルが存在しない: {abs_image_path}")
                if file_size == 0:
                    raise RuntimeError(f"画像ファイルが0バイト: {abs_image_path}")

                # B-1) ネットワーク監視開始
                upload_responses = []
                upload_response_bodies = []

                async def _on_response(response):
                    url = response.url
                    if any(kw in url for kw in ["upload", "image", "file", "asset"]):
                        entry = {"url": url, "status": response.status, "method": response.request.method}
                        upload_responses.append(entry)
                        if response.request.method == "POST" and "upload" in url:
                            try:
                                body_text = await response.text()
                                upload_response_bodies.append({"url": url, "status": response.status, "body": body_text[:2000]})
                            except Exception as e:
                                upload_response_bodies.append({"url": url, "status": response.status, "body": f"(取得失敗: {e})"})

                def _response_listener(response):
                    asyncio.create_task(_on_response(response))

                page.on("response", _response_listener)

                # B-2) ファイル選択（file_chooser方式 → 直接set_input_filesフォールバック）
                trigger_btn = page.locator('#js-file-trigger').first
                trigger_count = await trigger_btn.count()
                log.info(f"【STEP4-B1】#js-file-trigger count={trigger_count}")

                file_chooser_used = False
                files_in_dom = {"count": 0}

                if trigger_count > 0:
                    try:
                        async with page.expect_file_chooser(timeout=8000) as fc_info:
                            await trigger_btn.click(timeout=8000)
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(image_path)
                        file_chooser_used = True
                        log.info("【STEP4-B2】expect_file_chooser経由でファイルセット完了（通常クリック）")
                    except Exception as e:
                        log.warning(f"【STEP4-B2】通常クリックでのfile_chooserに失敗: {e}")
                        try:
                            log.info("【STEP4-B2】force=Trueでの強制クリックを試行")
                            async with page.expect_file_chooser(timeout=8000) as fc_info:
                                await trigger_btn.click(force=True, timeout=8000)
                            file_chooser = await fc_info.value
                            await file_chooser.set_files(image_path)
                            file_chooser_used = True
                            log.info("【STEP4-B2】expect_file_chooser経由でファイルセット完了（強制クリック）")
                        except Exception as e2:
                            log.warning(f"【STEP4-B2】強制クリックでもfile_chooserに失敗: {e2}")
                else:
                    log.warning("【STEP4-B1】#js-file-trigger が見つからない")

                if not file_chooser_used:
                    log.info("【STEP4-B2】フォールバック: #js-input-filesへの直接set_input_filesを試行")
                    file_input = page.locator('#js-input-files').first
                    input_count = await file_input.count()
                    log.info(f"【STEP4-B2】#js-input-files count={input_count}")
                    if input_count > 0:
                        try:
                            await file_input.set_input_files(image_path)
                            log.info("【STEP4-B2】フォールバックのset_input_files実行完了")
                        except Exception as e:
                            log.warning(f"【STEP4-B2】フォールバックも失敗: {e}")

                # B-3) ブラウザ側files確認
                await page.wait_for_timeout(800)
                try:
                    files_in_dom = await page.locator('#js-input-files').first.evaluate(
                        """el => {
                            const files = el.files;
                            if (!files || files.length === 0) return {count: 0};
                            return {count: files.length, name: files[0].name, size: files[0].size, type: files[0].type};
                        }"""
                    )
                except Exception as e:
                    log.warning(f"【STEP4-B3】input.files確認に失敗: {e}")
                log.info(f"【STEP4-B3】input.files の中身（ブラウザ側DOM）: {files_in_dom}")

                # B-4/5) ネットワーク監視終了・集計
                page.remove_listener("response", _response_listener)
                await page.wait_for_timeout(1000)
                post_responses = [r for r in upload_responses if r["method"] == "POST"]
                log.info(f"【STEP4-B4】POSTリクエストのみ抜粋: {post_responses}")
                log.info(f"【STEP4-B5】image/upload系POSTレスポンスのボディ内容: {upload_response_bodies}")

                upload_api_success = any(
                    r["method"] == "POST" and "upload" in r["url"] and 200 <= r["status"] < 300
                    for r in upload_responses
                )
                log.info(f"【STEP4-B5】upload_api_success={upload_api_success}")

                # B-6) 一覧が安定するまで待つ
                stabilized = False
                last_seen_count = before_count
                stable_streak = 0
                for i in range(20):
                    await page.wait_for_timeout(1000)
                    current_count = await page.locator(
                        'li.p-images-imageList__listItem:not(#js-file-upload-button)'
                    ).count()
                    if current_count == last_seen_count:
                        stable_streak += 1
                    else:
                        stable_streak = 0
                        last_seen_count = current_count
                    if stable_streak >= 3:
                        stabilized = True
                        log.info(f"【STEP4-B6】{i+1}秒後に一覧が安定: count={current_count}")
                        break
                else:
                    log.warning(f"【STEP4-B6】20秒待っても一覧が安定しなかった（最終count={last_seen_count}）")

                after_count = last_seen_count
                count_increased = after_count > before_count
                upload_confirmed = upload_api_success or count_increased

                await page.screenshot(path="ameblo_04b_after_upload.png")
                log.info(f"【STEP4-B】アップロード成否判定: upload_confirmed={upload_confirmed}")

                if not upload_confirmed:
                    raise RuntimeError("画像アップロードAPIの成功確認も、一覧枚数の増加も確認できなかった")

                # C) 新しい画像をクリック
                all_items = page.locator('li.p-images-imageList__listItem:not(#js-file-upload-button)')
                item_count_for_click = await all_items.count()
                log.info(f"【STEP4-C】クリック対象候補の総数: {item_count_for_click}")
                await page.screenshot(path="ameblo_04c_before_click.png")

                clicked_ok = False
                if item_count_for_click > 0:
                    target_item = None
                    target_src = None
                    for idx in range(min(3, item_count_for_click)):
                        candidate = all_items.nth(idx)
                        try:
                            src = await candidate.locator('img').first.get_attribute('src', timeout=2000)
                        except Exception:
                            src = None
                        log.info(f"【STEP4-C】候補[{idx}]のimg src: {src}")
                        if src is not None:
                            target_item = candidate
                            target_src = src
                            break

                    if target_item is None:
                        log.warning("【STEP4-C】imgを持つ候補が見つからず、候補[0]をそのまま使用")
                        target_item = all_items.first

                    log.info(f"【STEP4-C】クリック対象img src: {target_src}")
                    await target_item.click(timeout=10000)
                    await page.wait_for_timeout(800)
                    clicked_ok = True
                    await page.screenshot(path="ameblo_04c_item_clicked.png")
                else:
                    log.error("【STEP4-C】クリック可能な画像アイテムが0件")

                if not clicked_ok:
                    raise RuntimeError("新規画像のクリックに失敗")

                # D) 「カバーに設定する」をクリック → モーダルが閉じるのを確認
                confirm_btn = page.locator('button:has-text("カバーに設定する")').first
                is_confirm_visible = await confirm_btn.is_visible(timeout=5000)
                log.info(f"【STEP4-D】'カバーに設定する'ボタン visible={is_confirm_visible}")

                if not is_confirm_visible:
                    await page.screenshot(path="ameblo_04d_confirm_btn_not_found.png")
                    raise RuntimeError("カバーに設定するボタンが見つからない")

                await confirm_btn.click()
                log.info("【STEP4-D】'カバーに設定する'クリック実行")

                modal_closed = False
                try:
                    await page.wait_for_selector('text=カバーの設定', state='hidden', timeout=8000)
                    modal_closed = True
                except Exception as e:
                    log.warning(f"【STEP4-D】モーダルが閉じたことを確認できなかった: {e}")
                log.info(f"【STEP4-D】モーダルクローズ確認: {modal_closed}")

                # ★ モーダルが閉じた直後のスクショ（カバー画像セット確認用）
                # ⚠️ 2026/06/27時点のAmeblo仕様: 下書き保存を押すとカバー画像が消える。
                #    このスクショが「画像セット成功」の証拠になる。
                await page.wait_for_timeout(500)
                await page.screenshot(path="ameblo_04d_after_confirm.png")
                log.info("【STEP4-D】★ ameblo_04d_after_confirm.png 撮影（カバー画像セット確認用）")

                # E) DOM検証
                cover_check = await page.evaluate("""() => {
                    const candidates = [];
                    document.querySelectorAll('#js-coverContent img').forEach(img => {
                        candidates.push({selector: '#js-coverContent img', src: img.src, naturalWidth: img.naturalWidth});
                    });
                    document.querySelectorAll('[class*="p-cover"] img').forEach(img => {
                        candidates.push({selector: '[class*=p-cover] img', src: img.src, naturalWidth: img.naturalWidth});
                    });
                    const coverDiv = document.querySelector('#js-coverContent');
                    let bgImage = null;
                    if (coverDiv) bgImage = window.getComputedStyle(coverDiv).backgroundImage;
                    const bodyText = document.body.innerText || '';
                    return {
                        candidates: candidates,
                        backgroundImage: bgImage,
                        hasCancelText: bodyText.includes('選択を取り消す')
                    };
                }""")
                log.info(f"【STEP4-E】カバー画像DOM検証結果: {cover_check}")

                has_real_image = False
                for c in cover_check.get("candidates", []):
                    src = c.get("src") or ""
                    if src and not src.startswith("data:image/gif") and c.get("naturalWidth", 0) > 1:
                        has_real_image = True
                        break
                bg = cover_check.get("backgroundImage") or ""
                if bg and bg != "none":
                    has_real_image = True

                log.info(f"【STEP4-E】最終判定 has_real_image={has_real_image}")
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

                try:
                    modal_still_open = await page.locator('text=カバーの設定').first.is_visible(timeout=2000)
                except Exception:
                    modal_still_open = False

                if modal_still_open:
                    log.warning("【STEP4-後始末】モーダルが開いたまま残っている → キャンセルボタンで閉じる")
                    for selector in ['button:has-text("キャンセル")', 'button[aria-label="閉じる"]', '.ucsCommonModal__overlay']:
                        try:
                            cancel_el = page.locator(selector).first
                            if await cancel_el.is_visible(timeout=2000):
                                await cancel_el.click(force=True, timeout=3000)
                                await page.wait_for_timeout(1000)
                                log.info(f"【STEP4-後始末】'{selector}' でモーダルを閉じた")
                                break
                        except Exception as close_err:
                            log.warning(f"【STEP4-後始末】'{selector}' での閉じる試行に失敗: {close_err}")
                    await page.screenshot(path="ameblo_04_after_cleanup.png")
                else:
                    log.info("【STEP4-後始末】モーダルは既に閉じている（後始末不要）")
        else:
            log.info(f"【STEP4】画像スキップ（image_path={image_path}）")

        # STEP5: 下書き保存 or 投稿
        log.info(f"【STEP5】モード: {publish_mode}")
        # ★ 下書きボタンを押す前のスクショ（カバー画像確認用）
        # ⚠️ 2026/06/27時点のAmeblo仕様: 下書き保存を押すとカバー画像が消えるため
        #    このスクショで画像が入っていれば成功と判定する
        await page.wait_for_timeout(1000)
        await page.screenshot(path="ameblo_05_before_action.png")
        log.info("【STEP5】★ ameblo_05_before_action.png 撮影（下書き前・カバー画像最終確認用）")

        final_check = await page.evaluate("""() => {
            try {
                const editor = CKEDITOR.instances['amebloeditor'];
                if (editor) return {source: 'ckeditor', len: editor.getData().length};
            } catch (e) {}
            const ta = document.getElementById('amebloeditor');
            return {source: 'textarea', len: ta ? ta.value.length : 0};
        }""")
        log.info(f"【STEP5】送信直前 本文状態: {final_check}")

        target_text = "投稿する" if publish_mode == "publish" else "下書き保存"

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
            result = await page.evaluate("""(txt) => {
                const btns = Array.from(document.querySelectorAll('button'));
                const b = btns.find(b => b.textContent.trim() === txt);
                if (b) { b.click(); return true; }
                return false;
            }""", target_text)
            log.info(f"【STEP5】JS '{target_text}' クリック: {result}")
            action_done = result

        await page.wait_for_timeout(5000)
        await page.screenshot(path="ameblo_05_after_action.png")

        error_modal_text = await page.evaluate("""() => {
            const body = document.body.innerText || '';
            if (body.includes('本文を入力してください')) return '本文を入力してください エラーを検出';
            return null;
        }""")
        if error_modal_text:
            log.error(f"【STEP5】保存失敗: {error_modal_text}")
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
    test_image_only = os.environ.get("AMEBLO_TEST_IMAGE_ONLY", "").lower() in ("1", "true", "yes")

    if test_image_only:
        test_image_path = os.environ.get("AMEBLO_TEST_IMAGE_PATH", "").strip()
        if not test_image_path:
            candidates = sorted(glob.glob("images/*.jpg") + glob.glob("images/*.png"))
            if candidates:
                test_image_path = candidates[0]
                log.info(f"【テストモード】AMEBLO_TEST_IMAGE_PATH未指定 → images/内から自動選択: {test_image_path}")
            else:
                log.error("【テストモード】テスト用画像が見つかりません（images/にjpg/pngが1枚もない）")
                return
        if not os.path.exists(test_image_path):
            log.error(f"【テストモード】指定された画像が存在しません: {test_image_path}")
            return
        dummy_article = {
            "title": "画像アップロード単体テスト",
            "body": "これはAmeblo画像アップロード機能の単体テスト用ダミー本文です。",
            "image_path": test_image_path,
        }
        log.info(f"【テストモード】開始: image_path={test_image_path}")
        asyncio.run(post_ameblo(dummy_article, test_image_only=True))
        log.info("【テストモード】完了（queue/には触れていません）")
        return

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
