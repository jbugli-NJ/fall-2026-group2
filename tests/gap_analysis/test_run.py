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
    Retain all runs and group by model IDs within the current rubric.
    """
    first = AnalysisRun(metadata={
        'batch': 1, 'rubric_version': run.RUBRIC_VERSION,
        'answer_model': {'id': 'first-model'},
        'judge_model': {'id': 'judge-model'},
    }, results=[pair])
    run.save_results(tmp_path, [first])
    runs = run.load_runs(tmp_path)
    news = pair.model_copy(update={
        'external_source': 'news', 'contribution': run.Contribution.QUALIFIES,
    })
    weather = pair.model_copy(update={
        'external_source': 'weather', 'contribution': run.Contribution.UNRESOLVED,
    })
    runs.append(AnalysisRun(metadata={
        'batch': 2, 'rubric_version': run.RUBRIC_VERSION,
        'answer_model': {'id': 'second-model'},
        'judge_model': {'id': 'judge-model'},
    }, results=[pair, news, weather]))
    runs.extend([
        first.model_copy(),
        AnalysisRun(metadata={
            **first.metadata, 'judge_model': {'id': 'other-judge'},
        }, results=[pair]),
    ])
    unsupported = pair.model_copy(update={'contribution': run.Contribution.UNSUPPORTED})
    runs.extend([
        AnalysisRun(metadata={'rubric_version': 'old-rubric'}, results=[unsupported]),
        AnalysisRun(metadata={}, results=[unsupported]),
    ])
    run.save_results(tmp_path, runs)
    saved = json.loads(tmp_path.joinpath('results.json').read_text())
    assert saved['runs'] == [batch.model_dump(mode='json') for batch in runs]
    assert saved['summary_rubric_version'] == run.RUBRIC_VERSION
    summary = {
        (group['answer_model']['id'], group['judge_model']['id']): {
            (counts['template_id'], counts['external_source']): counts
            for counts in group['results']
        }
        for group in saved['summary']
    }
    assert set(summary) == {
        ('first-model', 'judge-model'),
        ('second-model', 'judge-model'),
        ('first-model', 'other-judge'),
    }
    first_counts = summary[('first-model', 'judge-model')]
    assert first_counts[('impacts', 'combined')]['evaluated'] == 2
    second_counts = summary[('second-model', 'judge-model')]
    assert {
        source: (counts['improved'], counts['evaluated'])
        for (_, source), counts in second_counts.items()
    } == {'combined': (1, 1), 'news': (1, 1), 'weather': (0, 1)}
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


def test_improvement_rate_excludes_confirmation_and_failed_evaluations(pair):
    """
    Count supported improvements among evaluated pairs and retain failed attempts.
    """
    results = [pair.model_copy(update={'contribution': category}) for category in run.Contribution]
    results.append(pair.model_copy(update={'contribution': None, 'judge_error': 'Failed'}))
    counts = run.summarize(results)['impacts / combined']
    assert counts['attempted'] == 8
    assert counts['evaluated'] == 7
    assert counts['improved'] == 3
    assert counts['improvement_rate'] == pytest.approx(3 / 7)
    assert counts['judge_failures'] == 1
    failed_counts = run.summarize([results[-1]])['impacts / combined']
    assert failed_counts['improvement_rate'] is None
