"""Import a complete local corpus and repeat it, writing new public count evidence.

No network/game writes. The database is explicitly selected and remains local.
Existing evidence is never overwritten. Run only with the project's own corpus.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wow_auction.knowledge_cli import protect_destination, read_json
from wow_auction.knowledge_storage import KnowledgeStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if args.evidence.exists():
        raise SystemExit('evidence_exists')
    protect_destination(args.database, Path('.private/config.local.json'))
    start = time.perf_counter()
    corpus = read_json(args.corpus)
    source_hash = hashlib.sha256(args.corpus.read_bytes()).hexdigest()
    evidence = {'captured_at': datetime.now(timezone.utc).isoformat(), 'kind': 'REAL_SOURCE_IMPORT_NOT_GAME_MECHANICS',
                'source_file_sha256': source_hash, 'revision': corpus['revision'], 'parser_revision': corpus['parser_revision'],
                'build': corpus['build'], 'file_count': len(corpus['files']), 'candidate_count': len(corpus['candidates']),
                'coverage': corpus['coverage']}
    with KnowledgeStore(args.database) as store:
        evidence['first'] = store.import_corpus(corpus, chunk_size=1000)
        after_first = {table: store.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                       for table in ('imports','entries','candidates','events','research_jobs')}
        evidence['repeat'] = store.import_corpus(corpus, chunk_size=1000)
        after_repeat = {table: store.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in after_first}
        assert evidence['repeat']['status'] == 'unchanged'
        assert after_first == after_repeat
        evidence['table_counts'] = after_repeat
        evidence['repeat_created_no_rows'] = True
        evidence['quick_check'] = store.db.execute('PRAGMA quick_check').fetchone()[0]
        evidence['foreign_key_errors'] = len(store.db.execute('PRAGMA foreign_key_check').fetchall())
        assert evidence['quick_check'] == 'ok' and evidence['foreign_key_errors'] == 0
        current = store.status()['catalogs'][0]
        evidence['received_at'] = current['received_at']
        evidence['available_at'] = current['available_at']
    evidence['database_bytes'] = args.database.stat().st_size
    evidence['elapsed_seconds'] = round(time.perf_counter()-start, 3)
    evidence['source_code_sha256'] = {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted(Path('wow_auction').glob('knowledge*.py'))}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    with args.evidence.open('x', encoding='utf-8') as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k:evidence[k] for k in ('file_count','candidate_count','first','repeat','database_bytes','elapsed_seconds')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
