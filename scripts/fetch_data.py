"""Download the tau2-bench files this repo reads, at one commit, and check each against its git blob hash.

    python scripts/fetch_data.py [DIR]      (default: data)

The files are not stored here: they are 7 to 11 MB each. tau2-bench is MIT licensed.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

COMMIT = "19f13ea3f5fba8b4c4d272d1353590f29dc53062"
RAW = f"https://raw.githubusercontent.com/sierra-research/tau2-bench/{COMMIT}/"

# repository path -> git blob sha1, read from the repository's tree at COMMIT
FILES = {
    "data/tau2/results/final/claude-3-7-sonnet-20250219_airline_default_gpt-4.1-2025-04-14_4trials.json": "aa5ff43544e0fb3bd49ce682ba9c0d7bad10e74a",
    "data/tau2/results/final/gpt-4.1-2025-04-14_airline_default_gpt-4.1-2025-04-14_4trials.json": "f4847076cbf6e509098d22fbf26ff26a7f2b4603",
    "data/tau2/results/final/gpt-4.1-mini-2025-04-14_airline_base_gpt-4.1-2025-04-14_4trials.json": "cfd3fc81927dab2ed942251ee5b4066e6a78cb1c",
    "data/tau2/results/final/o4-mini-2025-04-16_airline_default_gpt-4.1-2025-04-14_4trials.json": "83f3a42988689f8dd1c02c2b4dd7119b2af438a8",
    "data/tau2/domains/airline/tasks.json": "ea4ff5e3f5e3d97ca391846a6e8dd9eb3437dc80",
    "data/tau2/domains/airline/policy.md": "8098be7db3f47de943fad39d1e057ede6cc6cd04",
}


def blob_sha1(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def main(argv):
    out = Path(argv[1] if len(argv) > 1 else "data")
    out.mkdir(parents=True, exist_ok=True)
    for path, expected in FILES.items():
        target = out / Path(path).name
        if target.exists() and blob_sha1(target.read_bytes()) == expected:
            print("have", target.name)
            continue
        with urllib.request.urlopen(RAW + path) as response:
            content = response.read()
        got = blob_sha1(content)
        if got != expected:
            print(f"refusing {path}: blob {got}, expected {expected}", file=sys.stderr)
            return 1
        target.write_bytes(content)
        print("fetched", target.name, len(content), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
