#!/usr/bin/env python3
"""Herdr Groups: Spaces only, Python 3.11+, Unix."""

import argparse
import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from groups import State, TOKENS, COLORS, PREFIX, plan
import headings

PLUGIN_ID = SOURCE = 'herdr-groups'
MAX_MESSAGE = 4 * 1024 * 1024
PROMPTS = {
    'add': ('Group name: ', 'Color [grey]: '),
    'rename': ('Current name: ', 'New name: '),
    'color': ('Group name: ', 'Color: '),
    'delete': ('Group name to delete: ',),
}
OPERATIONS = (*PROMPTS, 'status', 'setup')


class Context:
    def __init__(self):
        if os.environ.get('HERDR_ENV') != '1' or not os.environ.get('HERDR_SOCKET_PATH'):
            raise ValueError('Run inside a Herdr pane or plugin action')
        self.endpoint = os.environ['HERDR_SOCKET_PATH']
        state_root = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
        root = Path(os.environ.get('HERDR_PLUGIN_STATE_DIR', state_root / 'herdr/plugins' / PLUGIN_ID))
        scope = hashlib.sha256(os.path.realpath(self.endpoint).encode()).hexdigest()[:16]
        self.directory = root / scope
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state = self.directory / 'groups.json'
        self.enabled = self.directory / 'enabled'
        self.stopped = self.directory / 'stopped'

    def rpc(self, method, params=None):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(self.endpoint)
            connection.sendall((json.dumps({'id': PLUGIN_ID, 'method': method, 'params': params or {}}) + '\n').encode())
            with connection.makefile('rb') as stream:
                line = stream.readline(MAX_MESSAGE + 1)
            if len(line) > MAX_MESSAGE or not line.endswith(b'\n'):
                raise ValueError('Incomplete or oversized API response')
            response = json.loads(line)
            if 'error' in response:
                raise RuntimeError(f'{method}: {response["error"]}')
            return response['result']

    def workspaces(self):
        return self.rpc('workspace.list')['workspaces']

    def report(self, identifier, tokens):
        self.rpc('workspace.report_metadata', {'workspace_id': identifier, 'source': SOURCE, 'tokens': tokens})


def sync(context, state, create=True):
    """Called under the session lock. Reads authoritative state, only patches changes."""
    records = headings.load(context)
    for workspace in context.workspaces():
        if workspace['workspace_id'] not in records and workspace['label'].startswith(PREFIX):
            raise ValueError(f'The prefix {PREFIX!r} is reserved for headings; rename ordinary Space {workspace["workspace_id"]}')
    records = headings.reconcile(context, state, create=create)
    workspaces = context.workspaces()
    _, reports = plan(state, workspaces, records)
    for workspace in workspaces:
        tokens = reports[workspace['workspace_id']]
        if any(workspace.get('tokens', {}).get(k) != v for k, v in tokens.items()):
            context.report(workspace['workspace_id'], tokens)


def clear(context, retain=()):
    for workspace in context.workspaces():
        if workspace['workspace_id'] not in retain and any(k in workspace.get('tokens', {}) for k in TOKENS):
            context.report(workspace['workspace_id'], dict.fromkeys(TOKENS))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('setup', 'unsetup', 'start', 'restore', 'stop', 'sync', 'status', 'list', 'manage', 'menu'):
        commands.add_parser(command)
    prompt = commands.add_parser('prompt')
    prompt.add_argument('operation', choices=OPERATIONS)
    form = commands.add_parser('dialog')
    form.add_argument('operation', nargs='?', choices=OPERATIONS,
                      default=os.environ.get('HERDR_GROUPS_OPERATION'))
    add = commands.add_parser('add')
    add.add_argument('name')
    add.add_argument('color', nargs='?', choices=COLORS, default='grey')
    rename = commands.add_parser('rename')
    rename.add_argument('old')
    rename.add_argument('new')
    color = commands.add_parser('color')
    color.add_argument('name')
    color.add_argument('color', choices=COLORS)
    delete = commands.add_parser('delete')
    delete.add_argument('name')
    args = parser.parse_args()
    try:
        if args.command == 'setup':
            print(Path(__file__).with_name('sidebar.toml').read_text(), end='')
            return
        context = Context()
        if args.command == 'prompt':
            context.rpc('plugin.pane.open', {'plugin_id': PLUGIN_ID, 'entrypoint': 'operation',
                        'placement': 'popup', 'env': {'HERDR_GROUPS_OPERATION': args.operation}})
            return
        if args.command == 'dialog':
            dialog(context, args.operation)
            return
        if args.command == 'manage':
            context.rpc('plugin.pane.open', {'plugin_id': PLUGIN_ID, 'entrypoint': 'manager', 'placement': 'popup'})
            return
        if args.command == 'menu':
            menu(context)
            return
        with (context.directory / 'state.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            execute(context, args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'herdr-groups: {error}\n')


def execute(context, args):
    command = args.command
    if command in ('stop', 'unsetup'):
        context.enabled.unlink(missing_ok=True)
        context.stopped.touch()
        if command == 'unsetup':
            retained = headings.remove(context)
            clear(context, retain=retained)
        print('Herdr Groups updates stopped; config and definitions retained.')
        return
    if command == 'sync' and not context.enabled.exists():
        return
    if command == 'restore':
        if context.stopped.exists():
            if context.state.exists():
                sync(context, State.load(context.state), create=False)
            return
        command = 'start'
    state = State.load(context.state)
    if command in ('list', 'status'):
        records = headings.load(context)
        section = None
        listing = []
        for workspace in context.workspaces():
            identifier = workspace['workspace_id']
            if identifier in records:
                section = records[identifier]['group']
            listing.append({'workspace_id': identifier, 'label': workspace['label'],
                            'number': workspace.get('number'), 'section': section,
                            'heading': identifier in records})
        print(json.dumps({'enabled': context.enabled.exists(), 'state_file': str(context.state),
                          **state.data, 'workspaces': listing}, ensure_ascii=False, indent=2))
        return
    if command == 'add':
        state.add(args.name, args.color)
    elif command == 'delete':
        state.require(args.name)
        if headings.remove(context, group=args.name):
            raise ValueError('Heading is modified or in use; resolve it before deleting the group')
        state.remove(args.name)
    elif command == 'rename':
        state.rename(args.old, args.new)
        headings.rename(context, args.old, args.new)
    elif command == 'color':
        state.color(args.name, args.color)
    # Saving once upgrades v1 to group definitions only, preserving its backup.
    if command != 'sync' or not context.state.exists() or json.loads(context.state.read_text()).get('version') != 2:
        state.save(context.state)
    if command == 'start':
        was_enabled = context.enabled.exists()
        try:
            sync(context, state)
            context.enabled.touch()
            context.stopped.unlink(missing_ok=True)
            for obsolete in ('dummy', 'resume-combined'):
                (context.directory / obsolete).unlink(missing_ok=True)
        except Exception:
            if not was_enabled:
                context.enabled.unlink(missing_ok=True)
                clear(context)
            raise
    elif context.enabled.exists():
        sync(context, state)


def dialog(context, operation):
    """Single-operation input, independent of the installation directory."""
    if operation not in OPERATIONS:
        raise ValueError('Unknown group operation')
    try:
        if operation == 'setup':
            print(Path(__file__).with_name('sidebar.toml').read_text())
            input('Press Enter to close: ')
            return
        state = State.load(context.state)
        print('Herdr Groups — ' + operation)
        if operation == 'status':
            records = headings.load(context)
            names = [records[w['workspace_id']]['group'] for w in context.workspaces()
                     if w['workspace_id'] in records and records[w['workspace_id']]['group'] in state.names]
            names.extend(name for name in state.names if name not in names)
            print('Updates: ' + ('running' if context.enabled.exists() else 'stopped'))
            for name in names:
                group = next(g for g in state.data['groups'] if g['name'] == name)
                print(f'▪ {name} ({group["color"]})')
            input('Press Enter to close: ')
            return
        print('Groups: ' + ', '.join(state.names))
        if operation in ('add', 'color'):
            print('Colors: ' + ', '.join(COLORS))
        values = [input(label).strip() for label in PROMPTS[operation]]
        if operation == 'add' and not values[1]:
            values[1] = 'grey'
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), operation, *values],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            print((result.stdout + result.stderr).strip() or 'Operation failed')
            input('Press Enter to close: ')
    except (EOFError, KeyboardInterrupt):
        return


def menu(context):
    """Small terminal menu; arrange normal Spaces directly in Herdr."""
    import shlex
    while True:
        for group in State.load(context.state).data['groups']:
            print(f'▪ {group["name"]} ({group["color"]})')
        print('\nHerdr Groups — add NAME COLOR | rename OLD NEW | color NAME COLOR')
        print('delete NAME — removes only the heading; arrange Spaces in Herdr')
        print('list | setup | start | stop | unsetup | q')
        print('Colors: ' + ', '.join(COLORS))
        try:
            line = input('groups> ').strip()
        except EOFError:
            return
        if line in ('q', 'quit', 'exit'):
            return
        try:
            argv = shlex.split(line)
        except ValueError as error:
            print(error)
            continue
        if not argv:
            continue
        if argv[0] in ('manage', 'menu', 'sync'):
            print('Choose one of the commands above')
            continue
        subprocess.run([sys.executable, str(Path(__file__).resolve()), *argv], check=False)


if __name__ == '__main__':
    main()
