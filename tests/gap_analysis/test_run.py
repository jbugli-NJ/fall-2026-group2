"""
Tests for paired execution and accumulated results.
"""

# Imports

import json
from unittest.mock import Mock

import pytest

from monty_tool.gap_analysis import run, storage
from monty_tool.gap_analysis.schemas import (
    AnswerAttempt, SavedAnalysisRun, SavedAnswerAttempt, SavedPairedResult,
)


# Fixtures

@pytest.fixture
def response_bucket(monkeypatch):
    """
    Mock the configured S3 bucket.
    """
    bucket = Mock()
    monkeypatch.setattr(storage, 'get_bucket', Mock(return_value=bucket))
    monkeypatch.setattr(storage, 'get_env_bucket_name', lambda: 'test-bucket')
    monkeypatch.setattr(storage, 'get_env_bucket_prefix', lambda: 'test-owner/')
    return bucket


@pytest.fixture
def saved_pair(pair):
    """
    Provide a source comparison with S3 response references for summary tests.
    """
    record = SavedAnswerAttempt(
        response_s3_uri='s3://test-bucket/responses/answer.json',
        input_tokens=0, output_tokens=0,
        tool_failure_count=0,
        duration_seconds=0,
    )
    return SavedPairedResult(
        external_source=pair.external_source,
        question=pair.question,
        core_context=pair.core_context,
        core=record,
        external=record,
        contribution=pair.contribution,
    )


# Tests

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
    assert result.core.error == attempt.error
    assert result.external.error == attempt.error


def test_judge_error_is_counted_separately(monkeypatch, pair):
    """
    Verify judge failures are counted separately from answer failures.
    """
    monkeypatch.setattr(run, 'answer_question', Mock(return_value=pair.core))
    judge = Mock()
    judge.judge.side_effect = ValueError('Judge failed')
    result = run.run_pair(pair.question, pair.core_context, Mock(), judge, external_source='news')
    assert result.judge_error == 'Judge failed'
    assert result.contribution is None
    assert result.core.error is None
    assert result.external.error is None


def test_saved_runs_accumulate_without_overwriting(tmp_path, saved_pair, monkeypatch):
    """
    Retain all runs and group by model IDs within the current rubric.
    """
    monkeypatch.setattr(storage, 'get_bucket', Mock(side_effect=AssertionError('Unexpected S3 access')))
    saved_pair.core = saved_pair.core.model_copy(update={'tool_failure_count': 1})
    first = SavedAnalysisRun(metadata={
        'batch': 1, 'rubric_version': run.RUBRIC_VERSION,
        'answer_model': {'id': 'first-model'},
        'judge_model': {'id': 'judge-model'},
    }, results=[saved_pair])
    run.save_results(tmp_path, [first])
    runs = run.load_runs(tmp_path)
    news = saved_pair.model_copy(update={
        'external_source': 'news', 'contribution': run.Contribution.QUALIFIES,
    })
    weather = saved_pair.model_copy(update={
        'external_source': 'weather', 'contribution': run.Contribution.UNRESOLVED,
    })
    runs.append(SavedAnalysisRun(metadata={
        'batch': 2, 'rubric_version': run.RUBRIC_VERSION,
        'answer_model': {'id': 'second-model'},
        'judge_model': {'id': 'judge-model'},
    }, results=[saved_pair, news, weather]))
    runs.extend([
        first.model_copy(),
        SavedAnalysisRun(metadata={
            **first.metadata, 'judge_model': {'id': 'other-judge'},
        }, results=[saved_pair]),
    ])
    unsupported = saved_pair.model_copy(update={'contribution': run.Contribution.UNSUPPORTED})
    runs.extend([
        SavedAnalysisRun(metadata={'rubric_version': 'old-rubric'}, results=[unsupported]),
        SavedAnalysisRun(metadata={}, results=[unsupported]),
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
    assert first_counts[('impacts', 'shakemap_footprints')]['evaluated'] == 2
    assert first_counts[('impacts', 'shakemap_footprints')]['tool_failures'] == 2
    second_counts = summary[('second-model', 'judge-model')]
    assert {
        source: (counts['improved'], counts['evaluated'])
        for (_, source), counts in second_counts.items()
    } == {'shakemap_footprints': (1, 1), 'news': (1, 1), 'weather': (0, 1)}
    assert tmp_path.joinpath('summary.svg').is_file()


@pytest.mark.parametrize('selection_flags', [[], ['--no-require-external-data']])
@pytest.mark.parametrize('source_flags, sources', [
    ([], ['news', 'weather', 'shakemap_footprints']),
    (['--external-sources', 'shakemap_footprints'], ['shakemap_footprints']),
    (['--external-sources', 'news', 'weather'], ['news', 'weather']),
    (['--external-sources', 'shakemap_footprints', 'shakemap_footprints'], ['shakemap_footprints']),
])
def test_main_measures_each_source_against_shared_baseline(
    monkeypatch,
    tmp_path,
    pair,
    selection_flags,
    source_flags,
    sources,
    response_bucket,
):
    """
    Save a separate judgment for each external source.
    """
    set_seed = Mock()
    monkeypatch.setattr(run, 'set_seed', set_seed)
    select_contexts = Mock(return_value=[pair.core_context])
    monkeypatch.setattr(run, 'select_contexts', select_contexts)
    monkeypatch.setattr(run, 'construct_gap_questions', Mock(return_value=[pair.question]))
    assistant = Mock()
    assistant.ask.return_value = pair.core.response
    monkeypatch.setattr(run, 'make_answerer', Mock(return_value=assistant))
    judge = Mock()
    contributions = {
        'news': run.Contribution.QUALIFIES,
        'weather': run.Contribution.UNRESOLVED,
        'shakemap_footprints': run.Contribution.EXTENDS_COVERAGE,
    }
    judge.judge.side_effect = [contributions[source] for source in sources]
    monkeypatch.setattr(run, 'LocalContributionJudge', Mock(return_value=judge))

    assert run.main([
        '--answer-model', run.MODEL_CHOICES[0], '--judge-model', run.MODEL_CHOICES[0],
        '--output-dir', str(tmp_path),
        '--country-code', 'JPN', '--hazard-name', 'Earthquake', '--limit', '2',
        *selection_flags,
        *source_flags,
    ]) == 0

    assert [call.kwargs for call in assistant.ask.call_args_list] == [
        {
            'use_tools': True,
            'enable_news': source == 'news',
            'enable_weather': source == 'weather',
            'enable_external_footprints': source == 'shakemap_footprints',
        }
        for source in [None, *sources]
    ]
    set_seed.assert_called_once_with(run.SEED)
    require_external_data = not selection_flags
    select_contexts.assert_called_once_with(
        run.GraphSearchArguments(country_code='JPN'),
        2,
        hazard_name='Earthquake',
        require_external_data=require_external_data,
        external_sources=sources,
    )
    batch = run.load_runs(tmp_path)[0]
    assert batch.metadata['external_sources'] == sources
    assert batch.metadata['comparisons'] == [f'core_vs_{source}' for source in sources]
    assert batch.metadata['seed'] == run.SEED
    assert batch.metadata['limit'] == 2
    assert batch.metadata['contexts'] == [pair.core_context]
    assert batch.metadata['require_external_data'] is require_external_data
    results = batch.results
    assert [(result.external_source, result.contribution) for result in results] == [
        (source, contributions[source]) for source in sources
    ]
    assert response_bucket.upload_file.call_count == 1 + len(sources)
    assert len({result.core.response_s3_uri for result in results}) == 1
    saved = json.loads(tmp_path.joinpath('results.json').read_text())
    assert {
        counts['external_source'] for counts in saved['summary'][0]['results']
    } == set(sources)
    assert tmp_path.joinpath('summary.svg').is_file()


def test_improvement_rate_excludes_confirmation_and_failed_evaluations(saved_pair):
    """
    Count supported improvements among evaluated pairs and retain failed attempts.
    """
    results = [saved_pair.model_copy(update={'contribution': category}) for category in run.Contribution]
    results.append(saved_pair.model_copy(update={'contribution': None, 'judge_error': 'Failed'}))
    counts = run.summarize(results)['impacts / shakemap_footprints']
    assert counts['attempted'] == 8
    assert counts['evaluated'] == 7
    assert counts['improved'] == 3
    assert counts['improvement_rate'] == pytest.approx(3 / 7)
    assert counts['judge_failures'] == 1
    failed_counts = run.summarize([results[-1]])['impacts / shakemap_footprints']
    assert failed_counts['improvement_rate'] is None


def test_load_filters_before_validation(tmp_path, saved_pair):
    """
    Skip obsolete or unversioned runs before validating current runs.
    """
    current = SavedAnalysisRun(
        metadata={'rubric_version': run.RUBRIC_VERSION}, results=[saved_pair],
    )
    tmp_path.joinpath('results.json').write_text(json.dumps({'runs': [
        {
            'metadata': {'rubric_version': 'source-contribution-v1'},
            'results': [{'question': {'event_id': 'e'}}],
        },
        {'results': [{}]},
        current.model_dump(mode='json'),
    ]}))
    assert run.load_runs(tmp_path) == [current]


def test_attempt_upload(pair, response_bucket):
    """
    Upload response JSON and return its S3 URI and failure count.
    """
    def check_upload(path, key):
        assert json.loads(path.read_text()) == pair.core.response

    response_bucket.upload_file.side_effect = check_upload
    pair.core.response.update(input_tokens=350, output_tokens=50)
    pair.core.response['tool_results'].append({'result': {'status': 'error'}})
    record = storage.save_attempt(pair.core)
    response_bucket.upload_file.assert_called_once()
    key = response_bucket.upload_file.call_args.args[1]
    assert key.startswith('test-owner/gap_analysis/responses/')
    assert record.response_s3_uri == f's3://test-bucket/{key}'
    assert record.tool_failure_count == 1
    assert record.input_tokens == 350
    assert record.output_tokens == 50


def test_failed_attempt_needs_no_upload(response_bucket):
    """
    Save the error and elapsed time when an attempt produces no response.
    """
    record = storage.save_attempt(AnswerAttempt(error='Failed', duration_seconds=1.5))
    assert record == SavedAnswerAttempt(
        response_s3_uri=None, tool_failure_count=0, input_tokens=None, output_tokens=None,
        error='Failed', duration_seconds=1.5,
    )
    response_bucket.upload_file.assert_not_called()
