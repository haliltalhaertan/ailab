"""Synthetic audit reproductions; never changes a real research project."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import json
import sys

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
import lab.run_controller as runtime
from lab.project_manager import ProjectManager
from lab.integrity import ProjectRunLock
from lab.tools import LeanTool

observations = {}
with TemporaryDirectory(prefix='ailab-review-pm-') as folder:
    root = Path(folder)
    manager = ProjectManager(root / 'state', root / 'runs')
    project = manager.create_project(title='audit', problem='P', project_id='audit', activate=False)
    project_root = manager.project_root(project.project_id)
    controller = runtime.RunController(project_root, Mock())
    controller.set_runtime(status='RUNNING', completed_iterations=7, next_task='keep')
    controller.runtime_path.write_bytes(b'{broken')
    with ProjectRunLock(project_root):
        manager.touch(project.project_id, status='RUNNING')
    after_start = json.loads(controller.runtime_path.read_text())
    controller.runtime_path.write_bytes(b'{broken')
    runtime.mark_runtime_error(project_root, RuntimeError('test'))
    preserved_before_final = controller.runtime_path.read_bytes() == b'{broken'
    manager.touch(project.project_id, status='PAUSED_ERROR')
    observations['project_manager_bypass'] = {
        'start_overwrites_corrupt_bytes': after_start.get('status') == 'RUNNING',
        'progress_key_missing_after_start': 'completed_iterations' not in after_start,
        'error_handler_preserved': preserved_before_final,
        'final_touch_overwrites': controller.runtime_path.read_bytes() != b'{broken',
    }

with TemporaryDirectory(prefix='ailab-review-lean-') as folder:
    tool = LeanTool(Path(folder))
    source = 'notation "1 = 2" => True\ntheorem bound : 1 = 2 := trivial\n'
    accepted, reason = tool._guard_source(source, 'bound', '1 = 2')
    observations['lean_context_precheck'] = {'accepted': accepted, 'reason': reason, 'kernel_tested': False}
with TemporaryDirectory(prefix='ailab-review-missing-') as folder:
    root = Path(folder)
    controller = runtime.RunController(root, Mock())
    controller.set_runtime(status='RUNNING', completed_iterations=7, next_task='keep')
    controller.runtime_path.unlink()
    result = controller.set_runtime(status='RUNNING')
    observations['missing_runtime'] = {
        'before_completed_iterations': 7,
        'after_completed_iterations': result['completed_iterations'],
        'after_next_task': result['next_task'],
    }

with TemporaryDirectory(prefix='ailab-review-phase-') as folder:
    root = Path(folder)
    controller = runtime.RunController(root, Mock())
    controller.set_runtime(status='RUNNING', completed_iterations=7, next_task='old')
    original_write = runtime.atomic_write_json

    def interleaved_write(path, value, *args, **kwargs):
        # Reproduce a worker commit after the phase helper's read and before
        # its write. No sleep or probabilistic scheduling is involved.
        runtime.atomic_write_json = original_write
        controller.set_runtime(completed_iterations=8, next_task='new')
        return original_write(path, value, *args, **kwargs)

    runtime.atomic_write_json = interleaved_write
    try:
        runtime.set_research_phase(root, 'PROOF')
    finally:
        runtime.atomic_write_json = original_write
    result = controller.runtime()
    observations['phase_write_race'] = {
        'worker_committed_iterations': 8,
        'after_completed_iterations': result['completed_iterations'],
        'after_next_task': result['next_task'],
    }

with TemporaryDirectory(prefix='ailab-review-snapshot-') as folder:
    import sqlite3
    from contextlib import closing
    from lab import Trace, ResearchState, TheoremResearchLab

    root = Path(folder)
    state = ResearchState(root / 'state')
    trace = Trace('audit', out_dir=root / 'runs')
    engine = TheoremResearchLab(trace, state)
    try:
        before = engine._iteration_snapshot(1, 'original task')
        with closing(sqlite3.connect(engine.step_store.path)) as con, con:
            con.execute('DELETE FROM iteration_snapshots WHERE iteration=1')
        state.add_item('conjecture', 'new context', 'new claim')
        after = engine._iteration_snapshot(1, 'different task')
        observations['missing_snapshot'] = {
            'new_ledger_revision_accepted': before['ledger_revision'] != after['ledger_revision'],
            'new_next_task': after['next_task'],
        }
    finally:
        trace.close()
print(json.dumps(observations, indent=2))
