"""Researcher-directed, isolated parallel experiments. No LLM calls or ledger writes."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import uuid
import zipfile

from lab.claim_check import MAX_POINTS, check_claim, integer, normalize_spec, spec_hash
from lab.code_experiment import GuardedExperimentWorkspace
from lab.integrity import ProjectRunLock, atomic_write_json, process_alive

ROOT = Path(__file__).resolve().parents[1] / 'batch_runs'
MAX_JOBS = 100
MAX_CHUNKS = 250
MAX_TOTAL_POINTS = 2_000_000


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def status(folder: Path) -> dict:
    data = read(folder / 'status.json')
    if data['state'] == 'RUNNING' and not process_alive(int(data.get('pid', 0))):
        data = {**data, 'state': 'INTERRUPTED', 'error': 'Çalıştırıcı durmuş. Kısmi sonuçlar korunuyor; kalan görevleri yeni paketle gönderin.'}
    return data


def batch_path(batch_id: str, root: Path = ROOT) -> Path:
    if not re.fullmatch(r'batch-[a-f0-9]{32}', batch_id):
        raise ValueError('Geçersiz paket kimliği')
    return root / batch_id


def windows(scope: dict) -> list[dict]:
    points = 1
    for bounds in scope.values():
        points *= bounds['max'] - bounds['min'] + 1
    if points <= MAX_POINTS:
        return [deepcopy(scope)]
    name = max(scope, key=lambda n: scope[n]['max'] - scope[n]['min'])
    mid = (scope[name]['min'] + scope[name]['max']) // 2
    left, right = deepcopy(scope), deepcopy(scope)
    left[name]['max'], right[name]['min'] = mid, mid + 1
    return windows(left) + windows(right)


def plan(request: dict) -> dict:
    if not isinstance(request, dict) or set(request) - {'title', 'workers', 'jobs'}:
        raise ValueError('Paket alanları: title, workers, jobs')
    workers = request.get('workers', 2)
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('Eşzamanlı iş sayısı 1–4 olmalı')
    jobs = request.get('jobs')
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= MAX_JOBS:
        raise ValueError('Bir pakette 1–100 görev olabilir')
    prepared, chunks, ids, total = [], [], set(), 0
    for raw in jobs:
        if not isinstance(raw, dict):
            raise ValueError('Görev bir nesne olmalı')
        job = deepcopy(raw)
        ident = job.get('id')
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident) or ident in ids:
            raise ValueError('Görev kimlikleri benzersiz olmalı: harf, rakam, alt çizgi veya tire')
        ids.add(ident)
        kind = job.setdefault('kind', 'claim_check')
        scopes: list[Any]
        if kind == 'claim_check':
            if set(job) - {'id', 'kind', 'claim_spec', 'scope', 'title'}:
                raise ValueError('Matematik görevi için bilinmeyen alan')
            spec = normalize_spec(job.get('claim_spec'))
            job['claim_spec'] = spec
            scope = deepcopy(job.get('scope', spec['variables']))
            if not isinstance(scope, dict) or set(scope) != set(spec['variables']):
                raise ValueError('Bütün değişkenler için sonlu kapsam gerekli')
            points = 1
            for name, bounds in scope.items():
                if not isinstance(bounds, dict) or set(bounds) != {'min', 'max'}:
                    raise ValueError('Kapsam min/max içermeli')
                low, high = integer(bounds['min']), integer(bounds['max'])
                domain = spec['variables'][name]
                if low > high or low < domain['min'] or (domain['max'] is not None and high > domain['max']):
                    raise ValueError('Kapsam iddianın tanım alanı dışında')
                points *= high - low + 1
            total += points
            if total > MAX_TOTAL_POINTS:
                raise ValueError('Paket en fazla 2 milyon aday noktası içerebilir')
            job['scope'] = scope
            job['spec_hash'] = spec_hash(spec)
            scopes = windows(scope)
        elif kind == 'python':
            if set(job) - {'id', 'kind', 'source', 'timeout_s', 'title'}:
                raise ValueError('Python görevi için bilinmeyen alan')
            if not isinstance(job.get('source'), str) or not 1 <= len(job['source'].encode()) <= 250_000:
                raise ValueError('Python kaynağı 1–250000 bayt olmalı')
            timeout = job.setdefault('timeout_s', 30)
            if type(timeout) is not int or not 1 <= timeout <= 60:
                raise ValueError('Python süresi 1–60 saniye olmalı')
            scopes = [None]
        else:
            raise ValueError('Desteklenen görev türleri: claim_check, python')
        prepared.append(job)
        for scope in scopes:
            chunks.append({'index': len(chunks), 'job_id': ident, 'scope': scope})
            if len(chunks) > MAX_CHUNKS:
                raise ValueError('Paket en fazla 250 çalışma parçası içerebilir')
    return {'title': str(request.get('title', 'Araştırmacı görevleri'))[:200], 'workers': workers,
            'jobs': prepared, 'chunks': chunks, 'candidate_points': total}


def create(request: dict, root: Path = ROOT) -> Path:
    prepared = plan(request)
    root.mkdir(parents=True, exist_ok=True)
    folder = root / ('batch-' + uuid.uuid4().hex)
    folder.mkdir()
    atomic_write_json(folder / 'request.json', request)
    atomic_write_json(folder / 'plan.json', prepared)
    files = ['batch_lab.py', 'claim_check.py', 'code_experiment.py', 'integrity.py', 'tools.py']
    (folder / 'code').mkdir()
    for name in files:
        (folder / 'code' / name).write_bytes((Path(__file__).parent / name).read_bytes())
    atomic_write_json(folder / 'manifest.json', {'request_hash': digest(request), 'plan_hash': digest(prepared),
        'created_at': now(), 'code_hashes': {n: hashlib.sha256((Path(__file__).parent / n).read_bytes()).hexdigest() for n in files}})
    atomic_write_json(folder / 'status.json', {'state': 'QUEUED', 'completed': 0, 'total': len(prepared['chunks']), 'updated_at': now()})
    return folder


def execute_chunk(folder_text: str, job: dict, chunk: dict) -> dict:
    folder = Path(folder_text)
    result_dir = folder / 'tasks' / f"{chunk['index']:04d}"
    result_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(result_dir / 'input.json', {'job': job, 'chunk': chunk})
    started = now()
    try:
        if (folder / 'cancel').exists():
            result = {'ok': False, 'error': 'CANCELLED', 'metadata': {}}
        elif job['kind'] == 'claim_check':
            result = check_claim(job['claim_spec'], {'scope': chunk['scope']}, item_id=job['id'],
                                 claim_hash=digest(job), iteration=chunk['index'] + 1).as_dict()
        else:
            ws = GuardedExperimentWorkspace(result_dir / 'workspace', timeout_s=job['timeout_s'],
                memory_limit_mb=256, max_output_bytes=1_048_576,
                cancel_check=lambda: (folder / 'cancel').exists())
            action = ws.write_file('experiment.py', job['source'])
            if action.ok:
                action = ws.run_python('experiment.py')
            result = action.as_dict()
    except Exception as exc:
        result = {'ok': False, 'error': str(exc), 'metadata': {}}
    receipt = {'chunk': chunk, 'job_hash': digest(job), 'started_at': started, 'finished_at': now(),
               'worker_pid': os.getpid(), 'result': result}
    atomic_write_json(result_dir / 'result.json', receipt)
    return receipt


def summarize(prepared: dict, receipts: list[dict]) -> list[dict]:
    summaries = []
    for job in prepared['jobs']:
        expected = [c for c in prepared['chunks'] if c['job_id'] == job['id']]
        found = [r for r in receipts if r['chunk']['job_id'] == job['id']]
        state, checked, witness = 'INCONCLUSIVE', 0, None
        valid, seen = [], set()
        for r in found:
            c = r['chunk']
            if c not in expected or c['index'] in seen or r['job_hash'] != digest(job):
                raise ValueError('Sonuç kimliği veya kapsamı eşleşmiyor')
            seen.add(c['index'])
            result, meta = r['result'], r['result'].get('metadata', {})
            if job['kind'] == 'claim_check':
                bound = (result.get('ok') and meta.get('claim_replayed') is True
                         and meta.get('claim_spec_hash') == job['spec_hash']
                         and meta.get('claim_hash') == digest(job) and meta.get('item_id') == job['id']
                         and meta.get('iteration') == c['index'] + 1 and meta.get('covered_scope') == c['scope'])
                valid.append(bool(bound and meta.get('kind') == 'EXACT_PASS'))
                if bound:
                    checked += int(meta.get('checked_points', 0))
                    if meta.get('kind') == 'DETERMINISTIC_COUNTEREXAMPLE':
                        witness = meta.get('witness')
            else:
                valid.append(bool(result.get('ok')))
        complete = len(found) == len(expected)
        if witness is not None:
            state = 'COUNTEREXAMPLE'
        elif complete and all(valid):
            state = 'FINITE_PASS' if job['kind'] == 'claim_check' else 'EXECUTION_ONLY'
        summaries.append({'id': job['id'], 'kind': job['kind'], 'state': state,
            'completed_chunks': len(found), 'total_chunks': len(expected), 'checked_points': checked,
            'requested_scope': job.get('scope'), 'scope_complete': complete and all(valid), 'witness': witness,
            'proof': False, 'novelty': 'UNASSESSED'})
    return summaries


def run(folder: Path) -> dict:
    # One active batch per root prevents several UI submissions multiplying the CPU cap.
    try:
        with ProjectRunLock(folder.parent), ProjectRunLock(folder):
            if read(folder / 'status.json')['state'] != 'QUEUED':
                raise ValueError('Bu paket daha önce başlatılmış; yeni paket oluşturun')
            prepared, manifest = read(folder / 'plan.json'), read(folder / 'manifest.json')
            if digest(prepared) != manifest['plan_hash'] or digest(read(folder / 'request.json')) != manifest['request_hash']:
                raise ValueError('Dondurulmuş görev paketi değiştirilmiş')
            for name, expected in manifest['code_hashes'].items():
                if hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() != expected:
                    raise ValueError('Çalıştırıcı sürümü değişmiş; yeni paket oluşturun')
            receipts, pending = [], {}
            jobs = {j['id']: j for j in prepared['jobs']}
            todo = iter(prepared['chunks'])
            def save(state: str) -> dict:
                data = {'state': state, 'completed': len(receipts), 'total': len(prepared['chunks']),
                    'updated_at': now(), 'pid': os.getpid(), 'jobs': summarize(prepared, receipts),
                    'model_calls': 0, 'model_cost_usd': 0}
                atomic_write_json(folder / 'status.json', data)
                return data
            save('RUNNING')
            with ProcessPoolExecutor(max_workers=prepared['workers'], mp_context=multiprocessing.get_context('spawn')) as pool:
                exhausted = False
                while pending or not exhausted:
                    while len(pending) < prepared['workers'] and not exhausted:
                        if (folder / 'cancel').exists():
                            exhausted = True
                            break
                        chunk = next(todo, None)
                        if chunk is None:
                            exhausted = True
                            break
                        pending[pool.submit(execute_chunk, str(folder), jobs[chunk['job_id']], chunk)] = chunk
                    if not pending:
                        break
                    done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                    for future in done:
                        pending.pop(future)
                        receipts.append(future.result())
                    save('RUNNING')
            final = save('CANCELLED' if (folder / 'cancel').exists() else 'COMPLETED')
            atomic_write_json(folder / 'summary.json', final)
            return final
    except Exception as exc:
        # Never overwrite another live worker's state on duplicate dispatch.
        from lab.integrity import ProjectBusyError
        if isinstance(exc, ProjectBusyError) and read(folder / 'status.json')['state'] == 'RUNNING':
            raise
        if read(folder / 'status.json')['state'] in {'COMPLETED', 'CANCELLED'}:
            raise
        atomic_write_json(folder / 'status.json', {'state': 'ERROR', 'error': str(exc), 'updated_at': now()})
        raise


def launch(folder: Path) -> int:
    kwargs: dict[str, Any] = {'cwd': str(Path(__file__).resolve().parents[1]), 'stdin': subprocess.DEVNULL,
                              'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB
    else:
        kwargs['start_new_session'] = True
    command = [sys.executable, '-m', 'lab.batch_lab', 'run', str(folder.resolve())]
    try:
        proc = subprocess.Popen(command, **kwargs)
    except OSError:
        if os.name != 'nt':
            raise
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(command, **kwargs)
    return proc.pid


def export(folder: Path) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and not path.is_symlink() and path.name not in {'run.lock', '.run.guard'}:
                archive.write(path, path.relative_to(folder))
    return stream.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['submit', 'run', 'status', 'cancel'])
    parser.add_argument('path', help='submit: request JSON; others: batch directory')
    parser.add_argument('--background', action='store_true')
    args = parser.parse_args()
    folder = Path(args.path).resolve()
    if args.action == 'submit':
        folder = create(read(folder))
        if args.background:
            launch(folder)
        else:
            run(folder)
        print(folder)
    elif args.action == 'run':
        run(folder)
    elif args.action == 'cancel':
        (folder / 'cancel').touch()
    else:
        print(json.dumps(status(folder), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
