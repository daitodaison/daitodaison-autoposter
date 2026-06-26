import logging, sys, subprocess
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE = Path(__file__).parent.parent

def run_script(name, path):
    try:
        result = subprocess.run(
            [sys.executable, str(BASE / path)],
            capture_output=True, text=True, cwd=str(BASE)
        )
        if result.returncode == 0:
            log.info(f"✅ {name} 完了")
        else:
            log.error(f"❌ {name} 失敗: {result.stderr[-500:]}")
    except Exception as e:
        log.error(f"❌ {name} 例外: {e}")

def run():
    log.info("=== AutoPoster 開始 ===")
    run_script("note",     "posters/note_poster.py")
    run_script("アメブロ", "posters/ameblo_poster.py")
    run_script("LinkedIn", "posters/linkedin_poster.py")
    run_script("Substack", "posters/substack_poster.py")
    log.info("=== AutoPoster 完了 ===")

if __name__ == "__main__":
    run()
