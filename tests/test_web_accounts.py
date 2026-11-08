"""Account isolation and authentication for the paper dashboard."""
import threading
import json

import pytest
from fastapi.testclient import TestClient

from marketmind import accounts, webapp
from marketmind.paper import runner, state


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(accounts, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(accounts, 'USERS_FILE', tmp_path / 'secrets' / 'users.json')
    monkeypatch.setattr(accounts, 'KEY_FILE', tmp_path / 'secrets' / 'session.key')
    monkeypatch.setattr(webapp, '_markets', lambda: {'ts': 0, 'markets': {}})
    state.init_account(cash=375)
    owner = accounts.set_user('owner', 'old-pass', owner=True)
    guest = accounts.set_user('guest', 'guest-pass', auto_trade=True)
    owner_client, guest_client = TestClient(webapp.app), TestClient(webapp.app)
    return owner_client, guest_client, owner, guest


def login(client, username, password):
    return client.post('/api/login', json={'username': username, 'password': password})


def test_login_is_required_and_ledger_controls_are_isolated(clients):
    owner_client, guest_client, _, guest = clients
    assert owner_client.get('/').status_code == 200
    assert owner_client.get('/api/state').status_code == 401
    assert login(owner_client, 'owner', 'wrong').status_code == 401
    assert login(owner_client, 'owner', 'old-pass').status_code == 200
    assert login(guest_client, 'guest', 'guest-pass').status_code == 200
    assert owner_client.get('/api/state').json()['cash'] == 375
    guest_state = guest_client.get('/api/state').json()
    assert guest_state['cash'] == 200
    assert guest_state['auto_trade'] is True
    assert guest_client.post('/api/halt').json()['halted'] is True
    assert guest_client.get('/api/state').json()['halted'] is True
    assert owner_client.get('/api/state').json()['halted'] is False
    assert owner_client.get('/api/state').json()['auto_trade'] is False
    assert guest_client.post('/api/notify-test').status_code == 403
    with state.use_account(guest['account_id']):
        conn = state.connect()
        state.record_decision(conn, 'BTCUSDT', 'hold', {'reason': 'guest only'})
        conn.close()
    assert len(guest_client.get('/api/state').json()['decisions']) == 1
    assert owner_client.get('/api/state').json()['decisions'] == []


def test_password_update_revokes_old_session_without_resetting_ledger(clients):
    owner_client, _, _, _ = clients
    assert login(owner_client, 'owner', 'old-pass').status_code == 200
    accounts.set_user('owner', 'new-pass', owner=True)
    assert owner_client.get('/api/state').status_code == 401
    assert login(owner_client, 'owner', 'old-pass').status_code == 401
    assert login(owner_client, 'owner', 'new-pass').status_code == 200
    assert owner_client.get('/api/state').json()['cash'] == 375
    assert 'new-pass' not in accounts.USERS_FILE.read_text()


def test_cross_origin_write_and_forged_session_are_rejected(clients):
    owner_client, guest_client, _, _ = clients
    login(owner_client, 'owner', 'old-pass')
    assert owner_client.post('/api/halt', headers={'origin': 'https://other.example'}).status_code == 403
    assert owner_client.get('/api/state').json()['halted'] is False
    guest_client.cookies.set('marketmind_session', 'fake.signature')
    assert guest_client.get('/api/state').status_code == 401
    assert owner_client.get('/api/coin/BTCUSDT?bars=10000').status_code == 400


def test_legacy_owner_migrates_without_resetting_balance(clients, tmp_path):
    accounts.USERS_FILE.unlink()
    legacy = tmp_path / 'secrets' / 'dashboard_login.json'
    legacy.write_text(json.dumps({'username': 'legacy', 'password': 'legacy-pass'}))
    assert accounts.migrate_legacy_owner() is True
    assert accounts.authenticate('legacy', 'legacy-pass') is not None
    with state.use_account('owner'):
        conn = state.connect()
        assert state.account_cash(conn) == 375
        conn.close()


def test_analyst_cache_is_scoped_to_each_account(clients, monkeypatch, tmp_path):
    owner_client, guest_client, _, guest = clients
    monkeypatch.setattr(webapp, 'CANDLES_DIR', tmp_path / 'empty-candles')
    webapp._cache['signals'].clear()
    with state.use_account('owner'):
        conn = state.connect()
        state.save_analyst(conn, 'BTCUSDT', {'summary': 'owner-only'})
        conn.close()
    with state.use_account(guest['account_id']):
        conn = state.connect()
        state.save_analyst(conn, 'BTCUSDT', {'summary': 'guest-only'})
        conn.close()
    login(owner_client, 'owner', 'old-pass')
    login(guest_client, 'guest', 'guest-pass')
    assert owner_client.get('/api/signals').json()['signals']['BTCUSDT']['ai_summary'] == 'owner-only'
    assert guest_client.get('/api/signals').json()['signals']['BTCUSDT']['ai_summary'] == 'guest-only'


def test_manual_cycle_uses_request_account_in_background(clients, monkeypatch):
    owner_client, guest_client, _, guest = clients
    assert login(guest_client, 'guest', 'guest-pass').status_code == 200
    done = threading.Event()
    def fake_cycle(notify=None, force=False):
        conn = state.connect()
        state.record_decision(conn, 'ETHUSDT', 'hold', {'account': state.current_account()})
        conn.close()
        done.set()
        return {'lines': []}
    monkeypatch.setattr(runner, 'run_cycle', fake_cycle)
    assert guest_client.post('/api/run').json()['started'] is True
    assert done.wait(3)
    assert login(owner_client, 'owner', 'old-pass').status_code == 200
    assert owner_client.get('/api/state').json()['decisions'] == []
    assert guest_client.get('/api/state').json()['decisions'][0]['symbol'] == 'ETHUSDT'


def test_dashboard_asset_versions_change_with_content(clients, tmp_path, monkeypatch):
    owner, _, _, _ = clients
    assert login(owner, 'owner', 'old-pass').status_code == 200
    assets = tmp_path / 'static'
    assets.mkdir()
    (assets / 'app.html').write_text('<link href="/static/dashboard.css"><script src="/static/dashboard.js"></script>')
    (assets / 'dashboard.css').write_text('body { color: black; }')
    (assets / 'dashboard.js').write_text('const version = 1;')
    monkeypatch.setattr(webapp, 'STATIC', assets)
    first = owner.get('/app')
    assert first.status_code == 200 and first.headers['cache-control'] == 'no-store'
    import re
    js_version = re.search(r'/static/dashboard\.js\?v=([0-9a-f]+)', first.text).group(1)
    css_version = re.search(r'/static/dashboard\.css\?v=([0-9a-f]+)', first.text).group(1)
    (assets / 'dashboard.js').write_text('const version = 2;')
    second = owner.get('/app')
    assert f'/static/dashboard.js?v={js_version}' not in second.text
    assert f'/static/dashboard.css?v={css_version}' in second.text
