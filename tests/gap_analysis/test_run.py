"""
Tests for paired execution and accumulated results.
"""

import json
from unittest.mock import Mock

import pytest

from monty_tool.gap_analysis import run
from monty_tool.gap_analysis.schemas import AnalysisRun


@pytest.mark.parametrize('failed', [False, True])
def test_pair_uses_both_modes_and_skips_failed_answers(monkeypatch, pair, failed):
    """
    Verify both access modes run and failed answers skip judging.
    """
    attempt = pair.core.model_copy(update={'error': 'Failed' if failed else None})
    answer = Mock(return_value=attempt)
    monkeypatch.setattr(run, 'answer_question', answer)
    judge = Mock()
    judge.judge.return_value = pair.contribution
    result = run.run_pair(pair.question, pair.core_context, Mock(), judge)
    assert [
        call.kwargs['enable_external'] for call in answer.call_args_list
    ] == [False, True]
    assert judge.judge.call_count == int(not failed)
    assert (result.contribution is None) == failed
    assert run.summarize([result])['impacts']['answer_failures'] == int(failed)


def test_judge_error_is_counted_separately(monkeypatch, pair):
    """
    Verify judge failures are counted separately from answer failures.
    """
    monkeypatch.setattr(run, 'answer_question', Mock(return_value=pair.core))
    judge = Mock()
    judge.judge.side_effect = ValueError('Judge failed')
    result = run.run_pair(pair.question, pair.core_context, Mock(), judge)
    counts = run.summarize([result])['impacts']
    assert counts['judge_failures'] == 1
    assert counts['evaluated'] == counts['answer_failures'] == 0


def test_saved_runs_accumulate_without_overwriting(tmp_path, pair):
    """
    Verify appending a batch preserves earlier results and updates the summary.
    """
    first = AnalysisRun(metadata={'batch': 1}, results=[pair])
    run.save_results(tmp_path, [first])
    runs = run.load_runs(tmp_path)
    runs.append(AnalysisRun(metadata={'batch': 2}, results=[pair]))
    run.save_results(tmp_path, runs)
    saved = json.loads(tmp_path.joinpath('results.json').read_text())
    assert saved['runs'][0] == first.model_dump(mode='json')
    assert saved['runs'][1]['metadata'] == {'batch': 2}
    assert saved['summary']['impacts']['evaluated'] == 2
    assert tmp_path.joinpath('summary.svg').is_file()
