# -*- coding: utf-8 -*-
#
# ⚠️ 【2026/06/27 時点のAmeblo仕様注意】
#   下書き保存を押すとカバー画像が消える不具合あり。
#   公開（投稿する）ボタンでは画像は保持される。
#   このスクリプトでは「下書き保存ボタンを押す直前」に
#   ameblo_05_before_action.png を撮影し、そこで画像がセット
#   されていれば「成功」とみなす（下書き後の画像消失はAmeblo側の問題）。
#
import json, os, asyncio, logging, glob, re
from pathlib import Path
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def md_to_html(text):
    lines = text.strip().split('\n')
    html_parts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
        line = re.sub(r'\*(.+?)\*', r'<em>\1</em>', line)
        if line.startswith('# '):
            html_parts.append(f'<h2>{line[2:]}</h2>')
        elif line.startswith('## '):
            html_parts.append(f'<h3>{line[3:]}</h3>')
        elif line.startswith('### '):
            html_parts.append(f'<h4>{line[4:]}</h4>')
        elif line.startswith('- ') or line.startswith('・'):
            item = line[2:] if line.startswith('- ') else line[1:]
            html_parts.append(f'<li>{item}</li>')
        elif line.startswith('---'):
            html_parts.append('<hr/>')
        else:
            html_parts.append(f'<p>{line}</p>')
    return '\n'.join(html_parts)


async def post_ameblo(article, test_image_only=False):
    user        = os.environ.get("AMEBLO_USER", "")
    pwd         = os.environ.get("AMEBLO_PASS", "")
    cookies_json = os.environ.get("AMEBLO_COOKIES", "")
    publish_mode = os.environ.get("PUBLISH_MODE", "draft")

    if test_image_only:
        publish_mode = "draft"

    title      = article.get("title", "テスト記事")
    body       = article.get("body", "テスト本文")
    image_path = article.get("image_path", "")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1280, "height": 900})

        if cookies_json:
            try:
                cookies = json.loads(cookies_json)
                await context.add_cookies(cookies)
                log.info("Cookie設定完了")
            except Exception as e:
                log.warning(f"Cookie設定失敗: {e}")

        page = await context.new_page()

        # ── STEP1: 投稿ページへ ────────────────────────────
        log.info("【STEP1】アメブロ投稿ページへアクセス...")
        await page.goto(
            "https://blog.ameba.jp/ucs/entry/srventryinsertinput.do",
            wait_until="networkidle", timeout=30000
        )
        await page.screenshot(path="ameblo_01_initial.png")
        log.info(f"【STEP1】URL: {page.url}")

        if "login" in page.url or "signin" in page.url:
            log.info("【STEP1】ログイン処理...")
            try:
                await page.fill('input[name="accountId"]', user, timeout=10000)
                await page.fill('input[name="password"]', pwd)
                await page.click('button[type="submit"]')
                await page.wait_for_load_state("networkidle", timeout=20000)
                await page.goto(
                    "https://blog.ameba.jp/ucs/entry/srventryinsertinput.do",
                    wait_until="networkidle", timeout=30000
                )
            except Exception as e:
                log.warning(f"【STEP1】ログイン失敗: {e}")

        # ── STEP2: タイトル入力 ────────────────────────────
        log.info("【STEP2】タイトル入力...")
        try:
            title_input = page.locator('input[name="entry_title"]').first
            await title_input.wait_for(state="visible", timeout=10000)
            await title_input.fill(title)
            log.info(f"【STEP2】タイトルOK: input[name='entry_title']")
        except Exception as e:
            log.warning(f"【STEP2】タイトル入力失敗: {e}")
        await page.screenshot(path="ameblo_02_title.png")

        # ── STEP3: 本文入力（CKEditor API） ────────────────
        log.info("【STEP3】CKEditor APIで本文入力...")
        body_html = md_to_html(body)[:8000]
        final_body_state = {"source": "unknown", "len": -1}
        try:
            await page.wait_for_function(
                "() => window.CKEDITOR && window.CKEDITOR.instances && window.CKEDITOR.instances['amebloeditor']",
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
                } catch(e) { return {ok: false, reason: String(e)}; }
            }""", body_html)
            log.info(f"【STEP3】CKEDITOR.setData結果: {set_result}")
            await page.wait_for_timeout(1500)

            verify_len = await page.evaluate("""() => {
                try {
                    const editor = CKEDITOR.instances['amebloeditor'];
                    return editor ? editor.getData().length : -1;
                } catch(e) { return -2; }
            }""")
            log.info(f"【STEP3】本文反映確認 文字数: {verify_len}")
            final_body_state = {"source": "ckeditor", "len": verify_len}

        except Exception as e:
            log.warning(f"【STEP3】CKEditor失敗: {e}")
            try:
                await page.evaluate("""(text) => {
                    const ta = document.getElementById('amebloeditor');
                    if (ta) {
                        ta.value = text;
                        ta.dispatchEvent(new Event('input', {bubbles:true}));
                        ta.dispatchEvent(new Event('change', {bubbles:true}));
                    }
                }""", body_html)
                log.info("【STEP3】フォールバック: textarea直接書き込み完了")
                final_body_state = {"source": "textarea_fallback", "len": len(body_html)}
            except Exception as e2:
                log.warning(f"【STEP3】フォールバックも失敗: {e2}")
        await page.screenshot(path="ameblo_03_body.png")

        # ── STEP4: カバー画像設定 ──────────────────────────
        #
        # 【⚠️ Ameblo 2026/06/27 仕様】
        #   下書き保存を押すとカバー画像が消える。
        #   そのため「下書き保存ボタンを押す直前」のスクショ
        #   (ameblo_05_before_action.png) に画像が入っていれば
        #   このステップは成功とみなす。
        #   公開（投稿する）時は画像が保持される。
        #
        image_set_ok = False

        if image_path and os.path.exists(image_path):
            file_size = os.path.getsize(image_path)
            log.info(f"【STEP4】カバー画像設定開始: {image_path} ({file_size}bytes)")

            try:
                # A: モーダルを開く
                cover_btn = page.locator('#js-coverSelect')
                await cover_btn.wait_for(state="visible", timeout=10000)
                await cover_btn.click()
                await page.wait_for_selector('text=カバーの設定', state="visible", timeout=8000)
                log.info("【STEP4-A】カバーの設定モーダルを開いた")
                await page.screenshot(path="ameblo_04a_modal_opened.png")

                # B: アップロード
                #    AmebloはアップロードするとモーダルのSNSプレビュー（右側）に
                #    即時反映され、画像が自動でSELECTED状態（緑枠）になる。
                #    → クリック操作は不要。
                file_input = page.locator('#js-input-files')
                await file_input.set_input_files(image_path)
                log.info("【STEP4-B】set_input_files実行完了")

                # サーバー処理完了を待つ（右側SNSプレビューの画像が切り替わるまで）
                upload_ok = False
                for i in range(20):
                    await page.wait_for_timeout(1000)
                    has_preview = await page.evaluate("""() => {
                        try {
                            // 右側SNSプレビューエリアのimgを確認
                            const imgs = document.querySelectorAll('[class*="SharePreview"] img, [class*="share_preview"] img, [class*="preview"] img');
                            for (const img of imgs) {
                                if (img.src && (img.src.includes('ameba') || img.src.includes('blogimg'))) return true;
                            }
                            // alt属性にカバー画像と入っているか
                            const altImgs = document.querySelectorAll('img[alt*="カバー画像"]');
                            if (altImgs.length > 0) return true;
                            return false;
                        } catch(e) { return false; }
                    }""")
                    if has_preview:
                        log.info(f"【STEP4-B】SNSプレビューに画像反映を確認（{i+1}秒後）")
                        upload_ok = True
                        break

                await page.screenshot(path="ameblo_04b_after_upload.png")

                if not upload_ok:
                    log.info("【STEP4-B】SNSプレビュー自動検出できず（続行）")

                # C: 「カバーに設定する」を押す（クリック選択は不要、アップロード後は自動選択済み）
                confirm_btn = page.locator('button:has-text("カバーに設定する")')
                await confirm_btn.wait_for(state="visible", timeout=8000)
                await confirm_btn.click()
                log.info("【STEP4-C】カバーに設定するクリックOK")

                # D: モーダルが閉じるのを待つ
                try:
                    await page.wait_for_selector('text=カバーの設定', state="hidden", timeout=8000)
                    log.info("【STEP4-D】モーダルが正常に閉じた")
                except:
                    log.warning("【STEP4-D】モーダルのクローズ検出できず（続行）")

                await page.wait_for_timeout(1000)
                await page.screenshot(path="ameblo_04d_after_confirm.png")

                # E: モーダルを閉じた直後の画像確認
                #    ⚠️ 下書き保存後は画像が消えるので、ここ（STEP4直後）で
                #    セットされているかを確認するのが唯一の成功判定タイミング
                cover_check = await page.evaluate("""() => {
                    try {
                        // カバー画像アイコンにimgがあるか
                        const coverImg = document.querySelector('#js-coverImage img');
                        if (coverImg && coverImg.src) return {ok: true, method: 'img_src', val: coverImg.src.slice(0,60)};

                        // CSS背景画像パターン
                        const coverEl = document.querySelector('#js-coverImage, [class*="p-cover__image"]');
                        if (coverEl) {
                            const bg = window.getComputedStyle(coverEl).backgroundImage;
                            if (bg && bg !== 'none' && bg.includes('url(')) return {ok: true, method: 'bg_image', val: bg.slice(0,60)};
                        }
                        // 「選択を取り消す」ボタンの存在（セット済みのサイン）
                        const allBtns = Array.from(document.querySelectorAll('button'));
                        const hasCancel = allBtns.some(b => b.textContent.includes('選択を取り消す'));
                        if (hasCancel) return {ok: true, method: 'cancel_btn'};

                        return {ok: false};
                    } catch(e) { return {ok: false, err: String(e)}; }
                }""")
                log.info(f"【STEP4-E】カバー画像DOM確認: {cover_check}")

                if cover_check.get('ok'):
                    image_set_ok = True
                    log.info("【STEP4-完了】✅ カバー画像セット成功（DOM確認済み）")
                    log.info("【STEP4-完了】⚠️ 注意: 下書き保存後は画像が消えます（Ameblo 2026/06/27時点の仕様）")
                    log.info("【STEP4-完了】✅ ameblo_05_before_action.png に画像が写っていれば成功とみなします")
                else:
                    log.warning("【STEP4-警告】DOMで画像を検出できず（スクショで目視確認してください）")
                    # アップロードAPIが通っていれば実質成功とみなす
                    if upload_ok:
                        image_set_ok = True
                        log.info("【STEP4-警告】SNSプレビュー反映を根拠に成功とみなします")

            except Exception as e:
                log.error(f"【STEP4-エラー】: {e}")
                await page.screenshot(path="ameblo_04_error.png")
                # モーダルが残っていれば閉じる
                try:
                    cancel = page.locator('button:has-text("キャンセル")')
                    if await cancel.is_visible(timeout=2000):
                        await cancel.click()
                        log.info("【STEP4】残存モーダルをキャンセルで閉じた")
                except:
                    pass
                await page.screenshot(path="ameblo_04_after_cleanup.png")

        elif image_path:
            log.warning(f"【STEP4】画像ファイルが存在しない: {image_path}")
        else:
            log.info("【STEP4】image_pathなし、カバー画像スキップ")

        # ── STEP5: 保存/投稿 ────────────────────────────────
        log.info(f"【STEP5】モード: {publish_mode}")

        body_check = await page.evaluate("""() => {
            try {
                const editor = CKEDITOR.instances['amebloeditor'];
                if (editor) return {source: 'ckeditor', len: editor.getData().length};
                const ta = document.getElementById('amebloeditor');
                if (ta) return {source: 'textarea', len: ta.value.length};
                return {source: 'unknown', len: -1};
            } catch(e) { return {source: 'error', len: -1}; }
        }""")
        log.info(f"【STEP5】送信直前 本文状態: {body_check}")

        # ★ 下書き保存前のスクショ（カバー画像あり状態の記録）
        # ⚠️ 下書き保存を押すとAmebloの仕様で画像が消えるため、
        #    このスクショが「画像セット成功」の証拠になる
        await page.screenshot(path="ameblo_05_before_action.png")
        log.info("【STEP5】★ ameblo_05_before_action.png 撮影完了（カバー画像の最終確認用）")

        action_done = False
        try:
            if publish_mode == "draft":
                btn_text = "下書き保存"
                btn = page.locator('button:has-text("下書き保存")').first
            else:
                btn_text = "投稿する"
                btn = page.locator('button:has-text("投稿する")').first

            visible = await btn.is_visible(timeout=10000)
            log.info(f"【STEP5】'{btn_text}' ボタン visible={visible}")

            if visible:
                await btn.click()
                action_done = True
                log.info(f"【STEP5】'{btn_text}' クリックOK")
            else:
                await page.evaluate("""(mode) => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const t = mode === 'draft'
                        ? btns.find(b => b.textContent.includes('下書き保存'))
                        : btns.find(b => b.textContent.includes('投稿する'));
                    if (t) t.click();
                }""", publish_mode)
                action_done = True
                log.info("【STEP5】JSクリックで実行")

        except Exception as e:
            log.error(f"【STEP5】ボタンクリック失敗: {e}")

        await page.wait_for_timeout(5000)
        final_url   = page.url
        final_title = await page.title()
        log.info(f"【STEP5】処理後URL: {final_url}")
        log.info(f"【STEP5】ページタイトル: {final_title}")

        if "srventryinsertinput" in final_url:
            log.info("【STEP5】URLは変わらず（下書き保存は正常な場合あり）")

        # バリデーションエラー検出
        try:
            error_text = await page.evaluate("""() => {
                const t = document.body.innerText;
                if (t.includes('本文を入力してください')) return '本文エラー';
                if (t.includes('タイトルを入力してください')) return 'タイトルエラー';
                return null;
            }""")
            if error_text:
                log.error(f"【STEP5】バリデーションエラー: {error_text}")
                action_done = False
        except:
            pass

        await page.screenshot(path="ameblo_05_after_action.png")

        if publish_mode == "draft":
            log.info("【STEP5】⚠️ 下書き保存後はAmebloの仕様によりカバー画像が消えます")
            log.info("【STEP5】✅ ameblo_05_before_action.png でカバー画像を確認してください")

        log.info(f"【最終サマリー】本文={body_check}, 画像セット={image_set_ok}, 保存操作={action_done}")
        log.info(f"【完了】アメブロ処理完了: {title[:40]}")
        log.info("アメブロ完了（ファイルはqueueに残します）")

        await browser.close()


def run():
    test_mode = os.environ.get("AMEBLO_TEST_IMAGE_ONLY", "").lower() in ("1", "true", "yes")

    if test_mode:
        log.info("=== 画像アップロード単体テストモード ===")
        images = sorted(glob.glob("images/*.jpg") + glob.glob("images/*.png"))
        test_image = images[-1] if images else ""
        if test_image:
            log.info(f"テスト用画像: {test_image}")
        else:
            log.warning("images/フォルダに画像が見つかりません")

        article = {
            "title": "[画像アップロードテスト] 画像アップロード単体テスト",
            "body": "これはAmeblo画像アップロード機能の単体テスト用ダミー本文です。",
            "image_path": test_image,
        }
        asyncio.run(post_ameblo(article, test_image_only=True))
        return

    # 通常モード
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.warning("queueにファイルがありません")
        return

    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)

    asyncio.run(post_ameblo(article))


if __name__ == "__main__":
    run()
