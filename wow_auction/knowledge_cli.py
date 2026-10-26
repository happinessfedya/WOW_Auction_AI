"""Stage 3 commands; no game control, trading or paid research calls."""
import json
import hashlib
from collections import Counter
from pathlib import Path
import time

from .file_reader import read_stable
from .knowledge import KnowledgeError
from .knowledge_storage import KnowledgeStore


def artifact_normalizer_revision(kind):
    """Parsing/build assessment changes require a new derived artifact revision."""
    module = {'craftsim': 'knowledge_craftsim.py', 'item_metadata': 'knowledge_items.py'}.get(kind)
    if module is None:
        raise KnowledgeError('unsupported_knowledge_artifact')
    checksum = hashlib.sha256(b'knowledge-artifact-v1\x00')
    files = ['knowledge_cli.py', 'knowledge.py', 'lua_data.py', module]
    if kind == 'craftsim':
        files.extend(('knowledge_engine_capture.py', 'knowledge_engine.py'))
    for name in files:
        checksum.update(name.encode() + b'\x00')
        checksum.update(Path(__file__).with_name(name).read_bytes())
    return checksum.hexdigest()


def register_commands(commands):
    commands.add_parser('knowledge-status', help='catalog versions, gaps and pending research')
    local = commands.add_parser('knowledge-local-scan', help='automatic read-only import of configured local addon caches')
    local.add_argument('--build', help='optional explicit build; otherwise detect installed Retail build')
    calculate = commands.add_parser('knowledge-calculate', help='recalculate observed CraftSim allocations using retained commodity observations')
    calculate.add_argument('--build', help='otherwise detect installed Retail build')
    calculate.add_argument('--details', action='store_true', help='include conditional calculations and exact item links')
    load = commands.add_parser('knowledge-import', help='resume a completely scanned corpus JSON')
    load.add_argument('corpus', type=Path)
    scan = commands.add_parser('knowledge-scan', help='scan an already downloaded pinned ATT tree')
    scan.add_argument('corpus_root', type=Path)
    scan.add_argument('--revision', required=True)
    scan.add_argument('--build', required=True)
    for name in ('knowledge-update', 'knowledge-watch'):
        update = commands.add_parser(name, help='public source update; no Blizzard API or paid AI')
        update.add_argument('--cache-root', type=Path, default=Path('.private/stage3/catalog-cache'))
        update.add_argument('--build', help='optional explicit build; otherwise detect installed Retail build')
        update.add_argument('--force', action='store_true', help='check upstream revision now')
        if name.endswith('watch'):
            update.add_argument('--interval', type=float, default=60)
            update.add_argument('--cycles', type=int, default=0)
    for name in ('knowledge-craftsim', 'knowledge-items'):
        imp = commands.add_parser(name, help='stable read of a local SavedVariables export')
        imp.add_argument('source', type=Path)
        imp.add_argument('--build', required=True)
    show = commands.add_parser('knowledge-candidates', help='page source claims; not trading recommendations')
    show.add_argument('source_id')
    show.add_argument('--limit', type=int, default=20)
    show.add_argument('--offset', type=int, default=0)
    rollback = commands.add_parser('knowledge-rollback', help='explicit catalog rollback, keeping history')
    rollback.add_argument('source_id')
    rollback.add_argument('import_id', type=int)
    rollback.add_argument('--reason', required=True)
    feedback = commands.add_parser('knowledge-feedback', help='queue evidence research; does not alter facts')
    feedback.add_argument('source_id')
    feedback.add_argument('key')
    feedback.add_argument('message')
    correction = commands.add_parser('knowledge-propose', help='save correction with independent current evidence')
    correction.add_argument('proposal', type=Path)
    select = commands.add_parser('knowledge-review', help='record an independent review decision or roll back an overlay')
    select.add_argument('source_id')
    select.add_argument('key')
    select.add_argument('correction_id', type=int, help='0 restores original source claim')
    select.add_argument('--review-reference', required=True)
    select.add_argument('--reason', required=True)


def read_json(path):
    raw = read_stable(path, max_bytes=256 * 1024 * 1024).data
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise KnowledgeError('duplicate_knowledge_json_key')
            result[key] = value
        return result
    def bad(_):
        raise KnowledgeError('nonfinite_knowledge_json')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
    except (ValueError, RecursionError):
        raise KnowledgeError('invalid_knowledge_json_file') from None


def protect_destination(path, config_path):
    from .ingestion import load_config, reject_source_destination
    if Path(config_path).exists():
        config, _ = load_config(config_path)
        reject_source_destination(Path(path), [Path(p) for p in config['wow_roots']])
    # Without configuration, known game data directories remain invalid targets.
    if any(part.lower() in {'wtf', 'interface'} for part in Path(path).resolve().parts):
        raise KnowledgeError('game_directory_is_read_only')


def knowledge_command(args):
    protect_destination(args.knowledge_db, args.config)
    with KnowledgeStore(args.knowledge_db) as store:
        if args.command == 'knowledge-status':
            result = store.status()
        elif args.command == 'knowledge-import':
            result = store.import_corpus(read_json(args.corpus))
        elif args.command == 'knowledge-scan':
            from .knowledge_att import scan_corpus
            result = store.import_corpus(scan_corpus(args.corpus_root, revision=args.revision, build=args.build))
        elif args.command == 'knowledge-local-scan':
            from .knowledge_local import LocalKnowledgePoller
            result = LocalKnowledgePoller(store, args.config, build=args.build).scan()
        elif args.command == 'knowledge-calculate':
            from .knowledge_local import LocalKnowledgePoller, detect_build
            from .knowledge_engine_jobs import EngineEvaluator
            build = args.build or detect_build(args.config)
            local = LocalKnowledgePoller(store, args.config, build=build).scan()
            result = EngineEvaluator(store, args.market_config).run(local, build=build, details=args.details)
        elif args.command in {'knowledge-update', 'knowledge-watch'}:
            from .knowledge_worker import update_sources
            from .knowledge_local import LocalKnowledgePoller, detect_build
            from .knowledge_sources import ATT_BUILD
            protect_destination(args.cache_root, args.config)
            if args.command == 'knowledge-watch' and (not 1 <= args.interval <= 60 or args.cycles < 0):
                raise KnowledgeError('invalid_knowledge_watch_parameters')
            cycles = 0
            local_poller = LocalKnowledgePoller(store, args.config, build=args.build)
            from .knowledge_engine_jobs import EngineEvaluator
            engine_evaluator = EngineEvaluator(store, args.market_config)
            while True:
                observed_build = args.build or detect_build(args.config)
                local_result = local_poller.scan(build=observed_build)
                previous = store.db.execute("SELECT i.build FROM imports i JOIN active_sources a ON a.import_id=i.id WHERE a.source='att-standard'").fetchone()
                catalog_build = observed_build or (previous[0] if previous else ATT_BUILD)
                result = update_sources(store, args.cache_root, build=catalog_build,
                                        force=args.force and cycles == 0)
                result['local'] = local_result
                result['craftsim_calculations'] = engine_evaluator.run(local_result, build=observed_build)
                result['build_context'] = {'value': catalog_build, 'installed_build_known': observed_build is not None,
                                          'basis': 'explicit' if args.build else 'local_build_info' if observed_build else 'last_known_or_source_reference'}
                print(json.dumps(result, ensure_ascii=False), flush=True)
                cycles += 1
                if args.command == 'knowledge-update' or args.cycles and cycles >= args.cycles:
                    return 2 if result.get('status') == 'retry_scheduled' else 0
                time.sleep(args.interval)
        elif args.command in {'knowledge-craftsim', 'knowledge-items'}:
            source = read_stable(args.source)
            observed_at = int(time.time())
            kind = 'craftsim' if args.command == 'knowledge-craftsim' else 'item_metadata'
            identity = {'source_sha256': source.sha256, 'normalizer_revision': artifact_normalizer_revision(kind),
                        'build': args.build}
            existing = store.find_artifact(kind, identity)
            if existing is None:
                if kind == 'craftsim':
                    from .knowledge_craftsim import parse_bridge
                    payload = parse_bridge(source.data, observed_at=observed_at, current_build=args.build)
                else:
                    from .knowledge_items import decode_tsm_item_info
                    from .lua_data import parse_saved_variables
                    payload = decode_tsm_item_info(parse_saved_variables(source.data), expected_build=args.build)
                    payload['source_sha256'] = source.sha256
                store.save_artifact(kind, payload, file_written_at_ns=source.file_written_at_ns, identity=identity)
            # Concurrent readers of the same source identity retain the first
            # committed payload/receive time, not their own later assessment.
            saved = store.find_artifact(kind, identity)
            payload = saved['payload']
            count = len(payload['profiles']) if kind == 'craftsim' else payload['row_count']
            result = {'status': 'artifact_unchanged' if existing else 'artifact_stored',
                      'kind': kind, 'artifact_id': saved['id'], 'records': count,
                      'source_sha256': source.sha256, 'recommendation_eligible': False}
            if kind == 'craftsim':
                from .knowledge_craftsim import assess_profile
                result['profile_statuses_now'] = dict(Counter(assess_profile(profile,
                    now=observed_at, current_build=args.build)['status'] for profile in payload['profiles']))
                result['rejected_records'] = len(payload['rejected'])
        elif args.command == 'knowledge-candidates':
            result = {'candidates': store.current_candidates(args.source_id, limit=args.limit, offset=args.offset)}
        elif args.command == 'knowledge-rollback':
            store.rollback(args.source_id, args.import_id, reason=args.reason)
            result = {'status': 'catalog_rollback_recorded'}
        elif args.command == 'knowledge-feedback':
            result = {'status': 'research_queued', 'fingerprint': store.feedback(args.source_id, args.key, args.message),
                      'paid_ai_implemented': False}
        elif args.command == 'knowledge-propose':
            proposal = read_json(args.proposal)
            if not isinstance(proposal, dict) or not {'source_id', 'key', 'candidate', 'evidence', 'reason'} <= proposal.keys():
                raise KnowledgeError('incomplete_correction_proposal')
            result = {'status': 'requires_independent_review',
                      'id': store.propose_correction(proposal['source_id'], proposal['key'], proposal['candidate'],
                                                     proposal['evidence'], reason=proposal['reason'])}
        elif args.command == 'knowledge-review':
            store.select_correction(args.source_id, args.key, args.correction_id or None,
                                    review_reference=args.review_reference, reason=args.reason)
            result = {'status': 'review_decision_recorded', 'recommendation_eligible': False}
        else:
            raise KnowledgeError('unsupported_knowledge_command')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
