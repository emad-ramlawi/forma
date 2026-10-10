"""Exercise actual launcher processes, signals, and owned browser shutdown."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def command():
    launcher = os.environ.get('FORMA_TEST_LAUNCHER')
    return [launcher] if launcher else [sys.executable, str(ROOT / 'forma.py')]


@contextmanager
def application(tmp_path, *arguments, env=None, ignored_sigint=False):
    launch = command() + list(arguments)
    if ignored_sigint:
        launch = [sys.executable, '-c',
                  'import os,signal,sys;signal.signal(signal.SIGINT,signal.SIG_IGN);os.execv(sys.argv[1],sys.argv[1:])', *launch]
    app = subprocess.Popen(launch, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, env=env, cwd=tmp_path)
    try:
        assert select.select([app.stdout], [], [], 20)[0], 'Launcher did not become ready'
        line = app.stdout.readline()
        assert 'Forma is ready: ' in line, line + app.stderr.read()
        yield app, line.strip().split('ready: ')[1]
    finally:
        if app.poll() is None:
            app.terminate()
            try:
                app.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app.kill(); app.wait(timeout=5)


def port_closed(url):
    parsed = urlsplit(url)
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex((parsed.hostname, parsed.port)) != 0


def test_ctrl_c_overrides_inherited_ignore_and_closes_port(tmp_path):
    with application(tmp_path, '--no-browser', ignored_sigint=True) as (app, url):
        app.send_signal(signal.SIGINT)
        assert app.wait(timeout=10) == 0
        port_closed(url)
        assert 'Traceback' not in app.stderr.read()
    # Restart must work immediately after shutdown.
    with application(tmp_path, '--no-browser', '--port', str(urlsplit(url).port)) as (app, url):
        app.terminate(); assert app.wait(timeout=10) == 0
        port_closed(url)


def fake_browser(tmp_path):
    directory = tmp_path / 'bin'; directory.mkdir()
    log = tmp_path / 'browser.json'
    browser = directory / 'chromium'
    browser.write_text(f'#!{sys.executable}\nimport os,sys,json,time\n'
                       f'open({str(log)!r},"w").write(json.dumps({{"pid":os.getpid(),"arguments":sys.argv}}))\n'
                       'while True: time.sleep(1)\n')
    browser.chmod(0o755)
    env = os.environ.copy(); env['PATH'] = str(directory)
    return log, env


def wait_browser(log):
    until = time.monotonic() + 10
    while time.monotonic() < until:
        if log.exists():
            try:
                return json.loads(log.read_text())
            except json.JSONDecodeError:
                pass
        time.sleep(.02)
    raise AssertionError('Browser was not launched')


def test_closing_owned_browser_stops_server_and_removes_profile(tmp_path):
    log, env = fake_browser(tmp_path)
    with application(tmp_path, env=env) as (app, url):
        browser = wait_browser(log)
        profile = Path(next(a.split('=', 1)[1] for a in browser['arguments'] if a.startswith('--user-data-dir=')))
        assert profile.is_dir()
        assert os.getpgid(browser['pid']) == browser['pid']
        os.kill(browser['pid'], signal.SIGTERM)
        assert app.wait(timeout=10) == 0
        port_closed(url)
        assert not profile.exists()


def test_ctrl_c_also_stops_owned_browser(tmp_path):
    log, env = fake_browser(tmp_path)
    with application(tmp_path, env=env) as (app, url):
        browser = wait_browser(log)
        app.send_signal(signal.SIGINT)
        assert app.wait(timeout=10) == 0
        port_closed(url)
        try:
            os.kill(browser['pid'], 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError('Owned browser was left running')


def test_default_browser_close_reload_grace_and_authentication(tmp_path):
    directory = tmp_path / 'bin'; directory.mkdir()
    opener = directory / 'xdg-open'; opener.write_text(f'#!{sys.executable}\n'); opener.chmod(0o755)
    env = os.environ.copy(); env['PATH'] = str(directory)
    with application(tmp_path, env=env) as (app, url):
        parsed = urlsplit(url); base = f'http://{parsed.netloc}'
        def event(client, action):
            req = Request(base + '/api/window', data=json.dumps({'client': client, 'action': action}).encode(),
                          headers={'X-Forma-Token': parsed.fragment, 'Content-Type': 'application/json'})
            return json.load(urlopen(req, timeout=3))
        event('first', 'open'); event('first', 'close'); event('reloaded', 'open')
        time.sleep(3.2)
        assert app.poll() is None, 'A reload stopped the server'
        event('reloaded', 'close')
        assert app.wait(timeout=10) == 0
        port_closed(url)
