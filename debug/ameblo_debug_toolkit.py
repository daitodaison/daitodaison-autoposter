# -*- coding: utf-8 -*-
"""
ameblo_debug_toolkit.py
=======================
Amebloの仕様変更・不具合調査で使ったデバッグ・検証コードの保管庫。
LinkedIn / Substack 等の他プラットフォーム対応時にも流用可能。

【解決した事例】2026/06/27
  問題: カバー画像が設定されない
  原因: CKEditorのDOM構造、Reactのcontrolled input問題、
        下書き保存で画像が消えるAmeblo仕様バグ
  解決: CKEditor公式API使用 + before_action時点のスクショで成功判定

目次:
  A. DOM構造調査ツール
  B. ファイルアップロード検証
  C. ネットワーク監視
  D. CKEditor操作検証
  E. モーダル操作パターン集
  F. 成功/失敗判定ロジック集
  G. LinkedIn用テンプレート（未実装）
  H. Substack用テンプレート（未実装）
"""

import json, os, asyncio, logging, glob
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ============================================================
# A. DOM構造調査ツール
# ============================================================

async def inspect_dom_at_selector(page, selector: str, label: str = ""):
    """
    指定セレクタ周辺のDOM構造を詳細ログ出力する。
    仕様変更時に「今どんな構造になっているか」を素早く把握するために使う。

    使用例（Amebloで実績あり）:
        await inspect_dom_at_selector(page, '#js-coverSelect', 'カバー選択ボタン')
        await inspect_dom_at_selector(page, '#js-input-files', 'アップロードinput')
        await inspect_dom_at_selector(page, 'button:has-text("カバーに設定する")', '確定ボタン')

    LinkedIn等への転用:
        await inspect_dom_at_selector(page, 'input[name="session_key"]', 'ログインinput')
        await inspect_dom_at_selector(page, '[class*="share-box"]', '投稿ボックス')
    """
    log.info(f"=== DOM調査: {label or selector} ===")
    try:
        result = await page.evaluate(f"""(sel) => {{
            const el = document.querySelector(sel);
            if (!el) return {{found: false}};
            const rect = el.getBoundingClientRect();
            return {{
                found: true,
                tagName: el.tagName,
                id: el.id,
                className: el.className,
                type: el.getAttribute('type'),
                name: el.getAttribute('name'),
                disabled: el.disabled,
                hidden: el.hidden,
                display: window.getComputedStyle(el).display,
                visibility: window.getComputedStyle(el).visibility,
                opacity: window.getComputedStyle(el).opacity,
                width: rect.width,
                height: rect.height,
                top: rect.top,
                left: rect.left,
                parentId: el.parentElement?.id,
                parentClass: el.parentElement?.className?.slice(0, 80),
                innerHTML_preview: el.innerHTML.slice(0, 200)
            }};
        }}""", selector)
        log.info(f"  DOM結果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    except Exception as e:
        log.warning(f"  DOM調査失敗: {e}")


async def find_all_inputs(page, label: str = ""):
    """
    ページ上の全input/button要素を列挙する。
    「どこに何のinputがあるか分からない」時の初動調査に使う。

    実績: #js-input-files の発見に使用（Ameblo カバー画像）
    """
    log.info(f"=== 全input/button列挙: {label} ===")
    result = await page.evaluate("""() => {
        const inputs = Array.from(document.querySelectorAll('input, button, textarea'));
        return inputs.map(el => ({
            tag: el.tagName,
            id: el.id,
            name: el.getAttribute('name'),
            type: el.getAttribute('type'),
            class: el.className?.slice(0, 60),
            text: el.textContent?.trim().slice(0, 30),
            visible: el.offsetWidth > 0 && el.offsetHeight > 0
        })).filter(el => el.id || el.name || el.text);
    }""")
    for item in result:
        log.info(f"  {item}")


async def inspect_iframe_dom(page, iframe_selector: str, inner_selector: str):
    """
    iframe内のDOM構造を調査する。
    CKEditorのWYSIWYGエリアはiframe内にあるため専用関数が必要。

    実績: Amebloのエディタ構造調査
        iframe.cke_wysiwyg_frame > body[contenteditable="true"]
    """
    log.info(f"=== iframe DOM調査: {iframe_selector} > {inner_selector} ===")
    try:
        frame = page.frame_locator(iframe_selector)
        content = await frame.locator(inner_selector).inner_html(timeout=5000)
        log.info(f"  iframe内容: {content[:300]}")
    except Exception as e:
        log.warning(f"  iframe調査失敗: {e}")


# ============================================================
# B. ファイルアップロード検証
# ============================================================

async def debug_file_upload(page, input_selector: str, file_path: str):
    """
    ファイルアップロードの詳細検証。
    set_input_files後にブラウザ側で本当にファイルが認識されたかを確認する。

    実績: Amebloで input.files.length === 0 問題を発見
        → Reactのcontrolled input問題（set_input_filesだけでは不十分）を特定

    転用: LinkedInの画像添付、Substackのサムネイル設定等
    """
    log.info(f"=== ファイルアップロード検証: {input_selector} ===")

    # ファイル存在確認（Python側）
    import os
    exists = os.path.exists(file_path)
    size   = os.path.getsize(file_path) if exists else 0
    log.info(f"  ファイル事前確認: 存在={exists}, サイズ={size}bytes, path={file_path}")

    if not exists:
        log.error(f"  ファイルが存在しない: {file_path}")
        return False

    # input要素の属性確認
    attrs = await page.evaluate(f"""(sel) => {{
        const el = document.querySelector(sel);
        if (!el) return {{found: false}};
        return {{
            found: true,
            type: el.type,
            accept: el.accept,
            multiple: el.multiple,
            disabled: el.disabled,
            className: el.className,
            files_before: el.files.length
        }};
    }}""", input_selector)
    log.info(f"  input属性: {attrs}")

    # set_input_files実行
    try:
        await page.locator(input_selector).set_input_files(file_path)
        log.info("  set_input_files実行完了")
    except Exception as e:
        log.error(f"  set_input_files失敗: {e}")
        return False

    # ブラウザ側でファイルが認識されたか確認
    files_check = await page.evaluate(f"""(sel) => {{
        const el = document.querySelector(sel);
        if (!el) return {{found: false}};
        return {{
            count: el.files.length,
            name: el.files[0]?.name,
            size: el.files[0]?.size,
            type: el.files[0]?.type
        }};
    }}""", input_selector)
    log.info(f"  ブラウザ側 input.files: {files_check}")

    if files_check.get('count', 0) == 0:
        log.warning("  input.files が空 → set_input_filesがブラウザに反映されていない")
        log.warning("  → Reactのcontrolled inputの可能性。expect_file_chooser方式を試すべき")
        return False

    log.info("  ✅ ファイルアップロード確認OK")
    return True


async def try_file_chooser_upload(page, trigger_selector: str, file_path: str, force: bool = False):
    """
    ボタンクリック→ファイル選択ダイアログ方式のアップロード。
    set_input_filesが効かない場合（Reactのcontrolled input等）に使う。

    実績: noteのカバー画像アップロードで成功
    転用: LinkedIn / Substackの画像添付

    Args:
        trigger_selector: クリックするボタンのセレクタ
        file_path: アップロードするファイルのパス
        force: Trueにすると is_visible チェックをスキップ（隠しボタン対応）
    """
    log.info(f"=== file_chooser方式アップロード: {trigger_selector} ===")
    try:
        trigger = page.locator(trigger_selector).first
        async with page.expect_file_chooser(timeout=10000) as fc_info:
            if force:
                await trigger.click(force=True, timeout=8000)
            else:
                await trigger.click(timeout=8000)
        file_chooser = await fc_info.value
        await file_chooser.set_files(file_path)
        log.info("  ✅ file_chooser方式でファイルセット完了")
        return True
    except Exception as e:
        log.error(f"  file_chooser方式失敗: {e}")
        return False


# ============================================================
# C. ネットワーク監視
# ============================================================

def setup_network_monitor(page, keywords: list = None):
    """
    特定キーワードを含むURLへのリクエスト/レスポンスを監視する。
    アップロードAPIが本当に叩かれているか、何が返ってきたかを確認。

    実績: Amebloで POST /api/editor/image/upload が200で成功していることを発見
    転用: LinkedInのAPI、SubstackのAPI監視

    使い方:
        responses = []
        setup_network_monitor(page, keywords=['upload', 'image', 'media'])
        # ... 操作 ...
        # レスポンスはresponses変数に蓄積される
    """
    if keywords is None:
        keywords = ['upload', 'image', 'media', 'api']

    responses = []

    async def on_response(response):
        url = response.url
        if any(kw in url.lower() for kw in keywords):
            entry = {
                'url': url[:100],
                'status': response.status,
                'method': response.request.method,
            }
            try:
                body = await response.text()
                entry['body_preview'] = body[:200]
            except:
                entry['body_preview'] = '(取得失敗)'
            responses.append(entry)
            log.info(f"  ネットワーク: {entry['method']} {entry['status']} {url[:80]}")
            if entry['body_preview']:
                log.info(f"  レスポンスボディ: {entry['body_preview']}")

    page.on('response', on_response)
    log.info(f"ネットワーク監視開始: キーワード={keywords}")
    return responses  # 呼び出し側でこの変数を参照することで蓄積されたデータを取得


async def wait_for_api_response(page, url_keyword: str, status: int = 200, timeout_sec: int = 15):
    """
    特定のAPIレスポンスを待機する。

    使い方:
        success = await wait_for_api_response(page, 'image/upload', 200, 15)

    転用: LinkedIn投稿API、Substack下書きAPI等の応答待ち
    """
    log.info(f"APIレスポンス待機: {url_keyword} (最大{timeout_sec}秒)")
    for i in range(timeout_sec):
        await page.wait_for_timeout(1000)
        # ページのJSからレスポンスを確認する方法は限られるため
        # 実際にはsetup_network_monitorと組み合わせて使う
        log.info(f"  待機中... {i+1}/{timeout_sec}秒")
    log.warning(f"APIレスポンス待機タイムアウト: {url_keyword}")
    return False


# ============================================================
# D. CKEditor操作検証
# ============================================================

async def debug_ckeditor(page, instance_name: str = 'amebloeditor'):
    """
    CKEditorインスタンスの状態を詳細確認する。
    CKEditorが存在しない/正しく初期化されていない場合の診断に使う。

    実績: Ameblo本文入力でCKEditorを発見し、setData()で解決
    転用: CKEditorを使う他のブログサービス全般
    """
    log.info(f"=== CKEditor診断: {instance_name} ===")
    result = await page.evaluate(f"""() => {{
        if (!window.CKEDITOR) return {{exists: false, reason: 'CKEDITOR未定義'}};
        const instances = Object.keys(window.CKEDITOR.instances);
        const editor = window.CKEDITOR.instances['{instance_name}'];
        return {{
            exists: true,
            all_instances: instances,
            target_exists: !!editor,
            status: editor?.status,
            mode: editor?.mode,
            data_length: editor ? editor.getData().length : -1,
            data_preview: editor ? editor.getData().slice(0, 100) : null
        }};
    }}""")
    log.info(f"  CKEditor状態: {json.dumps(result, ensure_ascii=False)}")
    return result


async def set_ckeditor_data(page, html_content: str, instance_name: str = 'amebloeditor'):
    """
    CKEditorに本文をセットする。
    Ameblo実績コード。他のCKEditor採用サービスにそのまま転用可能。

    転用候補:
        - CKEditor採用のWordPressプラグイン
        - 各種CMSのリッチテキストエディタ
    """
    log.info(f"CKEditor setData開始: {len(html_content)}文字")
    try:
        await page.wait_for_function(
            f"() => window.CKEDITOR && window.CKEDITOR.instances && window.CKEDITOR.instances['{instance_name}']",
            timeout=15000
        )
        result = await page.evaluate(f"""(html) => {{
            try {{
                const editor = CKEDITOR.instances['{instance_name}'];
                if (!editor) return {{ok: false, reason: 'no instance'}};
                editor.setData(html);
                editor.updateElement();  // 裏のtextareaにも同期
                return {{ok: true, len: editor.getData().length}};
            }} catch(e) {{ return {{ok: false, reason: String(e)}}; }}
        }}""", html_content)
        log.info(f"  setData結果: {result}")
        return result.get('ok', False)
    except Exception as e:
        log.error(f"  CKEditor setData失敗: {e}")
        return False


# ============================================================
# E. モーダル操作パターン集
# ============================================================

async def open_modal_and_wait(page, trigger_selector: str, modal_text: str, timeout: int = 8000):
    """
    ボタンをクリックしてモーダルが開くのを待つ汎用関数。

    実績: Amebloの「カバーの設定」モーダル
    転用: LinkedIn / Substackの各種ダイアログ
    """
    log.info(f"モーダルを開く: trigger={trigger_selector}, 待機テキスト='{modal_text}'")
    try:
        btn = page.locator(trigger_selector).first
        await btn.wait_for(state="visible", timeout=5000)
        await btn.click()
        await page.wait_for_selector(f'text={modal_text}', state="visible", timeout=timeout)
        log.info(f"  ✅ モーダル開いた: '{modal_text}'")
        return True
    except Exception as e:
        log.error(f"  モーダルopen失敗: {e}")
        return False


async def close_modal_safely(page, close_patterns: list = None):
    """
    モーダルを安全に閉じる（複数のパターンを試す）。
    モーダルが残留してSTEP5を妨害する問題の対策。

    実績: Amebloで「カバーの設定」モーダルの残留でSTEP5が30秒ブロックされた
    転用: 全プラットフォーム共通
    """
    if close_patterns is None:
        close_patterns = [
            'button:has-text("キャンセル")',
            'button:has-text("閉じる")',
            'button:has-text("Cancel")',
            'button:has-text("Close")',
            '[class*="close"]',
            '[class*="Close"]',
            '[aria-label="閉じる"]',
            '[aria-label="Close"]',
        ]
    for pattern in close_patterns:
        try:
            btn = page.locator(pattern).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                log.info(f"  モーダルをクローズ: {pattern}")
                await page.wait_for_timeout(500)
                return True
        except:
            continue
    log.warning("  モーダルのクローズに使えるボタンが見つからなかった")
    return False


# ============================================================
# F. 成功/失敗判定ロジック集
# ============================================================

async def check_cover_image_set(page):
    """
    カバー画像がセットされているかを複数の方法で確認する。

    【⚠️ Ameblo 2026/06/27 仕様注意】
        下書き保存後は画像が消えるため、下書きを押す「前」に呼ぶこと。
        公開後は画像が保持される。
        下書き前のスクショ(ameblo_05_before_action.png)に画像が写っていれば成功とみなす。

    転用: 他プラットフォームでカバー/サムネイル確認に使える
    """
    result = await page.evaluate("""() => {
        // パターン1: img要素のsrc
        const coverImg = document.querySelector('#js-coverImage img');
        if (coverImg?.src) return {ok: true, method: 'img_src', val: coverImg.src.slice(0,60)};

        // パターン2: CSS背景画像
        const coverEl = document.querySelector('#js-coverImage, [class*="p-cover__image"]');
        if (coverEl) {
            const bg = window.getComputedStyle(coverEl).backgroundImage;
            if (bg && bg !== 'none' && bg.includes('url('))
                return {ok: true, method: 'bg_image', val: bg.slice(0,60)};
        }

        // パターン3: 「選択を取り消す」ボタンの存在（セット済みのサイン）
        const btns = Array.from(document.querySelectorAll('button'));
        if (btns.some(b => b.textContent.includes('選択を取り消す')))
            return {ok: true, method: 'cancel_btn'};

        // パターン4: SNSプレビューのalt属性
        const altImgs = document.querySelectorAll('img[alt*="カバー画像"]');
        if (altImgs.length > 0)
            return {ok: true, method: 'alt_attr'};

        return {ok: false};
    }""")
    log.info(f"カバー画像確認結果: {result}")
    return result.get('ok', False)


async def check_validation_errors(page):
    """
    送信後のバリデーションエラーを検出する。

    実績: Amebloで「本文を入力してください」モーダルをスルーしていた問題を発見
    転用: 全プラットフォーム共通（エラーテキストをプラットフォームごとに調整）
    """
    patterns = {
        'ameblo': ['本文を入力してください', 'タイトルを入力してください'],
        'note':   ['本文を入力してください', 'タイトルを入力してください'],
        'linkedin': ['Add a description', 'Something went wrong'],
        'substack': ['Please add a title', 'Error'],
    }
    all_patterns = [p for ps in patterns.values() for p in ps]

    result = await page.evaluate("""(patterns) => {
        const text = document.body.innerText;
        return patterns.filter(p => text.includes(p));
    }""", all_patterns)

    if result:
        log.error(f"バリデーションエラー検出: {result}")
        return result
    return []


# ============================================================
# G. LinkedIn用テンプレート（2026/06/27 時点で未実装）
# ============================================================
#
# 【既知の問題】
#   input[name="session_key"] のタイムアウト（30秒）エラーが発生中
#   原因候補:
#     1. LinkedInがログインページのDOM構造を変更した
#     2. Bot検知でCAPTCHAにリダイレクトされている
#     3. Cookie/認証情報が切れている
#
# 【調査方法】
#   1. inspect_dom_at_selector(page, 'input[name="session_key"]', 'ログイン') で構造確認
#   2. find_all_inputs(page, 'ログインページ') で全inputを列挙
#   3. ページのスクショを撮って目視確認
#
# async def post_linkedin(article):
#     ...
#     await inspect_dom_at_selector(page, 'input[name="session_key"]', 'ログインinput')
#     # 上記の結果でfound=Falseなら、ページ全体のHTML構造が変わっている
#     ...

# ============================================================
# H. Substack用テンプレート（2026/06/27 時点で未実装）
# ============================================================
#
# 【調査ポイント】
#   - エディタの種類（CKEditor? ProseMirror? Tiptap?）を特定する
#     → window.CKEDITOR があればCKEditor（set_ckeditor_data()が使える）
#     → なければ inspect_dom_at_selector で contenteditable 要素を特定
#   - 画像アップロードのendpointを network_monitor で確認
#
# async def post_substack(article):
#     ...
#     responses = setup_network_monitor(page, keywords=['upload', 'image'])
#     # エディタ特定
#     ck_check = await debug_ckeditor(page, 'some_instance_name')
#     if ck_check['exists']:
#         await set_ckeditor_data(page, body_html)
#     else:
#         # contenteditable方式（noteと同じ）
#         await page.evaluate("el => el.innerHTML = html", ...)
#     ...


# ============================================================
# 実行例（単体デバッグとして使う場合）
# ============================================================

async def run_debug_session(url: str, cookies_json: str = ""):
    """
    対話的デバッグセッション。
    問題が起きているページに直接アクセスして各ツールを試す。
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1280, "height": 900})

        if cookies_json:
            try:
                cookies = json.loads(cookies_json)
                await context.add_cookies(cookies)
            except:
                pass

        page = await context.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        log.info(f"=== デバッグセッション開始: {url} ===")

        # ここに調査したいツールを追加していく
        await find_all_inputs(page, "ページ全体")
        await page.screenshot(path="debug_session.png")

        await browser.close()
        log.info("デバッグセッション終了")


if __name__ == "__main__":
    # 例: Amebloの投稿ページを調査
    # asyncio.run(run_debug_session(
    #     "https://blog.ameba.jp/ucs/entry/srventryinsertinput.do",
    #     os.environ.get("AMEBLO_COOKIES", "")
    # ))
    log.info("このファイルは直接実行するのではなく、各関数をimportして使ってください")
    log.info("例: from ameblo_debug_toolkit import inspect_dom_at_selector, debug_ckeditor")
