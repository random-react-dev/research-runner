#!/usr/bin/env python3
"""Connect a local research runner to the owner's server."""
import http.client, random, secrets
import argparse, fcntl, hashlib, json, os, platform, signal, shutil, subprocess, sys, time, uuid
from pathlib import Path
import urllib.request, urllib.error, urllib.parse
SERVER = 'https://ask-gateway.r95426882.workers.dev'
MAX_RESPONSE = 1024 * 1024
def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read(path):
    return json.loads(Path(path).read_text())


def origin(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('Server must use HTTPS (loopback HTTP is allowed for tests)')
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Server must be a clean origin')
    return value.rstrip('/')


class APIError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('Server returned HTTP ' + str(code))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_once(config, path, body=None, authenticated=True, timeout=10):
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'User-Agent': 'helper-runner/1.0'}
    if authenticated:
        headers['Authorization'] = 'Bearer ' + config['device_token']
    request = urllib.request.Request(origin(config['server_url']) + path,
                                    None if body is None else json.dumps(body).encode(), headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise RuntimeError('Server response exceeds 1 MiB')
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        raise APIError(code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected):
        raise ConnectionError('Server connection failed') from None


def api(config, path, body=None, authenticated=True):
    # Retry only reads and runner operations whose server semantics are replay-safe.
    retryable=body is None or path.startswith('/api/device/research-runner')
    deadline=time.monotonic()+60; delay=1
    while True:
        try: return request_once(config,path,body,authenticated,timeout=max(0.1,min(10,deadline-time.monotonic())))
        except APIError as error:
            if error.code not in (408,429,500,502,503,504): raise
            failure=error
        except ConnectionError as error: failure=error
        remaining=deadline-time.monotonic()
        if not retryable or remaining<=0: raise failure
        time.sleep(min(remaining,delay+random.uniform(0,0.25)))
        delay=min(delay*2,8)


def deliver(config, endpoint, path):
    response=api(config,endpoint+'/finish',read(path))
    path.unlink()  # Only remove the durable result after server acknowledgement.
    return response


def command(provider, binary, directory, settings):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SHELL', 'USER', 'LOGNAME', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
    environment['HOME'] = str(Path.home())
    model = settings['model']
    if provider == 'codex':
        home = directory / 'codex-home'
        home.mkdir(mode=0o700, exist_ok=True)
        environment.update(CODEX_HOME=str(home), HELPER_RUNNER_MODEL_TOKEN=settings['model_token'])
        values = {'model': model, 'model_provider': 'helper_runner', 'approval_policy': 'never',
                  'sandbox_mode': 'danger-full-access', 'web_search': 'disabled', 'notify': [],
                  'model_reasoning_effort': 'none', 'mcp_servers.research.url':settings['research_url'], 'mcp_servers.research.bearer_token_env_var':'HELPER_RUNNER_MODEL_TOKEN', 'mcp_servers.research.http_headers':{'x-device-provider':provider}, 'model_providers.helper_runner.name': 'Helper Runner',
                  'model_providers.helper_runner.base_url': settings['base_url'],
                  'model_providers.helper_runner.env_key': 'HELPER_RUNNER_MODEL_TOKEN',
                  'model_providers.helper_runner.wire_api': 'responses',
                  'model_providers.helper_runner.requires_openai_auth': False,
                  'model_providers.helper_runner.supports_websockets': False}
        if settings.get('model_catalog'):
            catalog = directory / 'models.json'
            save(catalog, settings['model_catalog'])
            values['model_catalog_json'] = str(catalog)
        args = [binary, 'exec', '--json', '--ephemeral', '--skip-git-repo-check', '--ignore-user-config',
                '--sandbox', 'danger-full-access', '-C', str(Path.home()), '-o', str(directory / 'result.txt')]
        for feature in ('plugins', 'remote_plugin', 'computer_use', 'browser_use', 'apps', 'hooks', 'multi_agent', 'multi_agent_v2', 'goals'):
            args += ['--disable', feature]
        for key, value in values.items():
            args += ['-c', key + '=' + ('{'+','.join(json.dumps(k)+'='+json.dumps(v) for k,v in value.items())+'}' if isinstance(value,dict) else json.dumps(value))]
        args += ['-']
    else:
        home = directory / 'claude-home'
        home.mkdir(mode=0o700, exist_ok=True)
        environment.update(CLAUDE_CONFIG_DIR=str(home), ANTHROPIC_AUTH_TOKEN=settings['model_token'],
                           ANTHROPIC_BASE_URL=settings['base_url'], ANTHROPIC_MODEL=model,
                           ANTHROPIC_DEFAULT_OPUS_MODEL=model, ANTHROPIC_DEFAULT_SONNET_MODEL=model,
                           ANTHROPIC_DEFAULT_HAIKU_MODEL=model, CLAUDE_CODE_SUBAGENT_MODEL=model,
                           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', DISABLE_AUTOUPDATER='1',
                           MAX_THINKING_TOKENS='0',
                           CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL='1')
        mcp = directory / 'mcp.json'
        save(mcp, {'mcpServers': {'research': {'type':'http','url':settings['research_url'],'headers':{'Authorization':'Bearer '+settings['model_token'],'x-device-provider':provider}}}})
        args = [binary, '-p', '--bare', '--permission-mode', 'bypassPermissions', '--dangerously-skip-permissions', '--permission-prompts', 'none',
                '--no-chrome', '--no-session-persistence', '--setting-sources', '', '--strict-mcp-config',
                '--mcp-config', str(mcp), '--settings', '{"disableAllHooks":true,"alwaysThinkingEnabled":false}',
                '--effort', 'low',
                '--tools', 'Bash,Edit,Write,Read,Glob,Grep', '--output-format', 'stream-json', '--verbose', '--model', model]
    return args, environment


def register(state, server):
    path = state / 'device.json'
    if path.exists():
        config = read(path)
        if origin(config['server_url']) != server:
            raise RuntimeError('This state directory belongs to another service')
        return config
    intent_path = state / 'enrollment-intent.json'
    if not intent_path.exists():
        save(intent_path, {'device_id':str(uuid.uuid4()), 'enrollment_nonce':str(uuid.uuid4())})
    intent = read(intent_path)
    config = {'server_url':server, 'state_dir':str(state)}
    if 'code' not in intent:
        intent['code'] = api(config, '/api/admin/enrollments', {'ttl_seconds':600}, False)['code']
        save(intent_path, intent)
    try:
        enrolled = api(config, '/api/enroll/redeem', {**intent, 'label':'Research PC', 'platform':'darwin'}, False)
    except APIError as error:
        if error.code != 401:
            raise
        intent['code'] = api(config, '/api/admin/enrollments', {'ttl_seconds':600}, False)['code']
        save(intent_path, intent)
        enrolled = api(config, '/api/enroll/redeem', {**intent, 'label':'Research PC', 'platform':'darwin'}, False)
    config.update(device_id=enrolled['device_id'], device_token=enrolled['device_token'])
    save(path, config)
    return config


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('question', nargs='?')
    parser.add_argument('--provider', choices=['codex','claude'], default='codex')
    parser.add_argument('--state-dir', default=str(Path.home()/'Library/Application Support/ResearchRunner'))
    parser.add_argument('--status', action='store_true')
    args=parser.parse_args()
    def interrupted(signum, frame): raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    os.umask(0o077)
    state=Path(args.state_dir).expanduser().resolve(); state.mkdir(parents=True,exist_ok=True,mode=0o700)
    if args.status:
        receipt=read(state/'session.json'); config=read(state/'device.json')
        print(json.dumps(api(config,'/api/device/research-runner/'+receipt['id']),indent=2)); return
    if not args.question: parser.error('Provide a research question')
    if sys.platform!='darwin': raise RuntimeError('macOS is required')
    binary=shutil.which(args.provider)
    if not binary: raise RuntimeError('Install '+args.provider+' first')
    with (state/'task-agent.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('A runner is already active; use --status')
        config=register(state,SERVER)
        key=hashlib.sha256((args.provider+'\0'+args.question).encode()).hexdigest()
        intent_path=state/'requests'/(key+'.json')
        if intent_path.exists(): intent=read(intent_path)
        else:
            intent={'question':args.question,'provider':args.provider,'key':key,'root':str(state),'python':sys.executable,'cli_paths':{p:shutil.which(p) for p in ('codex','claude') if shutil.which(p)},'attempt_token':secrets.token_urlsafe(32)}
            save(intent_path,intent)
        receipt_path=state/'requests'/(key+'.receipt.json')
        receipt=read(receipt_path) if receipt_path.exists() else {}
        if receipt.get('id'):
            pending=state/'runs'/receipt['id']/'pending-result.json'
            if pending.exists():
                response=deliver(config,'/api/device/research-runner/'+receipt['id'],pending)
                print(json.dumps(response,indent=2))
                if response.get('state')!='completed': raise SystemExit(1)
                return
        settings=api(config,'/api/device/research-runner',intent)
        receipt.update(id=settings['id'],task_id=settings['task_id'])
        save(receipt_path,receipt); save(state/'session.json',receipt)
        if settings.get('finished'):
            print(json.dumps(settings));
            if settings.get('state')!='completed': raise SystemExit(1)
            return
        if receipt.get('process_started'):
            raise RuntimeError('Previous agent was started. Inspect --status; automatic agent relaunch is disabled to prevent duplicate execution.')
        directory=state/'runs'/settings['id']
        command_args,env=command(args.provider,binary,directory,settings)
        # Connection context only; all work and setup decisions arrive through the server API.
        env['RESEARCH_RUNNER_STATE']=str(state)
        endpoint='/api/device/research-runner/'+settings['id']
        result=''; code=1; process=None
        try:
            with (directory/'events.jsonl').open('w') as out, (directory/'stderr.log').open('w') as err:
                receipt['process_started']=True; save(receipt_path,receipt)
                process=subprocess.Popen(command_args,stdin=subprocess.PIPE,stdout=out,stderr=err,env=env,cwd=Path.home(),start_new_session=True,text=True)
                process.stdin.write(args.question); process.stdin.close()
                deadline=time.monotonic()+settings['timeout_seconds']
                while process.poll() is None:
                    if time.monotonic()>deadline: raise RuntimeError('Runner deadline reached')
                    reply=api(config,endpoint+'/pulse',{})
                    if reply.get('stop'): raise RuntimeError('Server requested stop or pause')
                    time.sleep(5)
                code=process.returncode
            if args.provider=='codex' and (directory/'result.txt').exists(): result=(directory/'result.txt').read_text()[:200000]
            else:
                with (directory/'events.jsonl').open() as stream:
                    for line in stream:
                        try:
                            event=json.loads(line)
                            if event.get('type')=='result':
                                result=event.get('result','')[:200000]
                                if event.get('is_error'): code=1
                        except ValueError: pass
        except (Exception,KeyboardInterrupt) as error:
            result=str(error); code=1
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL); process.wait()
        for secret in (settings['model_token'],config['device_token']): result=result.replace(secret,'[redacted]')
        pending=directory/'pending-result.json'
        save(pending,{'exit_code':code,'result':result})
        response=deliver(config,endpoint,pending)
        print(json.dumps(response,indent=2))
        if code or response.get('state')!='completed': raise SystemExit(1)

if __name__=='__main__':
    try: main()
    except Exception as error: print(str(error),file=sys.stderr); raise SystemExit(1)
