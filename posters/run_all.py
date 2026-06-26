import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def run():
    log.info("=== AutoPoster 開始 ===")

    # 記事生成
    try:
        import generator.generate as gen
        gen.run()
        log.info("✅ 記事生成 完了")
    except Exception as e:
        log.error(f"❌ 記事生成 失敗: {e}")
        sys.exit(1)

    # note投稿
    try:
        import posters.note_poster as note
        note.run()
        log.info("✅ note 完了")
    except Exception as e:
        log.error(f"❌ note 失敗: {e}")

    # アメブロ投稿
    try:
        import posters.ameblo_poster as ameblo
        ameblo.run()
        log.info("✅ アメブロ 完了")
    except Exception as e:
        log.error(f"❌ アメブロ 失敗: {e}")

    # LinkedIn投稿
    try:
        import posters.linkedin_poster as linkedin
        linkedin.run()
        log.info("✅ LinkedIn 完了")
    except Exception as e:
        log.error(f"❌ LinkedIn 失敗: {e}")

    # Substack投稿
    try:
        import posters.substack_poster as substack
        substack.run()
        log.info("✅ Substack 完了")
    except Exception as e:
        log.error(f"❌ Substack 失敗: {e}")

    log.info("=== AutoPoster 完了 ===")

if __name__ == "__main__":
    run()