"""Local ingestion, public markets and knowledge catalogs; no game controls."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

from .ingestion import ConfigError, import_source, init_config, load_config, reject_source_destination, status
from .storage import Store, StorageError, restore_backup
from .market_common import MarketError
from .knowledge import KnowledgeError
from .knowledge_cli import knowledge_command, register_commands
from .knowledge_craftsim import CraftSimError
from .knowledge_items import ItemMetadataError
from .knowledge_att import ATTError
from .lua_data import LuaDataError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="WoW Auction Intelligence: local read-only import")
    parser.add_argument("--config", type=Path, default=Path(".private/config.local.json"))
    parser.add_argument("--market-config", type=Path, default=Path(".private/market.local.json"))
    parser.add_argument("--knowledge-db", type=Path, default=Path(".private/knowledge.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="discover supported local SV and save private configuration")
    init.add_argument("--wow-root", type=Path, required=True)
    init.add_argument("--region", choices=["EU", "US", "KR", "TW", "CN"])
    commands.add_parser("scan", help="one stable read/atomic import per source")
    show = commands.add_parser("status", help="redacted diagnostic by default")
    show.add_argument("--details", action="store_true", help="print private names, money and item data locally")
    watch = commands.add_parser("watch", help="foreground local-file polling; Ctrl+C stops")
    watch.add_argument("--interval", type=float, default=10)
    watch.add_argument("--cycles", type=int, default=0, help="0 means until Ctrl+C")
    backup = commands.add_parser("backup", help="consistent SQLite backup to a new file")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore", help="restore into a new file; active configuration unchanged")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    link = commands.add_parser("link", help="append manual buy-to-sale quantity link; no inferred full PnL")
    link.add_argument("source_key")
    link.add_argument("buy_id")
    link.add_argument("sale_id")
    link.add_argument("quantity", type=int)
    link.add_argument("--note", default="")
    link.add_argument("--supersedes-id", type=int)
    unlink = commands.add_parser("unlink", help="append an explicit manual-link revocation, preserving history")
    unlink.add_argument("source_key")
    unlink.add_argument("link_id", type=int)
    unlink.add_argument("--note", default="")
    rebuild = commands.add_parser("rebuild-ledger-context", help="explicit context correction from raw history with mandatory backup")
    rebuild.add_argument("source_key")
    rebuild.add_argument("--backup", type=Path, required=True)
    rebuild.add_argument("--reason", required=True)
    market_init = commands.add_parser('market-init', help='create separate market configuration; no network request')
    market_init.add_argument('--region', choices=['EU', 'US', 'KR', 'TW'], default='EU')
    market_init.add_argument('--realm-slug', required=True)
    market_scan = commands.add_parser('market-scan', help='one due-job market polling pass')
    market_scan.add_argument('--force', action='store_true', help='refresh without bypassing provider backoff or quota')
    market_watch = commands.add_parser('market-watch', help='foreground durable market scheduler; no game required')
    market_watch.add_argument('--interval', type=float, default=10)
    market_watch.add_argument('--cycles', type=int, default=0)
    commands.add_parser('market-status', help='market scopes, capabilities, freshness and source health')
    commands.add_parser('market-resolve', help='report provider-native connected-realm evidence without Battle.net API')
    commands.add_parser('market-expire', help='enforce the recorded source retention deadlines')
    register_commands(commands)
    args = parser.parse_args(argv)
    try:
        if args.command.startswith('knowledge-'):
            return knowledge_command(args)
        if args.command.startswith('market-'):
            return _market_command(args)
        if args.command == "init":
            config = init_config(args.config, args.wow_root, region=args.region)
            print(json.dumps({"status": "configured", "sources": len(config["sources"])}))
            return 0
        config, sources = load_config(args.config)
        roots = [Path(p) for p in config["wow_roots"]]
        if args.command in {"backup", "restore"}:
            reject_source_destination(args.destination, roots)
        if args.command == "rebuild-ledger-context":
            reject_source_destination(args.backup, roots)
        if args.command == "restore":
            restore_backup(args.backup, args.destination)
            print(json.dumps({"status": "restored_to_new_database", "active_configuration_unchanged": True}))
            return 0
        with Store(config["database"]) as store:
            if args.command == "status":
                print(json.dumps(status(store, sources, details=args.details), ensure_ascii=False, indent=2))
            elif args.command == "backup":
                store.backup(args.destination)
                print(json.dumps({"status": "backup_verified"}))
            elif args.command == "link":
                result = store.add_link(args.source_key, args.buy_id, args.sale_id, args.quantity,
                                        args.note, args.supersedes_id)
                print(json.dumps({"status": "manual_overlay_added", "link_id": result, "pnl": None}))
            elif args.command == "unlink":
                store.revoke_link(args.source_key, args.link_id, args.note)
                print(json.dumps({"status": "manual_overlay_revoked", "link_id": args.link_id}))
            elif args.command == "rebuild-ledger-context":
                source = next((s for s in sources if s.key == args.source_key and s.addon == "TradeSkillMaster"), None)
                if source is None:
                    raise ConfigError("configured_ledger_source_required")
                result = store.rebuild_ledger_context(source.key, account_scope=source.account_scope,
                                                     region=source.region, adapter_version=source.adapter_version,
                                                     backup_path=args.backup, reason=args.reason)
                print(json.dumps(result))
            else:
                if args.command == "watch" and (not 1 <= args.interval <= 86400 or args.cycles < 0):
                    raise ConfigError("invalid_watch_parameters")
                cycles = 0
                while True:
                    results = [import_source(store, source, settle_seconds=config.get("settle_seconds", 0.1)) for source in sources]
                    print(json.dumps({"imports": results}, ensure_ascii=False), flush=True)
                    cycles += 1
                    if args.command == "scan" or (args.cycles and cycles >= args.cycles):
                        return 2 if any(r["status"] in {"quarantined", "historical_reappearance"}
                                        or r.get("ledger_context_status") == "requires_resolution" for r in results) else 0
                    time.sleep(args.interval)
        return 0
    except (ConfigError, StorageError, MarketError, KnowledgeError, CraftSimError, ItemMetadataError, ATTError, LuaDataError) as exc:
        print(json.dumps({"status": "error", "error_code": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"status": "error", "error_code": "local_storage_or_access_failure"}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "stopped"}))
        return 0


def _market_command(args):
    from .market_config import init_market_config, load_market_config
    from .market_policy import source_policy
    from .market_storage import MarketStore
    from .market_worker import MarketWorker
    if args.command == 'market-init':
        local, _ = load_config(args.config)
        result = init_market_config(args.market_config, region=args.region, realm_slug=args.realm_slug,
                                    wow_roots=[Path(p) for p in local['wow_roots']])
        print(json.dumps({'status': 'market_configured', 'jobs': len(result['jobs'])}))
        return 0
    config = load_market_config(args.market_config)
    if args.command == 'market-watch' and (not 1 <= args.interval <= 60 or args.cycles < 0):
        raise MarketError('invalid_market_watch_parameters')
    with MarketStore(config['database']) as store:
        if args.command == 'market-expire':
            print(json.dumps({'status': 'retention_enforced', 'expired': store.expire()}))
            return 0
        # Local retention is enforced on each market invocation, including
        # diagnostics. While the program is stopped no background eraser runs.
        store.expire()
        worker = MarketWorker(store, config)
        if args.command == 'market-resolve':
            result = worker.resolve_realm()
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['status'] == 'provider_membership_observed' else 2
        if args.command == 'market-status':
            jobs = worker.jobs()
            print(json.dumps({'stage': 2, 'jobs': store.report([key for key, _ in jobs]),
                              'policies': {p: source_policy(p) for p in ('tsm', 'blizzard', 'undermine')},
                              'credentials': worker.credential_diagnostics(),
                              'price_is_not_execution': True, 'trading_not_implemented': True}, ensure_ascii=False, indent=2))
            return 0
        cycles = 0
        while True:
            result = worker.scan(force=getattr(args, 'force', False))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            cycles += 1
            if args.command == 'market-scan' or args.cycles and cycles >= args.cycles:
                good = {'accepted', 'unchanged', 'not_due_or_leased', 'disabled'}
                return 2 if any(row['status'] not in good for row in result['imports']) else 0
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
