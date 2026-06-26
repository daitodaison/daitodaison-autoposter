import logging, sys, os, subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def run_script(name, path):
    try:
        result = subprocess.run([sys.executable, path], capture_output=True, text=True)
        if result.returncode == 0:
            log.info(f"✅ {name} 完了")
        else:
            log.error(f"❌ {name} 失敗: {result.stderr[-300:]}")
    except Exception as e:
        log.error(f"❌ {name} 例外: {e}")

def run():
    log.info("=== AutoPoster 開始 ===")
    run_script("記事生成", "generator/generate.py")
    run_script("note", "posters/note_poster.py")
    run_script("アメブロ", "posters/ameblo_poster.py")
    run_script("LinkedIn", "posters/linkedin_poster.py")
    run_script("Substack", "posters/substack_poster.py")
    log.info("=== AutoPoster 完了 ===")

if __name__ == "__main__":
    run()