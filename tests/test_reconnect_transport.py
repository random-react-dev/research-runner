"""Real HTTP sockets: server commits, then deliberately drops the reply."""
import importlib.util, json, socket, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest
from test_sessions import setup, Start, Finish
from fastapi import HTTPException

spec=importlib.util.spec_from_file_location('launcher',Path(__file__).parents[1]/'skills/research/scripts/launch.py')
launch=importlib.util.module_from_spec(spec);spec.loader.exec_module(launch)

def test_drop_start_heartbeat_and_finish_replies(setup,tmp_path,monkeypatch):
    store,service,device,data=setup;data.attempt_token='r'*43
    calls={}; dropped=set()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            calls[self.path]=calls.get(self.path,0)+1
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            try:
                if self.headers.get('Authorization')!='Bearer test-auth': raise HTTPException(401,'Invalid')
                if self.path.endswith('/pulse'): reply=service.pulse(device,self.path.split('/')[-2])
                elif self.path.endswith('/finish'): reply=service.finish(device,self.path.split('/')[-2],Finish(**body))
                else:reply=service.start(device,Start(**body))
                status=200
            except HTTPException as e:reply={'detail':e.detail};status=e.status_code
            if status==200 and self.path not in dropped:
                dropped.add(self.path);self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            encoded=json.dumps(reply).encode();self.send_response(status);self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(launch.random,'uniform',lambda *a:0)
    config={'server_url':f'http://127.0.0.1:{server.server_port}','device_token':'test-auth'}
    path='/api/device/research-runner'
    try:
        first=launch.api(config,path,data.model_dump())
        assert calls[path]==2
        endpoint=path+'/'+first['id']
        assert launch.api(config,endpoint+'/pulse',{})=={'stop':False}
        assert calls[endpoint+'/pulse']==2
        pending=tmp_path/'pending-result.json';launch.save(pending,{'exit_code':0,'result':'Recovered report'})
        result=launch.deliver(config,endpoint,pending)
        assert result['state']=='completed' and result['result']=='Recovered report'
        assert calls[endpoint+'/finish']==2 and not pending.exists()
        with store.db() as db:
            assert db.execute('SELECT count(*) FROM tasks').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM research_runner_sessions').fetchone()[0]==1
        before=calls[path]
        with pytest.raises(launch.APIError):launch.api({**config,'device_token':'wrong'},path,data.model_dump())
        assert calls[path]==before+1
    finally:server.shutdown();server.server_close();thread.join()

def test_unacknowledged_report_is_retained(tmp_path,monkeypatch):
    pending=tmp_path/'pending.json';launch.save(pending,{'exit_code':0,'result':'Keep this'})
    def offline(*a,**k):raise ConnectionError('Offline')
    monkeypatch.setattr(launch,'api',offline)
    with pytest.raises(ConnectionError):launch.deliver({},'/session',pending)
    assert launch.read(pending)['result']=='Keep this'
