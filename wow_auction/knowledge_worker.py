"""Weekly/patch source refresh; AI investigation jobs remain pending for stage 6."""
from pathlib import Path
import re
import time

from .knowledge import KnowledgeError
from .knowledge_att import scan_corpus
from .knowledge_sources import ATT_REVISION, fetch_corpus, resolve_latest_revision


def update_sources(store, cache_root, *, build, force=False, now=None,
                   resolve=None, fetch=None, scan=None):
    fixed_clock = now
    clock = lambda: int(time.time()) if fixed_clock is None else fixed_clock
    now = clock()
    resolve = resolve or resolve_latest_revision
    fetch = fetch or fetch_corpus
    scan = scan or scan_corpus
    active = store.db.execute("SELECT i.* FROM imports i JOIN active_sources a ON a.import_id=i.id WHERE a.source='att-standard'").fetchone()
    store.schedule_updates(build=build, now=now)
    if active is None or force:
        with store.transaction():
            store._enqueue('source_update', 'att-standard',
                           {'source': 'att-standard', 'reason': 'initial' if active is None else 'explicit',
                            'build': build, **({'requested_at': now} if active is not None else {})}, now)
    job = store.claim_job(kind='source_update', now=now, lease_seconds=3600)
    if job is None:
        return {'status': 'not_due_or_leased', 'paid_ai_called': False}
    def heartbeat(_):
        store.renew_job(job['id'], job['lease_token'], lease_seconds=3600, now=clock())
    try:
        if job['scope'] != 'att-standard':
            raise KnowledgeError('unsupported_catalog_source')
        revision = ATT_REVISION if active is None else resolve()['revision']
        if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision):
            raise KnowledgeError('invalid_pinned_revision')
        root = Path(cache_root).resolve() / revision
        previous_root = Path(cache_root).resolve() / active['revision'] if active else None
        capture = fetch(root, revision=revision, progress=heartbeat,
                        reuse_root=previous_root if previous_root and previous_root.is_dir() else None)
        if capture.get('complete') is not True:
            raise KnowledgeError('source_fetch_incomplete')
        heartbeat(None)
        corpus = scan(root, revision=revision, build=build)
        heartbeat(None)
        result = store.import_corpus(corpus, now=fixed_clock, fault=heartbeat)
        store.finish_job(job['id'], job['lease_token'], now=clock())
        return {'status': 'source_checked', 'revision': revision, 'import': result,
                'paid_ai_called': False, 'research_jobs_not_executed': True}
    except (OSError, ValueError, KeyError, TypeError):
        # Safe diagnostic, no response body, local path or user data in output.
        try:
            store.finish_job(job['id'], job['lease_token'], error_code='source_refresh_failed', now=clock())
        except KnowledgeError:
            return {'status': 'retry_scheduled', 'error_code': 'source_update_lease_lost', 'paid_ai_called': False}
        return {'status': 'retry_scheduled', 'error_code': 'source_refresh_failed', 'paid_ai_called': False}
