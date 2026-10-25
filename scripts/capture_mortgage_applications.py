"""Opt-in capture of free issuer publications; never request licensed MBA data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from curl_cffi import requests

ROOT = Path(__file__).resolve().parents[1] / "tests/fixtures/macro_releases/mortgage_applications"
URLS = {
    "mba-2026-09-30.html": "https://www.mba.org/news-and-research/newsroom/news/2026/09/30/mortgage-applications-decrease-in-latest-mba-weekly-survey",
    "mba-2026-10-21.html": "https://www.mba.org/news-and-research/newsroom/news/2026/10/21/mortgage-applications-decrease-in-latest-mba-weekly-survey",
    "pali-history.xlsx": "https://www.fanniemae.com/mortgage-application-index/download",
    "pali-calendar.pdf": "https://www.fanniemae.com/media/document/pdf/fannie-mae-pali-rali-release-calendar.pdf",
    "pali-methodology.pdf": "https://www.fanniemae.com/media/document/pdf/fannie-mae-pali-rali-methodology.pdf",
    "pali-dictionary.pdf": "https://www.fanniemae.com/media/document/pdf/fannie-mae-pali-rali-data-dictionary.pdf",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="+", choices=sorted(URLS))
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    for name in args.names:
        url = URLS[name]
        response = requests.get(url, impersonate="chrome", timeout=40)
        response.raise_for_status()
        target = ROOT / name
        target.write_bytes(response.content)
        meta = dict(url=response.url, retrieved_at=datetime.now(timezone.utc).isoformat(),
                    sha256=hashlib.sha256(response.content).hexdigest())
        target.with_name(name + ".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(name, len(response.content))


if __name__ == "__main__":
    main()
