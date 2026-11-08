import pandas as pd
import pytest

from marketmind import llm
from marketmind.paper import analyst, runner, state


def test_trust_scores_only_mature_same_symbol_three_day_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(runner, 'PAPER_CFG', dict(runner.PAPER_CFG, signal_mode='closed'))
    day = 86400
    base = 20000 * day
    closes = [100. + i for i in range(10)] + [70.] * 6
    frame = pd.DataFrame({'ts': [base + i * day for i in range(len(closes))], 'close': closes})
    conn = state.connect()
    for i in range(6):
        state.save_analyst(conn, 'BTCUSDT', {'bias': 'bullish', 'confidence': .7})
        conn.execute('UPDATE analyst SET ts=? WHERE id=(SELECT MAX(id) FROM analyst)',
                     (base + (i + 1) * day + 3600,))
    for i in range(10):
        state.save_analyst(conn, 'ETHUSDT', {'bias': 'bearish', 'confidence': .7})
        conn.execute('UPDATE analyst SET ts=? WHERE id=(SELECT MAX(id) FROM analyst)',
                     (base + (i + 1) * day + 3600,))
    conn.commit()
    result = runner.ai_trust(conn, frame, .4, symbol='BTCUSDT')
    assert result == {'accuracy': 1.0, 'n': 6, 'trusted': True, 'conviction': True}
    # A later decline must not change the measured three-bar outcomes.
    assert frame.close.iloc[-1] < frame.close.iloc[0]
    # An incomplete three-bar future must not be scored.
    partial = runner.ai_trust(conn, frame.iloc[:4], .4, symbol='BTCUSDT')
    assert partial['n'] == 1 and not partial['conviction']
    monkeypatch.setitem(runner.PAPER_CFG, 'signal_mode', 'live')
    assert runner.ai_trust(conn, frame, .4, symbol='BTCUSDT')['n'] == 0
    conn.close()


def test_analyst_requires_boolean_entry_and_object_response(monkeypatch):
    monkeypatch.setattr(analyst, 'build_context', lambda *a, **kw: 'context')
    monkeypatch.setattr(llm, 'chat', lambda *a, **kw: '{"entry_ok":"false"}')
    result = analyst.analyze('BTCUSDT', pd.DataFrame(), None, None, {})
    assert result['ok'] and result['entry_ok'] is False
    monkeypatch.setattr(llm, 'chat', lambda *a, **kw: '[]')
    result = analyst.analyze('BTCUSDT', pd.DataFrame(), None, None, {})
    assert not result['ok'] and result['entry_ok'] is False


@pytest.mark.parametrize('cached', [True, False])
def test_cycle_recomputes_symbol_trust_and_uses_closed_bar(tmp_path, monkeypatch, cached):
    monkeypatch.setattr(state, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'paper.sqlite')
    monkeypatch.setattr(runner, 'ASSETS', ['BTCUSDT', 'ETHUSDT'])
    monkeypatch.setattr(runner, 'PAPER_CFG', dict(runner.PAPER_CFG, use_llm=True,
                                                  signal_mode='closed', allow_shorts=False))
    monkeypatch.setattr(runner.events, 'record', lambda *a, **kw: None)
    monkeypatch.setattr(runner, 'WallexPublicClient', lambda: object())
    monkeypatch.setattr(runner.ingest, 'load_candles', lambda *a: None)
    frame = pd.DataFrame({'ts': range(220), 'open': [100.] * 220, 'high': [101.] * 220,
                          'low': [99.] * 220, 'close': [100.] * 220, 'volume': [10.] * 220})
    monkeypatch.setattr(runner, 'refresh_candles', lambda client: {'BTCUSDT': frame, 'ETHUSDT': frame})
    monkeypatch.setattr(runner, 'live_quote', lambda *a: {'bid': 100., 'ask': 100., 'mid': 100., 'last': 100.})
    monkeypatch.setattr(runner, 'donchian_er', lambda df, **kw: {
        'long_entry': pd.Series([False] * len(df)), 'long_exit': pd.Series([False] * len(df))})
    monkeypatch.setattr(state, 'analyst_today', lambda conn, symbol: cached)
    monkeypatch.setattr(state, 'recent_analyst', lambda conn, symbol, limit: ([
        {'bias': 'neutral', 'confidence': .5, 'entry_ok': False, 'size_factor': .5,
         'summary': '', 'support': '', 'resistance': ''}] if cached else []))
    calls = []
    analyzed = []
    def analyze(symbol, df, *args, **kwargs):
        analyzed.append((symbol, int(df.ts.iloc[-1])))
        return {'bias': 'neutral', 'confidence': .5, 'entry_ok': False,
                'size_factor': .5, 'summary': '', 'key_levels': {}, 'ok': True}
    monkeypatch.setattr(runner.analyst, 'analyze', analyze)
    def trust(conn, df, min_acc, *, symbol):
        calls.append((symbol, int(df.ts.iloc[-1])))
        return {'trusted': False, 'conviction': False}
    monkeypatch.setattr(runner, 'ai_trust', trust)
    runner.run_cycle(force=True)
    assert calls == [('BTCUSDT', 218), ('ETHUSDT', 218)]
    assert analyzed == ([] if cached else calls)
