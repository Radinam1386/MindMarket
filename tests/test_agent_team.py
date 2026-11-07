import json
import threading

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from marketmind import accounts, llm, webapp
from marketmind.paper import agent_team, state


def candles():
    return pd.DataFrame({"ts": range(201), "open": [10.] * 201, "high": [12.] * 201,
                         "low": [9.] * 201, "close": [11.] * 201, "volume": [5.] * 201})


def test_new_model_parameter_fallback(monkeypatch):
    monkeypatch.setattr(llm, 'load_config', lambda: {'base_url': 'https://example.test',
                                                     'api_key': 'secret', 'model': 'test'})
    calls = []
    def post(url, json, headers, timeout):
        calls.append(dict(json))
        if len(calls) == 1:
            return httpx.Response(400, text='max_tokens unsupported; use max_completion_tokens')
        if len(calls) == 2:
            return httpx.Response(400, text='temperature does not support 0.2')
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
    monkeypatch.setattr(llm.httpx, 'post', post)
    assert llm.parse_json_loose(llm.chat('test', 'test')) == {'ok': True}
    assert 'max_tokens' not in calls[-1] and 'max_completion_tokens' in calls[-1]
    assert 'temperature' not in calls[-1]


def test_team_reports_failures_and_synthesizes(monkeypatch):
    def chat(system, user, **kwargs):
        if 'نقش: حجم' in user:
            raise llm.LLMError('secret provider error')
        return json.dumps({'summary': 'نظر آزمایشی', 'stance': 'neutral'})
    monkeypatch.setattr(llm, 'chat', chat)
    result = agent_team.run_team('BTCUSDT', candles())
    assert len(result['reports']) == 11
    assert len({r['id'] for r in result['reports']}) == 11
    assert result['reports'][4]['status'] == 'error'
    assert 'secret' not in json.dumps(result)
    assert result['reports'][-1]['id'] == 'coordinator'
    with pytest.raises(ValueError):
        agent_team.make_context('BTCUSDT', candles().head(10))


def test_team_endpoint_auth_isolation_and_single_job(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(accounts, 'USERS_FILE', tmp_path / 'secrets/users.json')
    monkeypatch.setattr(accounts, 'KEY_FILE', tmp_path / 'secrets/session.key')
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(webapp, 'CANDLES_DIR', tmp_path / 'candles')
    monkeypatch.setattr(agent_team, '_jobs', {})
    monkeypatch.setattr(agent_team, '_active', False)
    p = tmp_path / 'candles/BTCUSDT/1D.parquet'
    p.parent.mkdir(parents=True)
    candles().to_parquet(p)
    accounts.set_user('one', 'password', owner=True)
    accounts.set_user('two', 'password')
    one, two = TestClient(webapp.app), TestClient(webapp.app)
    assert one.get('/api/agent-team').status_code == 401
    assert one.post('/api/login', json={'username': 'one', 'password': 'password'}).status_code == 200
    assert two.post('/api/login', json={'username': 'two', 'password': 'password'}).status_code == 200
    started, release = threading.Event(), threading.Event()
    def work(symbol, frame):
        started.set()
        assert release.wait(3)
        return {'symbol': symbol, 'reports': []}
    monkeypatch.setattr(agent_team, 'run_team', work)
    assert one.post('/api/agent-team', json={'symbol': '../etc'}).status_code == 400
    assert one.post('/api/agent-team', json={'symbol': 'BTCUSDT'}).status_code == 202
    assert started.wait(3)
    assert one.get('/api/agent-team').json()['status'] == 'running'
    assert two.get('/api/agent-team').json()['status'] == 'empty'
    assert two.post('/api/agent-team', json={'symbol': 'BTCUSDT'}).status_code == 409
    release.set()
    for _ in range(100):
        if agent_team.latest_job('owner')['status'] != 'running':
            break
        threading.Event().wait(0.01)
    assert agent_team.latest_job('owner')['status'] == 'complete'


def test_team_gate_fails_closed_and_vetoes_opposing_votes(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'p.sqlite')
    reports = [{'id': key, 'status': 'ok', 'stance': 'neutral'} for key, _, _ in agent_team.ROLES]
    reports.append({'id': 'coordinator', 'status': 'ok', 'stance': 'neutral'})
    result = {'symbol': 'BTCUSDT', 'candle_ts': 200, 'created_at': __import__('time').time(),
              'reports': reports}
    assert agent_team.entry_gate(result, 'long') == 'allow'
    for report in reports[:3]:
        report['stance'] = 'bearish'
    assert agent_team.entry_gate(result, 'long') == 'blocked'
    assert agent_team.entry_gate(result, 'short') == 'allow'
    reports[3]['status'] = 'error'
    assert agent_team.entry_gate(result, 'long') == 'unavailable'
    conn = state.connect()
    state.set_setting(conn, 'team_review:BTCUSDT', json.dumps(result))
    assert agent_team.saved_review(conn, 'BTCUSDT', 200) is not None
    assert agent_team.saved_review(conn, 'BTCUSDT', 201) is None
    conn.close()


def test_failed_opinion_is_not_cached_for_the_day(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'p.sqlite')
    conn = state.connect()
    state.save_analyst(conn, 'BTCUSDT', {'ok': False, 'summary': 'failure'})
    assert not state.analyst_today(conn, 'BTCUSDT')
    state.save_analyst(conn, 'BTCUSDT', {'ok': True, 'summary': 'success'})
    assert state.analyst_today(conn, 'BTCUSDT')
    conn.close()


def test_paper_entry_waits_for_review_then_uses_it(tmp_path, monkeypatch):
    import time
    from marketmind.paper import runner
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(runner, 'ASSETS', ['BTCUSDT'])
    config = dict(runner.PAPER_CFG, use_llm=False, team_gate=True, allow_shorts=False)
    monkeypatch.setattr(runner, 'PAPER_CFG', config)
    monkeypatch.setattr(runner.events, 'record', lambda *a, **kw: None)
    monkeypatch.setattr(runner, 'WallexPublicClient', lambda: object())
    monkeypatch.setattr(runner.ingest, 'load_candles', lambda *a: None)
    df = pd.DataFrame({'ts': range(220), 'open': [100.] * 220, 'high': [101.] * 220,
                       'low': [99.] * 220, 'close': [100.] * 220, 'volume': [10.] * 220})
    monkeypatch.setattr(runner, 'refresh_candles', lambda client: {'BTCUSDT': df})
    monkeypatch.setattr(runner, 'live_quote', lambda *a: {'bid': 100., 'ask': 100., 'mid': 100., 'last': 100.})
    monkeypatch.setattr(runner, 'donchian_er', lambda frame, **kw: {
        'long_entry': pd.Series([True] * len(frame)), 'long_exit': pd.Series([False] * len(frame))})
    scheduled = []
    monkeypatch.setattr(agent_team, 'start_review', lambda account, symbol, frame, source: scheduled.append((account, symbol, int(frame.ts.iloc[-1]))) or 'started')
    runner.run_cycle(force=True)
    conn = state.connect()
    assert conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 0
    assert scheduled == [('owner', 'BTCUSDT', 218)]
    assert 'team_review_pending' in conn.execute('SELECT detail FROM decisions ORDER BY id DESC').fetchone()[0]
    reports = [{'id': key, 'status': 'ok', 'stance': 'neutral'} for key, _, _ in agent_team.ROLES]
    reports.append({'id': 'coordinator', 'status': 'ok', 'stance': 'neutral'})
    state.set_setting(conn, 'team_review:BTCUSDT', json.dumps({'symbol': 'BTCUSDT', 'candle_ts': 218, 'created_at': time.time(), 'reports': reports}))
    conn.close()
    runner.run_cycle(force=True)
    conn = state.connect()
    assert conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM decisions WHERE action='review'").fetchone()[0] == 1
    conn.close()


def test_existing_position_exits_without_waiting_for_team(tmp_path, monkeypatch):
    from marketmind.paper import runner
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(runner, 'ASSETS', ['BTCUSDT'])
    monkeypatch.setattr(runner, 'PAPER_CFG', dict(runner.PAPER_CFG, use_llm=True, team_gate=True))
    monkeypatch.setattr(runner.events, 'record', lambda *a, **kw: None)
    monkeypatch.setattr(runner, 'WallexPublicClient', lambda: object())
    monkeypatch.setattr(runner.ingest, 'load_candles', lambda *a: None)
    df = pd.DataFrame({'ts': range(220), 'open': [100.] * 220, 'high': [101.] * 220,
                       'low': [99.] * 220, 'close': [100.] * 220, 'volume': [10.] * 220})
    monkeypatch.setattr(runner, 'refresh_candles', lambda client: {'BTCUSDT': df})
    monkeypatch.setattr(runner, 'live_quote', lambda *a: {'bid': 100., 'ask': 100., 'mid': 100., 'last': 100.})
    monkeypatch.setattr(runner, 'donchian_er', lambda frame, **kw: {
        'long_entry': pd.Series([False] * len(frame)), 'long_exit': pd.Series([True] * len(frame))})
    monkeypatch.setattr(agent_team, 'start_review', lambda *a, **kw: pytest.fail('exit must not wait for review'))
    monkeypatch.setattr(runner.analyst, 'analyze', lambda *a, **kw: pytest.fail('exit must not wait for analyst'))
    state.init_account(cash=200.)
    conn = state.connect()
    state.open_position(conn, 'BTCUSDT', 'long', 100., 30., 3., 0.8546, 0.0045, meta={})
    assert runner.recent_entries(conn) == 1
    conn.close()
    runner.run_cycle(force=True)
    conn = state.connect()
    assert conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0] == 1
    conn.close()


def test_cycle_status_reports_running_and_completed(tmp_path, monkeypatch):
    from marketmind.paper import runner
    monkeypatch.setattr(accounts, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(accounts, 'USERS_FILE', tmp_path / 'secrets/users.json')
    monkeypatch.setattr(accounts, 'KEY_FILE', tmp_path / 'secrets/session.key')
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(webapp, '_active_cycles', {})
    accounts.set_user('one', 'password', owner=True)
    client = TestClient(webapp.app)
    assert client.post('/api/login', json={'username': 'one', 'password': 'password'}).status_code == 200
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def work(**kwargs):
        started.set()
        assert release.wait(3)
        return {'open': 1, 'closed': 2, 'lines': []}
    monkeypatch.setattr(runner, 'run_cycle', work)
    original_finish = webapp._cycle_finish
    def finish(*args, **kwargs):
        original_finish(*args, **kwargs)
        finished.set()
    monkeypatch.setattr(webapp, '_cycle_finish', finish)
    assert client.post('/api/run').json()['started'] is True
    assert started.wait(3)
    assert client.get('/api/autotrade').json()['running']['source'] == 'manual'
    release.set()
    assert finished.wait(3)
    status = client.get('/api/autotrade').json()
    assert status['running'] is None
    assert status['last_summary']['status'] == 'complete'
    assert (status['last_summary']['open'], status['last_summary']['closed']) == (1, 2)
