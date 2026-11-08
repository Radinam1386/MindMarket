import json
import sqlite3
import time

import pandas as pd
from fastapi.testclient import TestClient

from marketmind import accounts, webapp
from marketmind.paper import agent_team, runner, state


def test_profiles_are_account_scoped_and_do_not_mutate_shared_config(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'owner.sqlite')
    monkeypatch.setattr(runner, 'PAPER_CFG', dict(runner.PAPER_CFG, er_min=.15,
                                                  max_spread_pct=.15, max_entries_7d=3,
                                                  btc_filter_shorts=True))
    other = 'a' * 32
    with state.use_account('owner'):
        conn = state.connect()
        assert runner.entry_profile(conn) == {'name': 'standard', 'er_min': .15,
                                              'max_spread_pct': .15, 'max_entries_7d': 3,
                                              'btc_filter_shorts': True}
        state.set_setting(conn, 'paper_entry_profile', 'active')
        active = runner.entry_profile(conn)
        assert active == {'name': 'active', 'er_min': .10, 'max_spread_pct': .5,
                          'max_entries_7d': 8, 'btc_filter_shorts': False}
        state.set_setting(conn, 'paper_unlimited_weekly', '1')
        assert runner.entry_profile(conn)['max_entries_7d'] is None
        state.set_setting(conn, 'paper_entry_profile', 'standard')
        assert runner.entry_profile(conn)['max_entries_7d'] is None
        conn.close()
    with state.use_account(other):
        conn = state.connect()
        assert runner.entry_profile(conn)['name'] == 'standard'
        assert runner.entry_profile(conn)['btc_filter_shorts'] is True
        conn.close()
    assert runner.PAPER_CFG['er_min'] == .15
    assert runner.PAPER_CFG['max_spread_pct'] == .15


def test_profile_api_requires_auth_validates_and_isolates_users(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(accounts, 'USERS_FILE', tmp_path / 'secrets/users.json')
    monkeypatch.setattr(accounts, 'KEY_FILE', tmp_path / 'secrets/session.key')
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(runner, 'PAPER_CFG', dict(runner.PAPER_CFG, er_min=.15,
                                                  max_spread_pct=.15, max_entries_7d=3,
                                                  btc_filter_shorts=True))
    monkeypatch.setattr(webapp.events, 'record', lambda *a, **kw: None)
    accounts.set_user('owner', 'password', owner=True)
    accounts.set_user('guest', 'password')
    owner, guest = TestClient(webapp.app), TestClient(webapp.app)
    assert owner.post('/api/entry-profile', json={'profile': 'active'}).status_code == 401
    for client, username in ((owner, 'owner'), (guest, 'guest')):
        assert client.post('/api/login', json={'username': username, 'password': 'password'}).status_code == 200
    assert owner.post('/api/entry-profile', json={'profile': 'unsafe'}).status_code == 400
    with state.use_account('owner'):
        conn = state.connect()
        state.set_setting(conn, 'paper_unlimited_weekly', '1')
        conn.close()
    assert owner.post('/api/entry-profile', json={'profile': 'active'}).json()['profile'] == 'active'
    a, b = owner.get('/api/autotrade').json(), guest.get('/api/autotrade').json()
    assert (a['entry_profile'], a['max_spread_pct'], a['er_min'], a['max_entries_7d']) == ('active', .5, .1, None)
    assert b['entry_profile'] == 'standard' and b['max_spread_pct'] == .15
    assert owner.post('/api/entry-profile', json={'profile': 'standard'}).status_code == 200
    restored = owner.get('/api/autotrade').json()
    assert restored['entry_profile'] == 'standard' and restored['max_entries_7d'] is None


def test_active_profile_allows_candidate_after_spread_and_regime_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(runner, 'ASSETS', ['BTCUSDT', 'XRPUSDT'])
    monkeypatch.setattr(runner, 'PAPER_CFG', dict(runner.PAPER_CFG, er_min=.15,
        max_spread_pct=.15, max_entries_7d=3, btc_filter_shorts=True,
        use_llm=False, team_gate=True, allow_shorts=True, signal_mode='closed'))
    monkeypatch.setattr(runner.events, 'record', lambda *a, **kw: None)
    monkeypatch.setattr(runner, 'WallexPublicClient', lambda: object())
    monkeypatch.setattr(runner.ingest, 'load_candles', lambda *a: None)
    btc = pd.DataFrame({'ts': range(220), 'close': [100. + i*.01 for i in range(220)]})
    btc['open'], btc['high'], btc['low'], btc['volume'] = btc['close'], btc['close']+1, btc['close']-1, 10.
    xrp = pd.DataFrame({'ts': range(220), 'open': [100.] * 220, 'high': [101.] * 220,
                        'low': [99.] * 220, 'close': [100.] * 220, 'volume': [10.] * 220})
    xrp.loc[218, ['low', 'close']] = [89., 90.]
    monkeypatch.setattr(runner, 'refresh_candles', lambda c: {'BTCUSDT': btc, 'XRPUSDT': xrp})
    monkeypatch.setattr(runner, 'live_quote', lambda c, s: {'bid': 99.8, 'ask': 100.2, 'mid': 100., 'last': 100.})
    monkeypatch.setattr(runner, 'donchian_er', lambda df, **kw: {
        'long_entry': pd.Series([False] * len(df)), 'long_exit': pd.Series([False] * len(df))})
    monkeypatch.setattr(runner, 'efficiency_ratio', lambda close, n: pd.Series([.3] * len(close)))
    calls = []
    monkeypatch.setattr(agent_team, 'saved_review', lambda c, s, ts: {'created_at': 1})
    monkeypatch.setattr(agent_team, 'entry_gate', lambda result, side: calls.append(side) or 'allow')
    runner.run_cycle(force=True)
    conn = state.connect()
    assert conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 0
    assert 'spread' in conn.execute("SELECT detail FROM decisions WHERE symbol='XRPUSDT' ORDER BY id DESC LIMIT 1").fetchone()[0]
    # Even after more than the old eight-entry limit, the active profile may
    # enter when every other gate permits it.
    now = int(time.time())
    for _ in range(9):
        conn.execute("INSERT INTO trades (symbol,side,entry_ts,exit_ts,entry_px,exit_px,qty,"
                     "collateral,leverage,interest,fees,return_pct,close_reason,meta) "
                     "VALUES ('OTHER','long',?,?,?,?,?,?,?,?,?,?,?,?)",
                     (now-60, now-30, 100., 101., 1., 30., 3., 0., 0., 1., 'signal', '{}'))
    conn.commit()
    # The standard profile still enforces its own weekly cap.
    runner.PAPER_CFG['max_spread_pct'] = .5
    runner.run_cycle(force=True)
    reason = conn.execute("SELECT detail FROM decisions WHERE symbol='XRPUSDT' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert 'weekly budget (3/7d)' in reason
    runner.PAPER_CFG['max_spread_pct'] = .15
    state.set_setting(conn, 'paper_entry_profile', 'active')
    state.set_setting(conn, 'paper_unlimited_weekly', '1')
    conn.close()
    runner.run_cycle(force=True)
    conn = state.connect()
    position = conn.execute("SELECT side,meta FROM positions WHERE symbol='XRPUSDT'").fetchone()
    assert position and position['side'] == 'short'
    assert json.loads(position['meta'])['entry_profile'] == 'active'
    assert calls == ['short']
    conn.close()


def test_ai_short_needs_measured_trust_before_team_review(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(runner, 'ASSETS', ['BTCUSDT'])
    monkeypatch.setattr(runner, 'PAPER_CFG', dict(runner.PAPER_CFG, use_llm=True,
                                                  allow_shorts=True, team_gate=True))
    monkeypatch.setattr(runner.events, 'record', lambda *a, **kw: None)
    monkeypatch.setattr(runner, 'WallexPublicClient', lambda: object())
    monkeypatch.setattr(runner.ingest, 'load_candles', lambda *a: None)
    frame = pd.DataFrame({'ts': range(220), 'open': [100.] * 220, 'high': [101.] * 220,
                          'low': [99.] * 220, 'close': [100.] * 220, 'volume': [10.] * 220})
    monkeypatch.setattr(runner, 'refresh_candles', lambda c: {'BTCUSDT': frame})
    monkeypatch.setattr(runner, 'live_quote', lambda *a: {'bid': 100., 'ask': 100., 'mid': 100., 'last': 100.})
    monkeypatch.setattr(runner, 'donchian_er', lambda df, **kw: {
        'long_entry': pd.Series([False] * len(df)), 'long_exit': pd.Series([False] * len(df))})
    monkeypatch.setattr(state, 'analyst_today', lambda c, s: True)
    monkeypatch.setattr(state, 'recent_analyst', lambda c, s, n: [
        {'bias': 'bearish', 'confidence': .99, 'entry_ok': True, 'size_factor': .5,
         'summary': '', 'support': '', 'resistance': ''}])
    monkeypatch.setattr(runner, 'ai_trust', lambda c, df, min_acc, symbol: {'conviction': False, 'trusted': False})
    calls = []
    monkeypatch.setattr(agent_team, 'saved_review', lambda *a: calls.append('review'))
    runner.run_cycle(force=True)
    conn = state.connect()
    assert conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 0
    assert calls == []
    conn.close()
