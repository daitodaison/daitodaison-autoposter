import json, os, asyncio, logging, shutil, glob
from pathlib import Path
from playwright.async_api import async_playwright

ARTICLES_DIR = "queue"
POSTED_DIR = "posted"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# 本文「変換」戦略（Markdown的なテキスト → note用HTML）
# ------------------------------------------------------------
# 環境変数 NOTE_BODY_STRATEGY で切り替える。
# 値を指定しない場合は "baseline"（=今まで公開していた基準ロジック）が使われる。
# 新しい変換ロジックを試したい時は、ここに関数を追加して
# BODY_STRATEGIES 辞書に登録するだけでよい。既存のbaselineには一切触れない。
# ============================================================

def body_strategy_baseline(text):
    """基準点（これまで公開してきたロジックそのまま）。絶対にここは壊さない。"""
    lines = text.split('\n')
    html = []
    for line in lines:
        line = line.rstrip()
        if line.startswith('### '):
            html.append(f'<h3>{line[4:]}</h3>')
        elif line.startswith('## '):
            html.append(f'<h2>{line[3:]}</h2>')
        elif line.startswith('# '):
            html.append(f'<h1>{line[2:]}</h1>')
        elif line.startswith('---'):
            html.append('<hr>')
        elif line.strip() == '':
            html.append('<p><br></p>')
        else:
            html.append(f'<p>{line}</p>')
    return '\n'.join(html)


def body_strategy_baseline_escaped(text):
    """baselineと同じ構造だが、HTMLエスケープを追加した版。
    記事本文に < > & などが含まれていた場合、baselineではそのまま
    HTMLタグとして解釈されてしまう可能性があるため、その対策版。
    挙動の違いを比較する目的の実験用。"""
    def esc(s):
        return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    lines = text.split('\n')
    html = []
    for line in lines:
        line = line.rstrip()
        if line.startswith('### '):
            html.append(f'<h3>{esc(line[4:])}</h3>')
        elif line.startswith('## '):
            html.append(f'<h2>{esc(line[3:])}</h2>')
        elif line.startswith('# '):
            html.append(f'<h1>{esc(line[2:])}</h1>')
        elif line.startswith('---'):
            html.append('<hr>')
        elif line.strip() == '':
            html.append('<p><br></p>')
        else:
            html.append(f'<p>{esc(line)}</p>')
    return '\n'.join(html)


def body_strategy_bold_italic(text):
    """baselineに、Markdownの**太字**と*斜体*の変換を追加した実験版。
    note側のProseMirrorエディタが<strong>/<em>を正しく解釈するかを試す。"""
    import re

    def inline_format(s):
        s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'(?<!\*)\*(.+?)\*(?!\*)', r'<em>\1</em>', s)
        return s

    lines = text.split('\n')
    html = []
    for line in lines:
        line = line.rstrip()
        if line.startswith('### '):
            html.append(f'<h3>{inline_format(line[4:])}</h3>')
        elif line.startswith('## '):
            html.append(f'<h2>{inline_format(line[3:])}</h2>')
        elif line.startswith('# '):
            html.append(f'<h1>{inline_format(line[2:])}</h1>')
        elif line.startswith('---'):
            html.append('<hr>')
        elif line.strip() == '':
            html.append('<p><br></p>')
        else:
            html.append(f'<p>{inline_format(line)}</p>')
    return '\n'.join(html)


BODY_STRATEGIES = {
    "baseline": body_strategy_baseline,
    "baseline_escaped": body_strategy_baseline_escaped,
    "bold_italic": body_strategy_bold_italic,
}


def get_body_strategy():
    """環境変数NOTE_BODY_STRATEGYで選択された変換戦略の関数を返す。
    未指定・不正値の場合は必ずbaselineにフォールバックする
    （実験中の事故で本番が壊れないようにするための安全策）。"""
    name = os.environ.get("NOTE_BODY_STRATEGY", "baseline").strip() or "baseline"
    fn = BODY_STRATEGIES.get(name)
    if fn is None:
        log.warning(f"【本文戦略】未知のNOTE_BODY_STRATEGY='{name}' → 'baseline'にフォールバック")
        return "baseline", BODY_STRATEGIES["baseline"]
    return name, fn


# ============================================================
# 本文「注入」戦略（変換済みHTMLを実際にProseMirrorエディタへ入れる方法）
# ------------------------------------------------------------
# 環境変数 NOTE_INJECT_STRATEGY で切り替える。
# 未指定時は "baseline"（=今まで公開していたexecCommand方式）。
# ============================================================

async def inject_strategy_baseline(page, body_html):
    """基準点（これまで公開してきた注入ロジックそのまま）。
    document.execCommand('insertHTML', ...) でProseMirrorに流し込む。"""
    await page.evaluate("""(html) => {
        const editor = document.querySelector('.ProseMirror');
        if (editor) {
            editor.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);
            document.execCommand('insertHTML', false, html);
        }
    }""", body_html)
    await asyncio.sleep(3)
    return True


async def inject_strategy_clipboard_paste(page, body_html):
    """実験版: クリップボード経由でペーストイベントを発火させる方式。
    execCommandがブラウザのバージョンによって非推奨/不安定なことがあるため、
    ClipboardEventを使った代替ルートを試す。"""
    try:
        await page.evaluate("""async (html) => {
            const editor = document.querySelector('.ProseMirror');
            if (!editor) return false;
            editor.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);

            const dt = new DataTransfer();
            dt.setData('text/html', html);
            const pasteEvent = new ClipboardEvent('paste', {
                clipboardData: dt,
                bubbles: true,
                cancelable: true
            });
            editor.dispatchEvent(pasteEvent);
            return true;
        }""", body_html)
        await asyncio.sleep(3)
        return True
    except Exception as e:
        log.warning(f"【本文注入:clipboard_paste】失敗: {e}")
        return False


async def inject_strategy_line_by_line(page, body_html):
    """実験版: 1行（1要素）ずつ挿入する方式。
    insertHTMLを一括で流すと長文で失敗しやすい場合の代替案として、
    ProseMirrorの段落ごとにinsertHTMLを繰り返す。"""
    import re
    # body_htmlは<p>...</p>や<h1>...</h1>などのブロック単位を仮定
    blocks = re.findall(r'<(?:p|h1|h2|h3|hr)[^>]*>.*?</(?:p|h1|h2|h3)>|<hr\s*/?>', body_html, re.DOTALL)
    if not blocks:
        blocks = [body_html]

    try:
        await page.evaluate("""() => {
            const editor = document.querySelector('.ProseMirror');
            if (editor) {
                editor.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('delete', false, null);
            }
        }""")
        for block in blocks:
            await page.evaluate("""(html) => {
                const editor = document.querySelector('.ProseMirror');
                if (editor) {
                    editor.focus();
                    document.execCommand('insertHTML', false, html);
                }
            }""", block)
            await asyncio.sleep(0.15)
        await asyncio.sleep(2)
        return True
    except Exception as e:
        log.warning(f"【本文注入:line_by_line】失敗: {e}")
        return False


INJECT_STRATEGIES = {
    "baseline": inject_strategy_baseline,
    "clipboard_paste": inject_strategy_clipboard_paste,
    "line_by_line": inject_strategy_line_by_line,
}


def get_inject_strategy():
    """環境変数NOTE_INJECT_STRATEGYで選択された注入戦略の関数を返す。
    未指定・不正値の場合は必ずbaselineにフォールバックする。"""
    name = os.environ.get("NOTE_INJECT_STRATEGY", "baseline").strip() or "baseline"
    fn = INJECT_STRATEGIES.get(name)
    if fn is None:
        log.warning(f"【本文注入戦略】未知のNOTE_INJECT_STRATEGY='{name}' → 'baseline'にフォールバック")
        return "baseline", INJECT_STRATEGIES["baseline"]
    return name, fn


async def post_note(article, body_only_test=False):
    """
    note投稿のメイン処理。

    body_only_test=True の場合:
      - STEP3(画像アップロード)はスキップする
      - STEP4(タイトル入力)は固定の短いテスト用タイトルにする
      - STEP5(本文入力)は環境変数で選んだ戦略を使い、結果をはっきり分かるログ・
        スクリーンショットに残す
      - STEP6は必ず下書き保存のみ
      - 本文の見た目の変化だけを高速に繰り返し確認したい時に使う
    """
    title = article.get("title", "無題")
    body = article.get("body", "")
    image_path = article.get("image_path", "")
    cookies_json = os.environ.get("NOTE_COOKIES", "")

    body_strategy_name, body_strategy_fn = get_body_strategy()
    inject_strategy_name, inject_strategy_fn = get_inject_strategy()

    if body_only_test:
        log.info(
            f"【テストモード】本文単体テストとして起動 "
            f"(body_strategy={body_strategy_name}, inject_strategy={inject_strategy_name})"
        )
        title = f"[本文テスト:{body_strategy_name}/{inject_strategy_name}] {title}"
        image_path = ""  # 画像処理は確実にスキップする

    if not cookies_json:
        log.error("NOTE_COOKIES が設定されていません")
        return False
    cookies = json.loads(cookies_json)
    pw_cookies = []
    for c in cookies:
        cookie = {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c.get("path", "/")}
        if "expirationDate" in c:
            cookie["expires"] = int(c["expirationDate"])
        if "secure" in c:
            cookie["secure"] = c["secure"]
        if "httpOnly" in c:
            cookie["httpOnly"] = c["httpOnly"]
        if c.get("sameSite") in ["Strict", "Lax", "None"]:
            cookie["sameSite"] = c["sameSite"].capitalize()
        pw_cookies.append(cookie)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        await context.add_cookies(pw_cookies)
        page = await context.new_page()

        # STEP1: エディタへアクセス
        log.info("【STEP1】noteエディタへアクセス中...")
        await page.goto("https://note.com/notes/new", wait_until="domcontentloaded", timeout=60000)
        await page.screenshot(path="note_01_after_goto.png")
        log.info(f"【STEP1】URL: {page.url}")

        # STEP2: エディタ起動待機
        log.info("【STEP2】エディタ起動を最大60秒待機...")
        for i in range(60):
            if "login" in page.url:
                await page.screenshot(path="note_02_login_error.png")
                log.error("【STEP2】ログインが外れています")
                await browser.close()
                return False
            found = await page.evaluate("""() => {
                const el = document.querySelector('textarea[placeholder*="タイトル"]');
                return el && el.getBoundingClientRect().height > 0;
            }""")
            if found:
                log.info(f"【STEP2】エディタ起動完了（{i+1}秒）")
                break
            await asyncio.sleep(1)
        else:
            await page.screenshot(path="note_02_timeout.png")
            log.error("【STEP2】エディタが起動しませんでした")
            await browser.close()
            return False

        await page.screenshot(path="note_02_editor_ready.png")
        log.info("【STEP2】エディタ起動後のスクリーンショット保存")

        # STEP3: 画像アップロード（本文単体テスト時はスキップ）
        if body_only_test:
            log.info("【STEP3】本文単体テストのため画像アップロードをスキップ")
        elif image_path and os.path.exists(image_path):
            log.info(f"【STEP3】画像アップロード開始: {image_path}")
            try:
                btns = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('button')).map(b => ({
                        text: b.textContent.trim(),
                        aria: b.getAttribute('aria-label'),
                        class: b.className.substring(0, 50)
                    }))
                }""")
                log.info(f"【STEP3】ページ上のボタン一覧: {btns[:10]}")

                await page.screenshot(path="note_03_before_img_btn.png")

                img_btn = page.locator('button[aria-label="画像を追加"]').first
                is_visible = await img_btn.is_visible(timeout=5000)
                log.info(f"【STEP3】画像ボタン表示状態: {is_visible}")

                if is_visible:
                    await img_btn.click()
                    await page.wait_for_timeout(2000)
                    await page.screenshot(path="note_03_after_img_btn_click.png")
                    log.info("【STEP3】画像ボタンクリック後のスクリーンショット保存")

                    upload_text = page.locator('text=画像をアップロード').first
                    is_upload_visible = await upload_text.is_visible(timeout=5000)
                    log.info(f"【STEP3】「画像をアップロード」表示状態: {is_upload_visible}")

                    if is_upload_visible:
                        async with page.expect_file_chooser(timeout=10000) as fc_info:
                            await upload_text.click()
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(image_path)
                        await page.wait_for_timeout(5000)
                        await page.screenshot(path="note_03_after_upload.png")
                        log.info("【STEP3】ファイル選択後のスクリーンショット保存")

                        save_btn = page.locator('button:has-text("保存")').first
                        is_save_visible = await save_btn.is_visible(timeout=10000)
                        log.info(f"【STEP3】保存ボタン表示状態: {is_save_visible}")
                        if is_save_visible:
                            await page.wait_for_timeout(2000)
                            clicked = await page.evaluate("""() => {
                                const modal = document.querySelector('.ReactModal__Overlay');
                                if (modal) {
                                    const btns = Array.from(modal.querySelectorAll('button'));
                                    const saveBtn = btns.find(b => b.textContent.trim() === '保存');
                                    if (saveBtn) { saveBtn.click(); return true; }
                                }
                                return false;
                            }""")
                            log.info(f"【STEP3】JS保存クリック結果: {clicked}")
                            await page.wait_for_timeout(5000)
                            await page.screenshot(path="note_03_after_save.png")
                            log.info("【STEP3】画像保存後のスクリーンショット保存")
                    else:
                        log.warning("【STEP3】「画像をアップロード」メニューが見つかりません")
                else:
                    log.warning("【STEP3】画像追加ボタンが見つかりません")
            except Exception as e:
                await page.screenshot(path="note_03_error.png")
                log.warning(f"【STEP3】画像アップロードエラー: {e}")
        else:
            log.info(f"【STEP3】画像スキップ（パス:{image_path} 存在:{os.path.exists(image_path) if image_path else False}）")

        # STEP4: タイトル入力
        log.info("【STEP4】タイトル入力...")
        title_area = page.locator('textarea[placeholder*="タイトル"]').first
        await title_area.fill(title)
        await page.screenshot(path="note_04_title.png")
        log.info("【STEP4】タイトル入力OK")

        # STEP5: 本文入力（選択された戦略を使用）
        log.info(f"【STEP5】本文入力... (変換戦略={body_strategy_name}, 注入戦略={inject_strategy_name})")
        body_html = body_strategy_fn(body)
        log.info(f"【STEP5】変換後HTML文字数: {len(body_html)} (元テキスト文字数: {len(body)})")

        inject_ok = await inject_strategy_fn(page, body_html)
        log.info(f"【STEP5】本文注入実行結果: inject_ok={inject_ok}")

        # 実際にエディタに反映された文字数を確認（baseline同様 innerText で見る）
        actual_len = await page.evaluate("""() => {
            const editor = document.querySelector('.ProseMirror');
            return editor ? editor.innerText.length : -1;
        }""")
        log.info(f"【STEP5】エディタ反映後の実際の文字数(innerText): {actual_len}")

        await page.screenshot(path=f"note_05_body_{body_strategy_name}_{inject_strategy_name}.png")
        log.info("【STEP5】本文入力完了")

        # STEP6: 下書き保存
        log.info("【STEP6】下書き保存...")
        try:
            draft_btn = page.locator('button:has-text("下書き保存")').first
            is_draft_visible = await draft_btn.is_visible(timeout=5000)
            log.info(f"【STEP6】下書き保存ボタン表示状態: {is_draft_visible}")
            if is_draft_visible:
                await draft_btn.click()
                await page.wait_for_timeout(3000)
                await page.screenshot(path="note_06_draft_saved.png")
                log.info("【STEP6】下書き保存OK")
        except Exception as e:
            await page.screenshot(path="note_06_error.png")
            log.warning(f"【STEP6】下書き保存エラー: {e}")

        await browser.close()
        log.info(
            f"【完了】note処理完了: {title} "
            f"(body_strategy={body_strategy_name}, inject_strategy={inject_strategy_name}, "
            f"inject_ok={inject_ok}, actual_len={actual_len})"
        )
        return True


def run():
    body_only_test = os.environ.get("NOTE_BODY_ONLY_TEST", "").lower() in ("1", "true", "yes")

    if body_only_test:
        # テストモード: queue/の記事をそのまま使うが、画像は使わず本文処理だけ確認する。
        # 記事はqueue/の最新1件を使う（中身を確認したい記事を入れ替えたい場合は
        # 手動でqueue/にテスト用jsonを置いてから実行する想定）。
        files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
        if files:
            with open(files[0], encoding="utf-8") as f:
                article = json.load(f)
            log.info(f"【テストモード】queue/の記事を使用: {files[0]}")
        else:
            # queueが空の場合は固定のダミー記事でテストする
            article = {
                "title": "本文テスト用ダミー記事",
                "body": (
                    "# 見出し1のテスト\n"
                    "\n"
                    "通常の段落テキストです。**太字**と*斜体*のテストも含みます。\n"
                    "\n"
                    "## 見出し2のテスト\n"
                    "\n"
                    "- リストではなく、ただの行として扱われるはずのテキストです。\n"
                    "\n"
                    "---\n"
                    "\n"
                    "区切り線の後の文章です。"
                ),
                "image_path": "",
            }
            log.info("【テストモード】queue/が空のため固定のダミー記事を使用")

        asyncio.run(post_note(article, body_only_test=True))
        log.info("【テストモード】完了（queue/には触れていません）")
        return

    # 通常モード（既存の本番フロー、一切変更なし）
    files = sorted(glob.glob(f"{ARTICLES_DIR}/*.json"))
    if not files:
        log.info("投稿待ち記事なし")
        return
    with open(files[0], encoding="utf-8") as f:
        article = json.load(f)
    asyncio.run(post_note(article))
    # ファイル移動はしない（他の媒体も同じファイルを使うため）
    log.info(f"note完了（ファイルはqueueに残します）: {files[0]}")

if __name__ == "__main__":
    run()
