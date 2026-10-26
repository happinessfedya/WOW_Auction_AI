"""Explicit REAL TSM probe for review; public GET only, temporary market DB.

Run from the repository root. No game files, asset database or secret file is
opened. Raw values exist only in the temporary DB, removed on completion.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wow_auction.market_config import load_market_config
from wow_auction.market_http import HttpClient
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker


class ObservedClient(HttpClient):
    def __init__(self):
        super().__init__()
        self.observations = []
    def request(self, url, **kwargs):
        response = super().request(url, **kwargs)
        headers = kwargs.get('headers', {})
        self.observations.append({'url': url, 'http_status': response.status,
            'authorization_sent': 'Authorization' in headers,
            'if_none_match': headers.get('If-None-Match'),
            'if_modified_since': headers.get('If-Modified-Since'), 'response_headers': response.headers,
            'received_at_ns': response.received_at_ns, 'elapsed_seconds': response.elapsed_seconds,
            'body_bytes': len(response.body), 'wire_bytes': response.wire_bytes})
        return response


def main():
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else 'docs/stage2-r1-live-evidence.json')
    if destination.exists():
        raise SystemExit('Evidence already exists; choose a new output name.')
    config = load_market_config('.private/market.local.json')
    client = ObservedClient()
    with tempfile.TemporaryDirectory(prefix='stage2_real_probe_', dir='.private') as directory:
        path = Path(directory) / 'market.sqlite3'
        with MarketStore(path) as store, \
                patch('wow_auction.market_worker.load_secrets', side_effect=AssertionError('Unexpected secret inspection')), \
                patch.dict(os.environ, {'WOW_UNDERMINE_API_KEY': 'SYNTHETIC\nBROKEN'}):
            worker = MarketWorker(store, config, client=client)
            started = time.perf_counter()
            first = worker.scan(force=True)
            keys = [key for key, _ in worker.jobs()]
            initial = store.report(keys)
            # Respect the app's 5 requests/second ceiling before the next batch.
            time.sleep(1.05)
            second = worker.scan(force=True)
            repeated = store.report(keys)
            identities = [dict(row) for row in store.db.execute(
                'SELECT v.job_key,v.snapshot_id,v.normalizer_revision,v.etag,v.last_modified,v.generation,'
                's.sha256,s.dataset,s.source_snapshot_at,s.received_at_ns,s.available_at_ns '
                'FROM http_validators v JOIN snapshots s ON s.id=v.snapshot_id')]
            snapshots = [dict(row) for row in store.db.execute(
                'SELECT id,dataset,sha256,record_count,length(raw) AS raw_bytes,'
                'length(CAST(normalized AS BLOB)) AS normalized_bytes,source_snapshot_at,received_at_ns,available_at_ns FROM snapshots')]
            evidence = {'kind': 'REAL', 'captured_at_utc': datetime.now(timezone.utc).isoformat(),
                'blizzard_requests': 0, 'secret_loader_calls': 0, 'synthetic_invalid_disabled_ue_environment': True,
                'asset_database_and_game_files_opened': False, 'requests': client.observations,
                'first': first, 'initial_status': initial, 'second': second, 'repeated_status': repeated,
                'validators': identities, 'snapshots': snapshots, 'elapsed_seconds': time.perf_counter()-started,
                'database_bytes': path.stat().st_size, 'schema_version': store.db.execute('PRAGMA user_version').fetchone()[0],
                'integrity': store.db.execute('PRAGMA quick_check').fetchone()[0],
                'foreign_key_errors': len(store.db.execute('PRAGMA foreign_key_check').fetchall()),
                'limitations': ['Immediate conditional retrieval is not measured natural cadence',
                                'No real PC sleep/wake or UE backfill', 'TSM aggregates only; no UE access'],
                'code_sha256': {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(Path('wow_auction').glob('market_*.py'))}}
    evidence['temporary_raw_database_removed'] = not path.exists()
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'first': first, 'second': second, 'request_statuses': [r['http_status'] for r in client.observations],
                      'snapshots': len(snapshots), 'elapsed_seconds': evidence['elapsed_seconds'],
                      'integrity': evidence['integrity'], 'temporary_database_removed': not path.exists()}, ensure_ascii=False))
    expected = [200] * 5 + [304] * 5
    return 0 if [r['http_status'] for r in client.observations] == expected and len(snapshots) == 5 else 2


if __name__ == '__main__':
    raise SystemExit(main())
