import os
import sys
import urllib.request

BASE_URL = "https://snap.stanford.edu/data"

FILES = [
    "soc-pokec-relationships.txt.gz",
    "soc-pokec-profiles.txt.gz",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")


def _progress_hook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 / total_size)
        mb_done = downloaded / (1024 * 1024)
        mb_total = total_size / (1024 * 1024)
        sys.stdout.write(f"\r  {pct:5.1f}%  ({mb_done:8.1f} MB / {mb_total:8.1f} MB)")
    else:
        mb_done = downloaded / (1024 * 1024)
        sys.stdout.write(f"\r  {mb_done:8.1f} MB downloaded")
    sys.stdout.flush()


def download_file(filename: str) -> None:
    url = f"{BASE_URL}/{filename}"
    dest_path = os.path.join(RAW_DIR, filename)

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
        print(f"[skip] {filename} already exists at {dest_path}")
        return

    print(f"[download] {url}")
    try:
        urllib.request.urlretrieve(url, dest_path, reporthook=_progress_hook)
        print()
        size_mb = os.path.getsize(dest_path) / (1024 * 1024)
        print(f"[done] {filename} ({size_mb:.1f} MB) -> {dest_path}")
    except Exception as e:
        print(f"\n[error] failed to download {filename}: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        sys.exit(1)


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    print(f"Saving files to: {RAW_DIR}\n")

    for filename in FILES:
        download_file(filename)

    print("\nAll files downloaded.")


if __name__ == "__main__":
    main()
