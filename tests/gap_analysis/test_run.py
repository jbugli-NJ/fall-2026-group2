"""
Tests for paired execution and accumulated results.
"""

# Imports

import json
from unittest.mock import Mock, call

import pytest

from monty_tool.gap_analysis import run, storage
from monty_tool.gap_analysis.schemas import (
    AnswerAttempt, SavedAnalysisRun, SavedAnswerAttempt, SavedPairedResult,
)


# Fixtures

@pytest.fixture
def answer_bucket(monkeypatch):
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
    Provide a source comparison with archived answer references for summary tests.
    """
    record = SavedAnswerAttempt(
        answer_s3_uri='s3://test-bucket/answers/answer.txt',
        input_tokens=0, output_tokens=0,
        duration_seconds=0,
    )
    return SavedPairedResult(
        external_source=pair.external_source,
        question=pair.question,
        selection=pair.selection,
        evidence_s3_uri=pair.evidence_s3_uri,
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
    result = run.run_pair(
        pair.question,
        pair.selection,
        Mock(),
        judge,
        external_source=source,
        evidence={
            'events': [],
            'responses': [],
        },
        evidence_s3_uri=pair.evidence_s3_uri,
    )
    assert [
        call.kwargs['external_source']
        for call in answer.call_args_list
    ] == [None, source]
    assert result.external_source == source
    assert judge.judge.call_count == int(not failed)
    if not failed:
        judge.judge.assert_called_once_with(
            result,
            {
                'events': [],
                'responses': [],
            },
        )
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
    result = run.run_pair(
        pair.question,
        pair.selection,
        Mock(),
        judge,
        external_source='news',
        evidence={
            'events': [],
            'responses': [],
        },
        evidence_s3_uri=pair.evidence_s3_uri,
    )
    assert result.judge_error == 'Judge failed'
    assert result.contribution is None
    assert result.core.error is None
    assert result.external.error is None


def test_saved_runs_accumulate_without_overwriting(tmp_path, saved_pair, monkeypatch):
    """
    Retain all runs and group by model IDs within the current rubric.
    """
    monkeypatch.setattr(
        storage,
        'get_bucket',
        Mock(side_effect=AssertionError('Unexpected S3 access')),
    )
    first = SavedAnalysisRun(metadata={
        'batch': 1,
        'rubric_version': run.RUBRIC_VERSION,
        'answer_model': {'id': 'first-model'},
        'judge_model': {'id': 'judge-model'},
    }, results=[saved_pair])
    run.save_results(tmp_path, [first])
    runs = run.load_runs(tmp_path)
    news = saved_pair.model_copy(update={
        'external_source': 'news',
        'contribution': run.Contribution.IMPROVEMENT,
    })
    weather = saved_pair.model_copy(update={
        'external_source': 'weather',
        'contribution': run.Contribution.NO_CHANGE,
    })
    runs.append(SavedAnalysisRun(metadata={
        'batch': 2,
        'rubric_version': run.RUBRIC_VERSION,
        'answer_model': {'id': 'second-model'},
        'judge_model': {'id': 'judge-model'},
    }, results=[saved_pair, news, weather]))
    runs.extend([
        first.model_copy(),
        SavedAnalysisRun(metadata={
            **first.metadata,
            'judge_model': {'id': 'other-judge'},
        }, results=[saved_pair]),
    ])
    regression = saved_pair.model_copy(
        update={'contribution': run.Contribution.REGRESSION},
    )
    runs.extend([
        SavedAnalysisRun(metadata={'rubric_version': 'old-rubric'}, results=[regression]),
        SavedAnalysisRun(metadata={}, results=[regression]),
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
    second_counts = summary[('second-model', 'judge-model')]
    assert {
        source: (counts['improved'], counts['evaluated'])
        for (_, source), counts in second_counts.items()
    } == {
        'shakemap_footprints': (1, 1),
        'news': (1, 1),
        'weather': (0, 1),
    }
    assert tmp_path.joinpath('summary.svg').is_file()


@pytest.mark.parametrize('question_count', [1, 2])
@pytest.mark.parametrize('selection_flags', [[], ['--no-require-external-data']])
@pytest.mark.parametrize('source_flags, sources', [
    ([], ['news', 'weather', 'shakemap_footprints']),
    (['--external-sources', 'shakemap_footprints'], ['shakemap_footprints']),
    (['--external-sources', 'news', 'weather'], ['news', 'weather']),
    (
        ['--external-sources', 'shakemap_footprints', 'shakemap_footprints'],
        ['shakemap_footprints'],
    ),
])
def test_main_measures_each_source_against_its_own_baseline(
    monkeypatch,
    tmp_path,
    pair,
    selection_flags,
    source_flags,
    sources,
    question_count,
    answer_bucket,
):
    """
    Save a separate judgment for each external source.
    """
    set_seed = Mock()
    monkeypatch.setattr(run, 'set_seed', set_seed)
    select_country_hazards = Mock(return_value=[pair.selection])
    monkeypatch.setattr(run, 'select_country_hazards', select_country_hazards)
    questions = [pair.question.model_copy(update={'template_id': f'question_{i}'})
                 for i in range(question_count)]
    monkeypatch.setattr(run, 'construct_gap_questions', Mock(return_value=questions))
    snapshots = {
        source: {
            'events': [{
                'event_id': source,
                'impacts': [],
            }],
            'responses': [],
        }
        for source in sources
    }
    def retrieve_snapshot(filters, *, hazard_name, external_source):
        return snapshots[external_source]

    retrieve = Mock(side_effect=retrieve_snapshot)
    monkeypatch.setattr(run, 'retrieve_evidence', retrieve)
    assistant = Mock()

    def answer(prompt, **kwargs):
        return {
            'answer': json.loads(prompt)['records']['events'][0]['event_id'],
            'input_tokens': 0,
            'output_tokens': 0,
            'tool_results': [],
        }

    assistant.ask.side_effect = answer
    monkeypatch.setattr(run, 'make_answerer', Mock(return_value=assistant))
    judge = Mock()
    contributions = {
        'news': run.Contribution.IMPROVEMENT,
        'weather': run.Contribution.NO_CHANGE,
        'shakemap_footprints': run.Contribution.IMPROVEMENT,
    }
    judge.judge.side_effect = [
        contributions[source] for source in sources for _ in questions
    ]
    monkeypatch.setattr(run, 'LocalContributionJudge', Mock(return_value=judge))

    assert run.main([
        '--answer-model', run.MODEL_CHOICES[0], '--judge-model', run.MODEL_CHOICES[0],
        '--output-dir', str(tmp_path),
        '--country-code', 'JPN', '--hazard-name', 'Earthquake', '--limit', '2',
        *selection_flags,
        *source_flags,
    ]) == 0

    assert [call.kwargs for call in assistant.ask.call_args_list] == [
        {'use_tools': False} for _ in range(2 * len(sources) * question_count)
    ]
    assert all(
        judged.args[1] is snapshots[judged.args[0].external_source]
        for judged in judge.judge.call_args_list
    )
    assert retrieve.call_args_list == [
        call(
            pair.selection.filters,
            hazard_name=pair.question.hazard_name,
            external_source=source,
        ) for source in sources
    ]
    set_seed.assert_called_once_with(run.SEED)
    require_external_data = not selection_flags
    select_country_hazards.assert_called_once_with(
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
    assert batch.metadata['disaster_limit'] == 20
    assert batch.metadata['selections'] == [pair.selection.model_dump(mode='json')]
    assert batch.metadata['require_external_data'] is require_external_data
    results = batch.results
    assert [(result.external_source, result.contribution) for result in results] == [
        (source, contributions[source]) for source in sources for _ in questions
    ]
    assert all(
        result.evidence_s3_uri
        == judge.judge.call_args_list[i].args[0].evidence_s3_uri
        for i, result in enumerate(results)
    )
    assert answer_bucket.upload_file.call_count == len(sources) * (1 + 2 * question_count)
    assert len({result.evidence_s3_uri for result in results}) == len(sources)
    assert results[0].evidence_s3_uri.startswith(
        's3://test-bucket/test-owner/gap_analysis/evidence/',
    )
    assert len({result.core.answer_s3_uri for result in results}) == len(sources)
    for source in sources:
        source_pairs = [result for result in results if result.external_source == source]
        assert len({result.evidence_s3_uri for result in source_pairs}) == 1
    prompts = [json.loads(asked.args[0]) for asked in assistant.ask.call_args_list]
    for baseline, external in zip(prompts[::2], prompts[1::2]):
        assert baseline['external_source'] is None
        assert baseline['records']['events'] == external['records']['events']
    saved = json.loads(tmp_path.joinpath('results.json').read_text())
    assert {
        counts['external_source'] for counts in saved['summary'][0]['results']
    } == set(sources)
    assert tmp_path.joinpath('summary.svg').is_file()


def test_main_keeps_each_selections_evidence_with_its_questions(
    monkeypatch,
    tmp_path,
    pair,
):
    """
    Retrieve and archive once per selection, reusing its snapshot for all questions.
    """
    second = pair.selection.model_copy(update={
        'filters': run.GraphSearchArguments(country_code='FRA'),
    })
    selections = [pair.selection, second]
    snapshots = [{
        'events': [],
        'responses': [],
    }, {
        'events': [],
        'responses': [],
    }]
    monkeypatch.setattr(run, 'select_country_hazards', Mock(return_value=selections))
    monkeypatch.setattr(run, 'construct_gap_questions', lambda selection, templates: [
        pair.question.model_copy(update={
            'country_code': selection.filters.country_code,
            'template_id': template_id,
        }) for template_id in ['first', 'second']
    ])
    retrieve = Mock(side_effect=snapshots)
    monkeypatch.setattr(run, 'retrieve_evidence', retrieve)
    archive = Mock(side_effect=['s3://test/first.json', 's3://test/second.json'])
    monkeypatch.setattr(run, 'save_evidence', archive)
    monkeypatch.setattr(run, 'answer_question', Mock(return_value=pair.core))
    monkeypatch.setattr(run, 'save_attempt', Mock(return_value=SavedAnswerAttempt(
        input_tokens=0, output_tokens=0, duration_seconds=0,
    )))
    monkeypatch.setattr(run, 'save_results', Mock())
    monkeypatch.setattr(run, 'set_seed', Mock())
    monkeypatch.setattr(run, 'make_answerer', Mock())
    judge = Mock()
    judge.judge.return_value = pair.contribution
    monkeypatch.setattr(run, 'LocalContributionJudge', Mock(return_value=judge))

    assert run.main([
        '--answer-model', run.MODEL_CHOICES[0], '--judge-model', run.MODEL_CHOICES[0],
        '--output-dir', str(tmp_path), '--external-sources', 'news',
    ]) == 0

    assert [call.args[0] for call in retrieve.call_args_list] == [
        selection.filters for selection in selections
    ]
    assert archive.call_count == 2
    for call, snapshot in zip(archive.call_args_list, snapshots):
        assert call.args[0] is snapshot
    assert len(judge.judge.call_args_list) == 4
    for call, selection, snapshot, uri in zip(
        judge.judge.call_args_list,
        [pair.selection, pair.selection, second, second],
        [snapshots[0], snapshots[0], snapshots[1], snapshots[1]],
        ['s3://test/first.json'] * 2 + ['s3://test/second.json'] * 2,
    ):
        judged_pair, evidence = call.args
        assert judged_pair.selection == selection
        assert evidence is snapshot
        assert judged_pair.evidence_s3_uri == uri



def test_improvement_rate_counts_only_improvements_among_evaluated_pairs(saved_pair):
    """
    Count supported improvements among evaluated pairs and retain failed attempts.
    """
    results = [
        saved_pair.model_copy(update={'contribution': category})
        for category in run.Contribution
    ]
    results.append(
        saved_pair.model_copy(update={
            'contribution': None,
            'judge_error': 'Failed',
        }),
    )
    counts = run.summarize(results)['impacts / shakemap_footprints']
    assert counts['attempted'] == 4
    assert counts['evaluated'] == 3
    assert counts['improved'] == 1
    assert counts['improvement_rate'] == pytest.approx(1 / 3)
    assert counts['judge_failures'] == 1
    assert 'tool_failures' not in counts
    assert 'pairs_with_tool_failures' not in counts
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


def test_attempt_upload(pair, answer_bucket):
    """
    Upload only answer text and retain token counts in the result metadata.
    """
    def check_upload(path, key):
        assert path.read_text() == pair.core.answer

    answer_bucket.upload_file.side_effect = check_upload
    pair.core.input_tokens = 350
    pair.core.output_tokens = 50
    record = storage.save_attempt(pair.core)
    answer_bucket.upload_file.assert_called_once()
    key = answer_bucket.upload_file.call_args.args[1]
    assert key.startswith('test-owner/gap_analysis/answers/')
    assert record.answer_s3_uri == f's3://test-bucket/{key}'
    assert record.input_tokens == 350
    assert record.output_tokens == 50


def test_failed_attempt_needs_no_upload(answer_bucket):
    """
    Save the error and elapsed time when an attempt produces no response.
    """
    record = storage.save_attempt(AnswerAttempt(error='Failed', duration_seconds=1.5))
    assert record == SavedAnswerAttempt(
        answer_s3_uri=None, input_tokens=None, output_tokens=None,
        error='Failed', duration_seconds=1.5,
    )
    answer_bucket.upload_file.assert_not_called()
