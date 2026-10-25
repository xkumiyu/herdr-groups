"""Integration test in an isolated Herdr 0.9.5 server and PTY client.

Only the temporary server created here is stopped. No user sessions are changed.
"""

import codecs
import fcntl
import json
import os
import pty
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unicodedata
from pathlib import Path

from groups import TOKENS

ROOT = Path(__file__).resolve().parent


def wait(check, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError(f'Timed out: {check}')


class Screen:
    """Tiny cursor tracker for the test's ASCII labels, not a terminal emulator."""
    def __init__(self, descriptor):
        self.fd = descriptor
        self.cells = {}
        self.x = self.y = 0
        self.buffer = ''
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')

    def drain(self):
        while select.select([self.fd], [], [], .05)[0]:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            self.buffer += self.decoder.decode(data)
        i = 0
        while i < len(self.buffer):
            c = self.buffer[i]
            if c == '\x1b':
                match = re.match(r'\x1b\[([0-9;?<=>]*)([ -/]*)([@-~])', self.buffer[i:])
                if match:
                    raw, _, op = match.groups()
                    numbers = [int(n) if n else 0 for n in raw.split(';')] if not raw.startswith(('?', '>', '=')) else []
                    n = numbers[0] if numbers else 0
                    if op in ('H', 'f'):
                        self.y = (n or 1) - 1
                        self.x = ((numbers[1] if len(numbers) > 1 else 1) or 1) - 1
                    elif op == 'G':
                        self.x = (n or 1) - 1
                    elif op == 'd':
                        self.y = (n or 1) - 1
                    elif op in ('A', 'B'):
                        self.y += (n or 1) * (-1 if op == 'A' else 1)
                    elif op in ('C', 'D'):
                        self.x += (n or 1) * (-1 if op == 'D' else 1)
                    elif op == 'J' and n in (2, 3):
                        self.cells.clear()
                    elif op == 'K':
                        self.cells = {p: v for p, v in self.cells.items() if p[1] != self.y or (n == 0 and p[0] < self.x)}
                    i += len(match.group())
                    continue
                if self.buffer[i:].startswith(('\x1b]', '\x1bP')):
                    match = re.match(r'\x1b[\]P].*?(?:\x07|\x1b\\)', self.buffer[i:], re.DOTALL)
                    if not match:
                        break
                    i += len(match.group())
                    continue
                if len(self.buffer) - i < 3:
                    break
                i += 2
                continue
            if c == '\r':
                self.x = 0
            elif c == '\n':
                self.y += 1
            elif c >= ' ':
                self.cells[(self.x, self.y)] = c
                width = 0 if unicodedata.combining(c) or c in ('\u200b', '\ufe0f') else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1
                if width == 2:
                    self.cells[(self.x + 1, self.y)] = ' '
                self.x += width
            i += 1
        self.buffer = self.buffer[i:]

    def find(self, text):
        self.drain()
        for y in range(50):
            line = ''.join(self.cells.get((x, y), ' ') for x in range(160))
            if text in line:
                return line.index(text), y
        return None


def smoke():
    with tempfile.TemporaryDirectory(prefix='herdr-groups-smoke-') as temporary:
        base = Path(temporary)
        env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
        env.update(XDG_CONFIG_HOME=str(base / 'config'), XDG_STATE_HOME=str(base / 'state'),
                   HERDR_CONFIG_PATH=str(base / 'config.toml'), TERM='xterm-256color')
        original = '''onboarding = false
[terminal]
default_shell = "/bin/sh"
shell_mode = "non_login"
[ui]
mouse_capture = true
[ui.sidebar.agents]
rows = [["state_icon", "agent"]]
'''
        (base / 'config.toml').write_text(original)
        package = base / 'installed-plugin'
        shutil.copytree(ROOT, package, ignore=shutil.ignore_patterns('.git', 'tmp', '__pycache__', '.agents', '.codex', '.aws'))
        cli_base = ['herdr', '--session', f'groups-smoke-{os.getpid()}']

        def cli(*args, check=True):
            return subprocess.run([*cli_base, *args], env=env, cwd=base, check=check,
                                  capture_output=True, text=True, timeout=15)

        def api(*args):
            return json.loads(cli(*args).stdout)['result']

        def spaces():
            return api('workspace', 'list')['workspaces']

        def ids():
            return [w['workspace_id'] for w in spaces()]

        def rpc(method, params):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(10)
                connection.connect(str(socket_path))
                connection.sendall((json.dumps({'id': 'order-test', 'method': method, 'params': params}) + '\n').encode())
                with connection.makefile('rb') as stream:
                    result = json.loads(stream.readline())
            assert 'error' not in result, result
            return result['result']

        def run_plugin(*args, check=True):
            return subprocess.run([sys.executable, str(ROOT / 'plugin.py'), *args], env=plugin_env,
                                  capture_output=True, text=True, timeout=15, check=check)

        def listing():
            return json.loads(run_plugin('list').stdout)

        def action(name):
            log_id = api('plugin', 'action', 'invoke', 'herdr-groups.' + name)['log']['log_id']
            def finished():
                logs = api('plugin', 'log', 'list', '--plugin', 'herdr-groups', '--limit', '100')['logs']
                entry = next((e for e in logs if e['log_id'] == log_id and e['status'] != 'running'), None)
                if entry:
                    assert entry.get('exit_code') == 0, entry
                return entry
            wait(finished)

        def headers():
            return {w['section']: w['workspace_id'] for w in listing()['workspaces'] if w['heading']}

        def section(identifier):
            return next(w['section'] for w in listing()['workspaces'] if w['workspace_id'] == identifier)

        def shell_idle(pane):
            info = api('pane', 'process-info', '--pane', pane)['process_info']
            return bool(info.get('foreground_processes')) and all(p['pid'] == info.get('shell_pid') for p in info['foreground_processes'])

        def publish(identifier, text):
            # Unrelated metadata must survive group updates unchanged.
            cli('workspace', 'report-metadata', identifier, '--source', 'test-provider',
                '--token', 'external_label=' + text)

        master = client = None
        with (base / 'server.log').open('w') as log:
            server = subprocess.Popen([*cli_base, 'server'], env=env, cwd=base, stdout=log, stderr=log)
            try:
                wait(lambda: cli('workspace', 'list', check=False).returncode == 0)
                socket_path = next((base / 'config').rglob('herdr.sock'))
                plugin_env = dict(env, HERDR_ENV='1', HERDR_SOCKET_PATH=str(socket_path))
                cli('plugin', 'link', str(package))
                a = api('workspace', 'create', '--cwd', str(base), '--label', 'alpha', '--no-focus')['workspace']['workspace_id']
                b = api('workspace', 'create', '--cwd', str(base), '--label', 'beta', '--focus')['workspace']['workspace_id']
                assert run_plugin('setup').stdout == (ROOT / 'sidebar.toml').read_text()
                assert (base / 'config.toml').read_text() == original and ids() == [a, b]
                # This is an explicit user config edit, outside plugin execution.
                template = ROOT / 'sidebar.toml'
                configured = original + '\n' + template.read_text()
                (base / 'config.toml').write_text(configured)
                rpc('server.reload_config', {})
                cli('config', 'check')
                action('start')
                publish(a, 'alpha')
                publish(b, 'beta')
                h0 = headers()['default']
                assert ids() == [a, b, h0], 'Setup reordered existing Spaces'
                assert section(a) is None
                run_plugin('add', 'oss', 'blue')
                h1 = headers()['oss']
                foreign = api('workspace', 'create', '--cwd', str(base), '--label', 'default', '--no-focus')['workspace']['workspace_id']
                publish(foreign, 'default')
                publish(h0, '▪ default')
                publish(h1, '▪ oss')
                assert not next(w['heading'] for w in listing()['workspaces'] if w['workspace_id'] == foreign)
                # This simulates a user's drag; the plugin itself never moves a Space.
                wanted = [h0, a, h1, b, foreign]
                rpc('workspace.move_block', {'workspace_ids': wanted})
                run_plugin('sync')
                assert ids() == wanted
                assert section(a) == 'default' and section(b) == 'oss'
                assert next(w for w in spaces() if w['workspace_id'] == a)['tokens']['herdr_groups_spacer'] == '\u200b'
                assert next(w for w in spaces() if w['workspace_id'] == b)['focused']

                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 50, 160, 0, 0))
                client = subprocess.Popen(cli_base, env=env, cwd=base, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
                os.close(slave)
                screen = Screen(master)
                wait(lambda: screen.find('alpha') and screen.find('beta') and screen.find('▪ oss'))
                assert screen.find('alpha')[0] == screen.find('beta')[0] == screen.find('▪ oss')[0] == 1
                x, y = screen.find('▪ oss')
                line = ''.join(screen.cells.get((column, y), ' ') for column in range(1, 20))
                assert line.count('▪ oss') == 1 and ' · ' not in line, line
                assert not ''.join(screen.cells.get((column, y - 1), ' ') for column in range(1, 14)).strip(' \u200b')
                os.write(master, f'\x1b[<0;2;{y}M\x1b[<0;2;{y}m'.encode())
                wait(lambda: next(w for w in spaces() if w['workspace_id'] == a)['focused'])
                os.write(master, f'\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m'.encode())
                wait(lambda: next(w for w in spaces() if w['workspace_id'] == h1)['focused'])
                cli('workspace', 'focus', b)
                # Moving a member changes only its positional section.
                rpc('workspace.move_block', {'workspace_ids': [b], 'before_workspace_id': a})
                run_plugin('sync')
                assert ids() == [h0, b, a, h1, foreign]
                assert section(b) == 'default'
                # Moving a heading is also respected even when definitions have a different order.
                rpc('workspace.move_block', {'workspace_ids': [h1], 'before_workspace_id': h0})
                run_plugin('sync')
                assert ids() == [h1, h0, b, a, foreign]
                assert next(w for w in spaces() if w['workspace_id'] == h1)['tokens']['herdr_groups_spacer'] == '\u200b'
                assert next(w for w in spaces() if w['workspace_id'] == a)['tokens']['external_label'] == 'alpha'
                assert not any(k.startswith('herdr_groups_name') for w in spaces() for k in w.get('tokens', {}))
                for removed in ('assign', 'order', 'icon', 'exclude', 'include', 'migrate'):
                    assert run_plugin(removed, check=False).returncode != 0
                run_plugin('color', 'oss', 'orange')
                run_plugin('rename', 'oss', 'projects')
                assert headers()['projects'] == h1
                assert next(w for w in spaces() if w['workspace_id'] == h1)['tokens']['herdr_groups_orange'] == '▪ projects'
                assert listing()['version'] == 2
                assert 'assign' not in json.loads(Path(listing()['state_file']).read_text())
                # Native actions resolve the copied package root from an
                # unrelated caller cwd, without exposing any installation path.
                unchanged = ids()
                action('setup')
                wait(lambda: screen.find('Press Enter to close:'))
                assert (base / 'config.toml').read_text() == configured and ids() == unchanged
                os.write(master, b'\r')
                wait(lambda: not screen.find('Press Enter to close:'))
                action('status')
                wait(lambda: screen.find('Updates: running'))
                assert ids() == unchanged
                os.write(master, b'\r')
                wait(lambda: not screen.find('Press Enter to close:'))
                action('add')
                wait(lambda: screen.find('Group name:'))
                os.write(master, b'native group\r')
                wait(lambda: screen.find('Color [grey]:'))
                os.write(master, b'\r')
                native = wait(lambda: headers().get('native group'))
                action('rename')
                wait(lambda: screen.find('Current name:'))
                os.write(master, b'native group\r')
                wait(lambda: screen.find('New name:'))
                os.write(master, b'native renamed\r')
                wait(lambda: headers().get('native renamed') == native)
                action('color')
                wait(lambda: screen.find('Group name:'))
                os.write(master, b'native renamed\r')
                wait(lambda: screen.find('Color:'))
                os.write(master, b'cyan\r')
                wait(lambda: next(w for w in spaces() if w['workspace_id'] == native).get('tokens', {}).get('herdr_groups_cyan') == '▪ native renamed')
                action('delete')
                wait(lambda: screen.find('Group name to delete:'))
                os.write(master, b'native renamed\r')
                wait(lambda: native not in ids())
                assert ids() == unchanged
                # Invalid input is shown in the popup without creating a group.
                action('add')
                wait(lambda: screen.find('Group name:'))
                os.write(master, b'invalid group\r')
                wait(lambda: screen.find('Color [grey]:'))
                os.write(master, b'invalid\r')
                wait(lambda: screen.find('invalid choice'))
                assert ids() == unchanged and 'invalid group' not in headers()
                os.write(master, b'\r')
                wait(lambda: not screen.find('Press Enter to close:'))
                # Closing an input popup without submitting leaves state intact.
                action('delete')
                wait(lambda: screen.find('Group name to delete:'))
                rpc('popup.close', {})
                assert ids() == unchanged
                assert (base / 'config.toml').read_text() == configured
                print(f'PASS: path-independent native setup/start/status/add/rename/color/delete actions, invalid input and cancellation')
                # Group operations use the existing popup without creating another Space.
                count = len(spaces())
                api('plugin', 'action', 'invoke', 'herdr-groups.manage')
                wait(lambda: screen.find('groups>'))
                assert len(spaces()) == count
                os.write(master, b'q\r')
                client.terminate()
                client.wait(timeout=10)
                client = None
                os.close(master)
                master = None
                print(f'PASS: native display, no name copying/watcher, header deduplication, manual order, gaps/clicks and popup')

                # Real worktree provenance never causes plugin reordering or saved assignments.
                repo, checkout = base / 'repo', base / 'linked'
                subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
                subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                                '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'fixture'], check=True, capture_output=True)
                subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '-b', 'topic', str(checkout)], check=True, capture_output=True)
                parent = api('workspace', 'create', '--cwd', str(repo), '--label', 'parent', '--no-focus')['workspace']['workspace_id']
                child = api('worktree', 'open', '--workspace', parent, '--path', str(checkout), '--no-focus')['workspace']['workspace_id']
                publish(parent, 'parent')
                publish(child, 'topic')
                assert next(w for w in spaces() if w['workspace_id'] == child)['worktree']['is_linked_worktree']
                rpc('workspace.move_block', {'workspace_ids': [parent, child], 'before_workspace_id': h0})
                before = ids()
                run_plugin('sync')
                assert ids() == before
                assert section(parent) == section(child) == 'projects'
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as subscription:
                    subscription.connect(str(socket_path))
                    subscription.sendall(b'{"id":"test","method":"events.subscribe","params":{"subscriptions":[{"type":"workspace.reordered"},{"type":"workspace.metadata_updated"}]}}\n')
                    subscription.recv(65536)
                    run_plugin('sync')
                    if select.select([subscription], [], [], .3)[0]:
                        raise AssertionError(f'No-op emitted events: {subscription.recv(65536)!r}')
                cli('server', 'stop')
                server.wait(timeout=10)
                server = subprocess.Popen([*cli_base, 'server'], env=env, cwd=base, stdout=log, stderr=log)
                wait(lambda: cli('workspace', 'list', check=False).returncode == 0)
                wait(lambda: next(w for w in spaces() if w['workspace_id'] == h1).get('tokens', {}).get('herdr_groups_orange') == '▪ projects')
                assert ids() == before
                assert (base / 'config.toml').read_text() == configured
                # Deleting a group refuses an in-use heading; then removes only that heading.
                pane = api('pane', 'list', '--workspace', h1)['panes'][0]['pane_id']
                wait(lambda: shell_idle(pane))
                cli('pane', 'run', pane, 'sleep 2')
                wait(lambda: not shell_idle(pane))
                assert run_plugin('delete', 'projects', check=False).returncode != 0
                assert ids() == before and headers()['projects'] == h1
                wait(lambda: shell_idle(pane))
                run_plugin('delete', 'projects')
                assert ids() == [i for i in before if i != h1]
                assert section(parent) is None  # Now above the first remaining heading.
                before = ids()
                action('stop')
                assert ids() == before, 'Stop must preserve heading positions'
                assert (base / 'config.toml').read_text() == configured
                assert next(w for w in spaces() if w['workspace_id'] == h0)['tokens']['herdr_groups_grey'] == '▪ default'
                cli('workspace', 'rename', b, 'renamed')
                run_plugin('restore')
                assert ids() == before and not listing()['enabled']
                # A stopped cold restore rehydrates existing heading display,
                # without enabling sync, creating another Space or editing config.
                cli('server', 'stop')
                server.wait(timeout=10)
                server = subprocess.Popen([*cli_base, 'server'], env=env, cwd=base, stdout=log, stderr=log)
                wait(lambda: cli('workspace', 'list', check=False).returncode == 0)
                wait(lambda: next(w for w in spaces() if w['workspace_id'] == h0).get('tokens', {}).get('herdr_groups_grey') == '▪ default')
                assert ids() == before and not listing()['enabled']
                assert (base / 'config.toml').read_text() == configured
                action('start')
                assert ids() == before
                assert (base / 'config.toml').read_text() == configured
                run_plugin('delete', 'default')
                assert listing()['groups'] == []
                remaining = ids()
                run_plugin('sync')
                assert ids() == remaining and not headers()
                run_plugin('add', 'new', 'blue')
                fresh = headers()['new']
                assert ids() == remaining + [fresh]
                action('unsetup')
                assert ids() == remaining and foreign in ids()
                assert (base / 'config.toml').read_text() == configured
                assert not any(k in TOKENS for w in spaces() for k in w.get('tokens', {}))
                cli('config', 'check')
                print(f'PASS: config never modified, safe deletion, active/stopped cold restore, stop/start and user order preserved')
            except BaseException:
                print((base / 'server.log').read_text()[-3000:])
                print('SPACES', spaces())
                print(cli('plugin', 'log', 'list', '--plugin', 'herdr-groups', '--limit', '10', check=False).stdout)
                raise
            finally:
                if client is not None:
                    client.terminate()
                    client.wait(timeout=10)
                if master is not None:
                    os.close(master)
                if server.poll() is None:
                    if 'plugin_env' in locals():
                        run_plugin('stop', check=False)
                    cli('server', 'stop', check=False)
                    server.wait(timeout=10)


if __name__ == '__main__':
    smoke()


