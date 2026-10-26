"""Fetch a complete pinned ATT author corpus; no credentials or Lua execution."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wow_auction.knowledge_sources import ATT_REVISION, fetch_corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path(".private/stage3/corpora-att"))
    parser.add_argument("--revision", default=ATT_REVISION)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--reuse-root", type=Path)
    args = parser.parse_args()
    result = fetch_corpus(args.destination, args.revision, args.workers, reuse_root=args.reuse_root,
                          progress=lambda value: print(json.dumps(value), flush=True))
    print(json.dumps(result, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
