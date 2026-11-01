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
    source = 'news'
    attempt = pair.core.model_copy(update={'error': 'Failed' if failed else None})
    answer = Mock(return_value=attempt)
    monkeypatch.setattr(run, 'answer_question', answer)
    judge = Mock()
    judge.judge.return_value = pair.contribution
    result = run.run_pair(pair.question, pair.core_context, Mock(), judge, external_source=source)
    assert [
        call.kwargs['external_source']
        for call in answer.call_args_list
    ] == [None, source]
    assert result.external_source == source
    assert judge.judge.call_count == int(not failed)
    assert (result.contribution is None) == failed
    assert run.summarize([result])[f'impacts / {source}']['answer_failures'] == int(failed)


def test_judge_error_is_counted_separately(monkeypatch, pair):
    """
    Verify judge failures are counted separately from answer failures.
    """
    monkeypatch.setattr(run, 'answer_question', Mock(return_value=pair.core))
    judge = Mock()
    judge.judge.side_effect = ValueError('Judge failed')
    result = run.run_pair(pair.question, pair.core_context, Mock(), judge, external_source='news')
    counts = run.summarize([result])['impacts / news']
    assert counts['judge_failures'] == 1
    assert counts['evaluated'] == counts['answer_failures'] == 0


def test_saved_runs_accumulate_without_overwriting(tmp_path, pair):
    """
    Verify appending a batch preserves earlier results and updates the summary.
    """
    first = AnalysisRun(metadata={'batch': 1}, results=[pair])
    run.save_results(tmp_path, [first])
    runs = run.load_runs(tmp_path)
    news = pair.model_copy(update={
        'external_source': 'news', 'contribution': run.Contribution.QUALIFIES,
    })
    weather = pair.model_copy(update={
        'external_source': 'weather', 'contribution': run.Contribution.UNRESOLVED,
    })
    runs.append(AnalysisRun(metadata={'batch': 2}, results=[pair, news, weather]))
    run.save_results(tmp_path, runs)
    saved = json.loads(tmp_path.joinpath('results.json').read_text())
    assert saved['runs'][0] == first.model_dump(mode='json')
    assert saved['runs'][1]['metadata'] == {'batch': 2}
    summary = saved['summary']
    assert set(summary) == {'impacts / combined', 'impacts / news', 'impacts / weather'}
    assert summary['impacts / combined']['evaluated'] == 2
    assert summary['impacts / news']['categories']['qualifies'] == 1
    assert summary['impacts / news']['categories']['unresolved'] == 0
    assert summary['impacts / weather']['categories']['unresolved'] == 1
    assert summary['impacts / weather']['categories']['qualifies'] == 0
    assert tmp_path.joinpath('summary.svg').is_file()


def test_main_measures_each_source_against_shared_baseline(monkeypatch, tmp_path, pair):
    """
    Save a separate judgment for each external source.
    """
    set_seed = Mock()
    monkeypatch.setattr(run, 'set_seed', set_seed)
    monkeypatch.setattr(run, 'select_contexts', Mock(return_value=[pair.core_context]))
    monkeypatch.setattr(run, 'construct_gap_questions', Mock(return_value=[pair.question]))
    assistant = Mock()
    assistant.ask.return_value = pair.core.response
    monkeypatch.setattr(run, 'make_answerer', Mock(return_value=assistant))
    judge = Mock()
    judge.judge.side_effect = [run.Contribution.QUALIFIES, run.Contribution.UNRESOLVED]
    monkeypatch.setattr(run, 'LocalContributionJudge', Mock(return_value=judge))

    assert run.main([
        '--answer-model', run.MODEL_CHOICES[0], '--judge-model', run.MODEL_CHOICES[0],
        '--output-dir', str(tmp_path),
    ]) == 0

    assert [call.kwargs for call in assistant.ask.call_args_list] == [
        {'use_tools': True, 'enable_news': False, 'enable_weather': False},
        {'use_tools': True, 'enable_news': True, 'enable_weather': False},
        {'use_tools': True, 'enable_news': False, 'enable_weather': True},
    ]
    set_seed.assert_called_once_with(run.SEED)
    batch = run.load_runs(tmp_path)[0]
    assert batch.metadata['seed'] == run.SEED
    results = batch.results
    assert [(result.external_source, result.contribution) for result in results] == [
        ('news', run.Contribution.QUALIFIES), ('weather', run.Contribution.UNRESOLVED),
    ]
