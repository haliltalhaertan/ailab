"""Build existing strict contracts from a small form; no provider calls."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.directed_contract import DirectedTaskContract, safe_relative

KINDS = {
    'İspat adımını denetle': 'proof_review',
    'Bağımsız türetim yap': 'analytic_derivation',
    'Karşı örnek ara': 'counterexample_search',
    'Sonuçları karşılaştır': 'package_audit',
    'Sonlu tam sayı kontrolü': 'exact_computation',
}


def input_manifest(root: str | Path, paths: list[str]) -> list[dict[str, Any]]:
    base = Path(root).resolve(strict=True)
    if not base.is_dir() or len(paths) > 32:
        raise ValueError('Girdi klasörü ve en fazla 32 açık dosya yolu gerekli.')
    records = []
    total = 0
    for name in paths:
        name = safe_relative(name)
        path = base / name
        if any(part.is_symlink() for part in [path, *path.parents] if part != base):
            raise ValueError('Girdi yolu sembolik bağlantı içeremez.')
        path = path.resolve(strict=True)
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError('Girdi dosyası izin verilen klasörün dışında.')
        total += path.stat().st_size
        if total > 16 * 1024 * 1024:
            raise ValueError('Bu formda toplam girdi sınırı 16 MB.')
        raw = path.read_bytes()
        records.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'required': True, 'mutable': False})
    return records


def build_task(*, kind: str, task_id: str, project_id: str, parent_task_id: str,
               parent_commit: str, objective: str, statement: str, quantifiers: str,
               definitions: list[str], excluded: list[str], expected_output: str,
               inputs: list[dict[str, Any]], model: str = '', producers: int = 2,
               auditor: bool = True, effort: str = 'low', completion_tokens: int = 4096,
               reasoning_cap: int | None = None, final_tokens: int = 0,
               input_price: float = 0, output_price: float = 0, cost_limit: float = 0.5,
               wall_seconds: int = 600, low: int = 1, high: int = 200,
               predicate: str = 'n*(n+1)%2 == 0', assumptions: list[str] | None = None) -> dict:
    if kind not in KINDS or not 1 <= producers <= 8:
        raise ValueError('Görev türü veya üretici sayısı geçersiz.')
    if not objective.strip():
        raise ValueError('Yapılacak işi belirtin.')
    if not expected_output.strip():
        raise ValueError('Beklenen teslimi belirtin.')
    native = kind == 'Sonlu tam sayı kontrolü'
    lanes: list[dict[str, Any]]
    if native:
        from lab.claim_check import MAX_POINTS, normalize_spec
        if high < low or high - low + 1 > MAX_POINTS:
            raise ValueError(f'Bu tek kol en fazla {MAX_POINTS} nokta kontrol eder. Daha büyük işler için Yerel hesaplama kullanın.')
        spec = normalize_spec({'variables': {'n': {'min': low, 'max': high}},
                               'assumptions': assumptions or [], 'predicate': predicate})
        lanes = [{'lane_id': 'exact', 'role': 'ExactComputer', 'execution': 'claim_check', 'claim_spec': spec}]
        # The form must not label a finite check as proving an unrelated user claim.
        statement = f'{predicate}; n tam sayı, {low} <= n <= {high}; varsayımlar: {assumptions or []}'
        quantifiers = f'Yalnız [{low}, {high}] aralığındaki tam sayılar'
        definitions = ['n tam sayıdır.', *(assumptions or [])]
    else:
        common: dict[str, Any] = {'execution': 'model', 'model': model.strip(), 'input_paths': [x['path'] for x in inputs],
                  'max_completion_tokens': completion_tokens, 'max_wall_seconds': min(wall_seconds, 3600),
                  'input_price_per_million': input_price, 'output_price_per_million': output_price}
        if reasoning_cap is not None:
            common.update(max_reasoning_tokens=reasoning_cap, min_final_answer_tokens=final_tokens)
        else:
            common['reasoning_effort'] = effort
            if final_tokens:
                common['min_final_answer_tokens'] = final_tokens
        lanes = [{**common, 'lane_id': f'producer-{i+1}', 'role': KINDS[kind],
                  'independence_group': f'producer-{i+1}'} for i in range(producers)]
        if auditor:
            lanes.append({**common, 'lane_id': 'audit', 'role': 'Auditor',
                          'depends_on': [x['lane_id'] for x in lanes], 'can_read_other_lane_outputs': True,
                          'visible_inputs': ['task_contract', 'definitions', 'selected_artifacts']})
    count = len(lanes)
    token_limit = max(20000, completion_tokens + 12000)
    data = {
        'schema_version': '1.0', 'task_id': task_id.strip(), 'project_id': project_id.strip(),
        'parent_task_id': parent_task_id.strip(), 'parent_state_commit': parent_commit.strip(),
        'created_at': datetime.now(timezone.utc).isoformat(), 'created_by': 'task-builder', 'mode': 'directed_task',
        'objective': objective.strip() + '\nBeklenen teslim (findings içinde): ' + expected_output.strip(),
        'task_type': KINDS[kind],
        'primary_claim': {'claim_id': 'PRIMARY', 'statement': statement, 'status_at_start': 'OPEN',
                          'quantifiers': quantifiers, 'definitions': definitions},
        'scope': {'included': [quantifiers], 'excluded': excluded},
        'inputs': inputs, 'input_manifests': [], 'allowed_tools': ['claim_check'] if native else [],
        'forbidden_tools': ['network', 'canonical_write'] + (['model'] if native else []),
        'allowed_methods': ['bounded exact arithmetic'] if native else ['Analysis of the supplied definitions and evidence'],
        'forbidden_methods': ['autonomous task creation', 'scope expansion', 'claiming novelty without evidence'],
        'agent_plan': {'strategy': 'dag' if auditor and not native else 'parallel_independent',
                       'max_parallel_workers': 1 if native else producers, 'lanes': lanes},
        'budget': {'max_total_llm_calls': 0 if native else count, 'max_calls_per_lane': 0 if native else 1,
                   'max_total_tokens': 0 if native else count * token_limit,
                   'max_tokens_per_lane': 0 if native else token_limit,
                   'max_total_cost_usd': 0 if native else cost_limit, 'max_wall_seconds': wall_seconds,
                   'max_parallel_workers': 1 if native else producers, 'max_retries_per_call': 0},
        'stop_rules': ['BUDGET_EXHAUSTED', 'INPUT_INTEGRITY_FAILURE', 'USER_STOP'],
        'required_outputs': ['CLAIM_LEDGER.json', 'MASTER_FINDINGS.md'],
        'result_policy': {'allow_new_task_creation': False, 'allow_scope_expansion': False,
                          'allow_budget_expansion': False, 'allow_canonical_repository_write': False,
                          'unexpected_result_action': 'RECORD_AS_ESCALATION_CANDIDATE'},
    }
    return DirectedTaskContract.model_validate(data).model_dump(mode='json')
