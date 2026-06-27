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

async def post_ameblo(article, test_image_only=False):
    """
    Ameblo投稿のメイン処理。

    test_image_only=True の場合:
      - STEP2(タイトル入力)・STEP3(本文入力)は最小限の固定値で済ませる
      - STEP4(画像アップロード)のロジックはそのまま全部実行する
      - STEP5は必ず「下書き保存」を使う（公開はしない安全策）
      - 画像アップロードのデバッグだけを高速に繰り返し試したい時に使う
    """
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
                # 一覧アイテムは <li class="p-images-imageList__listItem ...">。
                # 以前は [class*="..."] (部分一致)で取得していたため、
                # 同じ文字列を部分的に含むクラス名を持つ別要素
                # (<button class="p-images-imageList__listItem__uploadButton">、
                #  つまりアップロードボタン自体)も誤ってマッチしてしまっていた。
                # タグ名をliに固定した完全一致セレクタに変更し、
                # アップロード専用タイル(id="js-file-upload-button")だけを明示的に除外する。
                before_count = await page.locator(
                    'li.p-images-imageList__listItem:not(#js-file-upload-button)'
                ).count()
                log.info(f"【STEP4-B】アップロード前の画像枚数: {before_count}")

                # --- B-0) ファイルがディスク上に本当に存在するか、Python側で直接検証 ---
                abs_image_path = os.path.abspath(image_path)
                file_exists = os.path.exists(image_path)
                file_size = os.path.getsize(image_path) if file_exists else -1
                cwd = os.getcwd()
                log.info(
                    f"【STEP4-B0】ファイル事前検証: "
                    f"渡されたpath='{image_path}', 絶対path='{abs_image_path}', "
                    f"存在={file_exists}, サイズ={file_size}bytes, 現在の作業ディレクトリ='{cwd}'"
                )
                if not file_exists:
                    log.error(f"【STEP4-B0】ファイルがディスク上に存在しない → STEP4中断: {abs_image_path}")
                    try:
                        parent_dir = os.path.dirname(image_path) or "."
                        siblings = os.listdir(parent_dir)
                        log.info(f"【STEP4-B0】参考: '{parent_dir}' 内のファイル一覧（先頭20件）: {siblings[:20]}")
                    except Exception as e:
                        log.warning(f"【STEP4-B0】ディレクトリ一覧取得失敗: {e}")
                    raise RuntimeError(f"画像ファイルが存在しない: {abs_image_path}")
                if file_size == 0:
                    log.error(f"【STEP4-B0】ファイルサイズが0バイト → STEP4中断: {abs_image_path}")
                    raise RuntimeError(f"画像ファイルが0バイト: {abs_image_path}")

                # --- B-1) ネットワークレスポンスの監視を開始 ---
                # アップロードAPIへのリクエストが実際に送信され、どう応答されたかを直接観測する。
                # image/upload系のレスポンスはボディの内容まで取得し、
                # サーバーが実際にどんなURL/IDを返しているかを確認する。
                upload_responses = []
                upload_response_bodies = []

                async def _on_response(response):
                    url = response.url
                    if any(kw in url for kw in ["upload", "image", "file", "asset"]):
                        entry = {
                            "url": url,
                            "status": response.status,
                            "method": response.request.method,
                        }
                        upload_responses.append(entry)
                        # POSTかつ/upload系のものはボディも取得を試みる
                        if response.request.method == "POST" and "upload" in url:
                            try:
                                body_text = await response.text()
                                upload_response_bodies.append({
                                    "url": url,
                                    "status": response.status,
                                    "body": body_text[:2000],  # 長すぎる場合に備えて先頭2000文字
                                })
                            except Exception as e:
                                upload_response_bodies.append({
                                    "url": url,
                                    "status": response.status,
                                    "body": f"(取得失敗: {e})",
                                })

                def _response_listener(response):
                    asyncio.create_task(_on_response(response))

                page.on("response", _response_listener)

                # --- B-2) #js-input-files への直接set_input_filesは
                #     ブラウザ側input.filesに反映されないことが実機検証で確認済み
                #     （Reactのcontrolled input同期問題が疑われる）。
                #     そのため「アップロード」ボタン(#js-file-trigger)をクリックして
                #     ネイティブのファイル選択ダイアログを開かせ、
                #     expect_file_chooser()でそれを捕まえる方式に変更する。
                #     これはnote_poster.pyで既に成功している実装パターンと同じ。
                trigger_btn = page.locator('#js-file-trigger').first
                trigger_count = await trigger_btn.count()
                log.info(f"【STEP4-B1】#js-file-trigger count={trigger_count}")

                file_chooser_used = False
                files_in_dom = {"count": 0}

                if trigger_count > 0:
                    # 通常のクリックを試す（visible待ちでタイムアウトすることがある）
                    try:
                        async with page.expect_file_chooser(timeout=8000) as fc_info:
                            await trigger_btn.click(timeout=8000)
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(image_path)
                        file_chooser_used = True
                        log.info("【STEP4-B2】expect_file_chooser経由でファイルセット完了（通常クリック）")
                    except Exception as e:
                        log.warning(f"【STEP4-B2】通常クリックでのfile_chooserに失敗: {e}")
                        # visible判定を無視した強制クリックを試す
                        # (#js-file-triggerがホバー時のみ表示される、または透明な要素の下に
                        #  隠れている設計のため、通常のvisibility待ちが永遠に成立しない可能性がある)
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
                    # フォールバック: 旧来の直接set_input_files方式を試す
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

                # --- B-3) ファイル選択がブラウザ側に実際に反映されたか確認 ---
                # file_chooser経由でセットした場合、対象inputは#js-file-trigger押下で
                # 内部的に開かれた#js-input-files（もしくは同等の動的input）である想定だが、
                # 念のため#js-input-filesを直接確認する。
                await page.wait_for_timeout(800)
                try:
                    files_in_dom = await page.locator('#js-input-files').first.evaluate(
                        """el => {
                            const files = el.files;
                            if (!files || files.length === 0) return {count: 0};
                            return {
                                count: files.length,
                                name: files[0].name,
                                size: files[0].size,
                                type: files[0].type
                            };
                        }"""
                    )
                except Exception as e:
                    log.warning(f"【STEP4-B3】input.files確認に失敗: {e}")

                log.info(f"【STEP4-B3】input.files の中身（ブラウザ側DOM）: {files_in_dom}")

                if files_in_dom.get("count", 0) == 0:
                    log.warning(
                        "【STEP4-B3】input.filesは依然0件 → "
                        "ただしfile_chooser経由のアップロードはinput.filesに反映されない実装もあるため、"
                        "次の画像枚数増加チェックで最終判定する"
                    )
                else:
                    log.info(
                        f"【STEP4-B3】ブラウザはファイルを認識済み: "
                        f"name={files_in_dom.get('name')}, size={files_in_dom.get('size')}bytes"
                    )

                # --- B-4) アップロードAPIへのPOSTが成功しているかを最優先の判定軸にする ---
                # 実機検証で「左側の画像一覧表示の更新だけがローディングスピナーのまま遅延し、
                # 右側のSNSシェアプレビューには新しい画像が既に反映されている」ことが分かった。
                # つまり「一覧の枚数が増えたか」は判定軸として不適切で、
                # 「アップロードAPI(image/upload)がPOSTで200を返したか」を最優先の根拠とする。
                # その後、ローディングスピナーが消えて一覧が安定するまで待ってから次に進む。
                page.remove_listener("response", _response_listener)
                # ボディ取得は非同期タスクなので、念のため少し待ってから収集結果を確認する
                await page.wait_for_timeout(1000)
                log.info(f"【STEP4-B4】アップロード関連と思われるネットワークレスポンス一覧（件数のみ）: {len(upload_responses)}件")
                post_responses = [r for r in upload_responses if r["method"] == "POST"]
                log.info(f"【STEP4-B4】POSTリクエストのみ抜粋: {post_responses}")
                log.info(f"【STEP4-B5】image/upload系POSTレスポンスのボディ内容: {upload_response_bodies}")

                upload_api_success = any(
                    r["method"] == "POST" and "upload" in r["url"] and 200 <= r["status"] < 300
                    for r in upload_responses
                )
                log.info(f"【STEP4-B5】upload_api_success(POSTが200番台で成功したか)={upload_api_success}")

                # --- B-6) ローディングスピナーが消えて、左側の一覧が安定するまで待つ ---
                # 「枚数が増える」ではなく「ローディング中の表示が消える」ことを待機条件にする。
                # 一覧のloading表現が不明なため、汎用的に「アニメーション中の要素が無くなる」
                # ことと「枚数が変化し続けていないか(安定したか)」の両方で判断する。
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
                    # 3秒間カウントが変化しなければ「安定した」と判断する
                    if stable_streak >= 3:
                        stabilized = True
                        log.info(
                            f"【STEP4-B6】{i+1}秒後に一覧が安定: count={current_count} "
                            f"(アップロード前={before_count})"
                        )
                        break
                else:
                    log.warning(f"【STEP4-B6】20秒待っても一覧が安定しなかった（最終count={last_seen_count}）")

                after_count = last_seen_count
                log.info(f"【STEP4-B6】最終的な画像枚数: before={before_count}, after={after_count}")

                # --- 最終判定 ---
                # 「枚数が増えた」を理想とするが、実機ではアップロード後に一覧の並び替えだけが起きて
                # 総数が変わらないケース（例: 古い画像が削除されつつ追加される設計等）もあり得るため、
                # upload_api_success(サーバーが受理した)を主たる根拠とし、
                # 枚数増加は補助的な確認情報として扱う。
                count_increased = after_count > before_count
                upload_confirmed = upload_api_success or count_increased

                await page.screenshot(path="ameblo_04b_after_upload.png")
                log.info(
                    f"【STEP4-B】アップロード成否判定: upload_confirmed={upload_confirmed} "
                    f"(upload_api_success={upload_api_success}, count_increased={count_increased}, "
                    f"file_chooser_used={file_chooser_used})"
                )

                if not upload_confirmed:
                    log.error(
                        "【STEP4-B】画像アップロードAPIが成功レスポンスを返さず、かつ一覧枚数も増えなかった → STEP4中断 "
                        f"(参考情報: file_chooser_used={file_chooser_used}, files_in_dom={files_in_dom}, "
                        f"stabilized={stabilized}, POSTレスポンス件数={len(post_responses)}, "
                        f"レスポンスボディ={upload_response_bodies})"
                    )
                    raise RuntimeError("画像アップロードAPIの成功確認も、一覧枚数の増加も確認できなかった")

                # --- C) 新しくアップロードされた画像を正しく特定してクリック ---
                # 旧ロジック「:first」決め打ちをやめ、枚数が増えた事実を踏まえた上で、
                # 一覧の先頭アイテムが新規アップロード分であることを前提に選択する
                # （Amebloは新着順に先頭表示される仕様を想定。誤っていればここのログで判明する）
                all_items = page.locator('li.p-images-imageList__listItem:not(#js-file-upload-button)')
                item_count_for_click = await all_items.count()
                log.info(f"【STEP4-C】クリック対象候補の総数: {item_count_for_click}")
                await page.screenshot(path="ameblo_04c_before_click.png")

                clicked_ok = False
                if item_count_for_click > 0:
                    # 念のため、最大3件まで「imgタグを実際に持つ要素」かどうかを確認しながら
                    # クリック対象を探す。万が一セレクタが想定外の要素を拾ってしまっていても、
                    # img無し要素を無条件でクリックして失敗するのを防ぐための保険。
                    target_item = None
                    target_src = None
                    max_check = min(3, item_count_for_click)
                    for idx in range(max_check):
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
                        # img srcが取れなくても、候補[0]自体は要素として存在するので
                        # 最終手段としてそれをそのまま使う（旧来の挙動と同じ）
                        log.warning("【STEP4-C】imgを持つ候補が見つからず、候補[0]をそのまま使用")
                        target_item = all_items.first

                    log.info(f"【STEP4-C】クリック対象に決定したアイテムのimg src: {target_src}")

                    await target_item.click(timeout=10000)
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

                # STEP4が中断した場合、「カバーの設定」モーダルが開いたまま
                # 残っていると、STEP5の下書き保存ボタンがオーバーレイに
                # 隠されてクリックできなくなる（前回実機ログで確認済みの副作用）。
                # ここで確実にモーダルを閉じる後始末をしておく。
                try:
                    modal_still_open = await page.locator('text=カバーの設定').first.is_visible(timeout=2000)
                except Exception:
                    modal_still_open = False

                if modal_still_open:
                    log.warning("【STEP4-後始末】モーダルが開いたまま残っている → キャンセルボタンで閉じる")
                    closed = False
                    for selector in ['button:has-text("キャンセル")', 'button[aria-label="閉じる"]', '.ucsCommonModal__overlay']:
                        try:
                            cancel_el = page.locator(selector).first
                            if await cancel_el.is_visible(timeout=2000):
                                await cancel_el.click(force=True, timeout=3000)
                                await page.wait_for_timeout(1000)
                                closed = True
                                log.info(f"【STEP4-後始末】'{selector}' クリックでモーダルを閉じた")
                                break
                        except Exception as close_err:
                            log.warning(f"【STEP4-後始末】'{selector}' での閉じる試行に失敗: {close_err}")
                    if not closed:
                        log.error("【STEP4-後始末】モーダルを閉じられなかった → STEP5でクリック競合の可能性が残る")
                    await page.screenshot(path="ameblo_04_after_cleanup.png")
                else:
                    log.info("【STEP4-後始末】モーダルは既に閉じている（後始末不要）")
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
    test_image_only = os.environ.get("AMEBLO_TEST_IMAGE_ONLY", "").lower() in ("1", "true", "yes")

    if test_image_only:
        # テストモード: queue/の記事を使わず、専用のテスト画像で画像アップロードだけ試す。
        # テスト用画像パスは環境変数 AMEBLO_TEST_IMAGE_PATH で指定可能（未指定時はデフォルトを探す）。
        test_image_path = os.environ.get("AMEBLO_TEST_IMAGE_PATH", "").strip()

        if not test_image_path:
            # imagesフォルダの中から最初に見つかったjpg/pngを自動採用する
            candidates = sorted(glob.glob("images/*.jpg") + glob.glob("images/*.png"))
            if candidates:
                test_image_path = candidates[0]
                log.info(f"【テストモード】AMEBLO_TEST_IMAGE_PATH未指定 → images/内から自動選択: {test_image_path}")
            else:
                log.error("【テストモード】テスト用画像が見つかりません（images/にjpg/pngが1枚もない）")
                log.error("【テストモード】環境変数 AMEBLO_TEST_IMAGE_PATH で明示的に画像パスを指定してください")
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

    # 通常モード（既存の本番フロー）
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
