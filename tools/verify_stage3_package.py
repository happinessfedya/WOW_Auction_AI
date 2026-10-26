"""Capture final local checks and package hashes; no network or game writes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import unquote
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    test_log = ROOT/'.private/stage3/final-package-unittest.log'
    test_text = test_log.read_text(encoding='utf-8-sig')
    match = re.search(r'Ran (\d+) tests in ([\d.]+)s', test_text)
    assert match and re.search(r'^OK\s*$', test_text, re.M) and 'FAILED' not in test_text
    watch_log = ROOT/'.private/stage3/final-autonomous-watch.log'
    watch = [json.loads(line) for line in watch_log.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    assert len(watch) == 2 and all(row['local']['game_actions_required'] is False for row in watch)
    assert all(row['status'] == 'file_unchanged' for row in watch[1]['local']['sources'])
    assert all(row['status'] == 'not_due_or_leased' and row['paid_ai_called'] is False for row in watch)
    source_files = sorted(p for directory in ('wow_auction', 'addons', 'tools') for p in (ROOT/directory).rglob('*')
                          if p.is_file() and p.suffix in {'.py', '.lua', '.toc'})
    hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in source_files}
    archive = ROOT/'docs/research/baselines/stage-2-r2.zip'
    unchanged = []
    review_hashes = {}
    with zipfile.ZipFile(archive) as old:
        for name in old.namelist():
            if name.startswith(('wow_auction/', 'tests/')) and name.endswith('.py'):
                if name in {'wow_auction/__main__.py', 'wow_auction/__init__.py'}:
                    continue
                assert (ROOT/name).read_bytes() == old.read(name), name
                unchanged.append(name)
            if name.startswith('docs/reviews/'):
                assert (ROOT/name).read_bytes() == old.read(name), name
                review_hashes[name] = sha(ROOT/name)
    addon_root = Path(r'W:\Battle.net\Games\World of Warcraft\_retail_\Interface\AddOns\WowAuctionBridge')
    installed = {}
    for name in ('Bridge.lua', 'Passive.lua', 'WowAuctionBridge.toc'):
        expected = hashes['addons/WowAuctionBridge/'+name]
        assert sha(addon_root/name) == expected
        installed[name] = expected
    # Read-only DB inspection does not migrate or modify any book.
    database = ROOT/'.private/knowledge.sqlite3'
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as db:
        quick = db.execute('PRAGMA quick_check').fetchone()[0]
        foreign = len(db.execute('PRAGMA foreign_key_check').fetchall())
        assert quick == 'ok' and foreign == 0
        counts = {table: db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                  for table in ('imports', 'entries', 'candidates', 'research_jobs', 'artifacts')}
        jobs = [dict(zip(('kind','status','count'), row)) for row in db.execute(
            'SELECT kind,status,COUNT(*) FROM research_jobs GROUP BY kind,status')]
    autonomy = {'captured_at': datetime.now(timezone.utc).isoformat(), 'kind': 'REAL_AUTONOMOUS_LOCAL_POLL',
                'command': 'python -B -m wow_auction knowledge-watch --cycles 2 --interval 1',
                'cycles': watch, 'log_sha256': sha(watch_log), 'watcher_left_running': False,
                'game_control_calls': 0, 'fresh_passive_game_runtime_verified': False,
                'installed_addon_version': '0.3.2', 'installed_addon_hashes': installed,
                'source_code_sha256': hashes}
    write_new(ROOT/'docs/stage3-autonomous-run-evidence.json', autonomy)
    report = {'captured_at': datetime.now(timezone.utc).isoformat(),
              'full_suite': {'tests': int(match[1]), 'seconds': float(match[2]), 'result': 'PASS',
                             'skipped': 0, 'log_sha256': sha(test_log), 'prior_suite': 377},
              'python': sys.version.split()[0], 'sqlite': sqlite3.sqlite_version,
              'accepted_stage2_r2_archive_sha256': sha(archive),
              'unchanged_prior_implementation': unchanged,
              'prior_code_changes': {'wow_auction/__main__.py': 'stage3 CLI wiring',
                                     'wow_auction/__init__.py': 'package version and module documentation'},
              'reviewer_owned_files_unchanged': review_hashes,
              'knowledge_database': {'schema': 1, 'quick_check': quick, 'foreign_key_errors': foreign,
                                     'bytes': database.stat().st_size, 'table_counts': counts, 'job_counts': jobs},
              'source_code_sha256': hashes, 'installed_addon_hashes': installed,
              'no_blizzard_web_api_or_credentials': True, 'manual_recipe_open_required': False,
              'paid_ai_calls': 0, 'stage4_started': False}
    write_new(ROOT/'docs/stage3-checks.json', report)
    manifest_path = ROOT/'docs/stage3-package-manifest.sha256'
    files = [ROOT/name for name in ('.gitignore','README.md','pyproject.toml')]
    for directory in ('wow_auction','tests','tools','addons','docs','codex_handoff'):
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                     and p.suffix not in {'.pyc','.pyo','.log'} and p != manifest_path)
    files = sorted(set(files))
    assert not any('.private' in p.parts or '.local.' in p.name for p in files)
    with manifest_path.open('x', encoding='utf-8') as stream:
        for path in files:
            stream.write(sha(path)+'  '+path.relative_to(ROOT).as_posix()+'\n')
    broken, checked = [], 0
    for path in files:
        if path.suffix != '.md' or 'reviews' in path.parts:
            continue
        for raw in re.findall(r'\]\(([^\n)]+)\)', path.read_text(encoding='utf-8-sig')):
            target = raw.strip().strip('<>').split('#',1)[0]
            if not target or target.startswith(('http:', 'https:', 'mailto:', 'plugin:', 'codex:')):
                continue
            target = unquote(target)
            # Ignore symbolic example placeholders; existing repo paths must resolve.
            if '<' in target or '>' in target:
                continue
            destination = Path(target) if re.match(r'^[A-Za-z]:[/\\]', target) else path.parent/target
            destination = Path(re.sub(r':\d+$', '', str(destination)))
            checked += 1
            if not destination.exists():
                broken.append({'file': path.relative_to(ROOT).as_posix(), 'target': target})
    print(json.dumps({'tests': int(match[1]), 'manifest_entries': len(files), 'manifest_sha256': sha(manifest_path),
                      'prior_files_unchanged': len(unchanged), 'local_links_checked': checked, 'broken_links': broken}, ensure_ascii=False))
    return 1 if broken else 0


if __name__ == '__main__':
    raise SystemExit(main())
