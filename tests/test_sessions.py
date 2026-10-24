import json, sys, time, hashlib, os
from pathlib import Path
import pytest
from fastapi import HTTPException
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,os.environ.get('RESEARCH_CONTROL_SOURCE',str(ROOT/'production/server')))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from store import Store
from research_runner import Sessions, Start, Finish

@pytest.fixture
def setup(tmp_path,monkeypatch):
    catalog=tmp_path/'models.json';catalog.write_text('{}');monkeypatch.setenv('HELPER_BOOTSTRAP_MODELS',str(catalog))
    s=Store(tmp_path/'state.db','a'*40,'b'*40)
    device='runner-test-device-123456';s.redeem(s.enroll()['code'],device,'nonce-12345678901234567890','test','darwin')
    runner=Sessions(s);s.research_runner=runner
    with s.db() as db:db.execute("UPDATE research_runner_policy SET helper='skip'")
    data=Start(question='Research one official source',provider='codex',key='a'*64,root='/tmp/research-runner-test',python='/usr/bin/python3',cli_paths={'codex':'/usr/bin/codex'})
    return s,runner,device,data

def test_session_authority_completion_and_dedup(setup):
    s,r,d,data=setup;start=r.start(d,data)
    p=r.authorize(start['model_token'],'codex');assert 'do not install' in p['server_instructions']
    assert r.authorize(start['model_token'],'claude') is None
    with pytest.raises(HTTPException):r.start(d,data)
    assert r.pulse(d,start['id'])=={'stop':False}
    out=r.finish(d,start['id'],Finish(exit_code=0,result='Report with https://example.com'))
    assert out['state']=='completed'
    assert r.authorize(start['model_token'],'codex') is None
    assert r.start(d,data)['finished']
    assert r.status(d,start['id'])['result']['report'].startswith('Report')

def test_stop_and_required_gate(setup):
    s,r,d,data=setup;start=r.start(d,data)
    s.stop_task(start['task_id'])
    assert r.pulse(d,start['id'])['stop']
    assert r.authorize(start['model_token'],'codex') is None
    assert r.finish(d,start['id'],Finish(exit_code=1,result='Stopped'))['state']=='cancelled'

def test_required_helper_cannot_be_claimed_ready(setup):
    s,r,d,data=setup
    with s.db() as db:
        db.execute("UPDATE research_runner_policy SET helper='required'")
        db.execute('CREATE TABLE helper_bootstrap_sessions(device_id TEXT,status TEXT)')
    start=r.start(d,data)
    result=r.finish(d,start['id'],Finish(exit_code=0,result='I installed it'))
    assert result['state']=='failed' and 'verified live helper' in result['error']

def test_instruction_injection_uses_current_rules(setup):
    s,r,d,data=setup;start=r.start(d,data)
    with s.db() as db:
        db.execute('CREATE TABLE manager_rules(scope TEXT,instructions TEXT)')
        db.execute('INSERT INTO manager_rules VALUES (?,?)',('global','Use official sources only'))
    assert 'Use official sources only' in r.authorize(start['model_token'],'codex')['server_instructions']
