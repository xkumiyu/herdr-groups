import copy
import json
import tempfile
import unittest
import os
import subprocess
import sys
import tomllib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from groups import State, plan, ICON_TOKENS, TOKENS, HEADER_TOKEN
from plugin import sync, execute
import headings
import plugin


def space(identifier, label='same', repo=None, child=False):
    value = {'workspace_id': identifier, 'label': label}
    if repo:
        value['worktree'] = {'repo_key': repo, 'is_linked_worktree': child}
    return value


class OrderTest(unittest.TestCase):
    def test_normal_space_tokens_are_never_read_or_republished(self):
        class UnreadableTokens(dict):
            def get(self, *args):
                raise AssertionError('Groups must not inspect another display provider')
        workspace = space('a', 'actual')
        workspace['tokens'] = UnreadableTokens()
        _, reports = plan(State(), [workspace])
        self.assertTrue(all(value is None for value in reports['a'].values()))
        self.assertFalse(any(k.startswith('herdr_groups_name') for k in reports['a']))

    def test_setup_only_prints_a_template_without_herdr_or_config_changes(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / 'config.toml'
            config.write_text('user_owned = true\n')
            env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
            env.update(HERDR_CONFIG_PATH=str(config), XDG_STATE_HOME=str(Path(root) / 'state'))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('plugin.py')), 'setup'],
                                    env=env, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, Path(__file__).with_name('sidebar.toml').read_text())
            self.assertEqual(config.read_text(), 'user_owned = true\n')
            self.assertFalse((Path(root) / 'state').exists())

    def test_order_is_authoritative_and_only_boundaries_receive_gaps(self):
        state = State()
        state.add('oss', 'blue')
        records = {'h0': {'group': 'default'}, 'h1': {'group': 'oss'}}
        # Same repo straddles a heading: the plugin does not relocate anything.
        ws = [space('loose'), space('h1'), space('c', repo='R', child=True),
              space('h0'), space('p', repo='R'), space('same-name')]
        before = copy.deepcopy((state.data, ws))
        ordered, tokens = plan(state, ws, records)
        self.assertEqual(ordered, ws)
        self.assertEqual((state.data, ws), before)
        self.assertEqual(tokens['h1']['herdr_groups_blue'], '▪ oss')
        self.assertEqual(tokens['h0']['herdr_groups_grey'], '▪ default')
        self.assertEqual(tokens['c']['herdr_groups_spacer'], '\u200b')
        self.assertTrue(all(tokens[i]['herdr_groups_spacer'] is None for i in ('loose', 'h0', 'h1', 'p', 'same-name')))
        moved = [ws[0], ws[3], ws[4], ws[5], ws[1], ws[2]]
        _, changed = plan(state, moved, records)
        self.assertEqual(changed['same-name']['herdr_groups_spacer'], '\u200b')
        self.assertIsNone(changed['c']['herdr_groups_spacer'])
        for i in ('loose', 'c', 'p', 'same-name'):
            self.assertTrue(all(tokens[i][k] is None for k in ICON_TOKENS))

    def test_empty_sections_and_no_groups(self):
        state = State()
        state.add('oss', 'blue')
        ws = [space('h0'), space('h1'), space('a', 'actual')]
        _, tokens = plan(state, ws, {'h0': {'group': 'default'}, 'h1': {'group': 'oss'}})
        self.assertEqual(tokens['h0']['herdr_groups_spacer'], '\u200b')
        self.assertTrue(all(v is None for v in tokens['a'].values()))
        state.remove('default')
        state.remove('oss')
        self.assertEqual(state.data, {'version': 2, 'groups': []})
        self.assertEqual(plan(state, [ws[-1]])[0], [ws[-1]])

    def test_v1_migration_keeps_only_group_definitions_and_original_backup(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'groups.json'
            original = json.dumps({'version': 1, 'groups': [{'name': 'oss', 'color': 'blue', 'icon': '🔹'}],
                                   'assign': {'space:a': 'oss'}, 'fallback': 'oss', 'included': [], 'excluded': []})
            path.write_text(original)
            state = State.load(path)
            self.assertEqual(state.data, {'version': 2, 'groups': [{'name': 'oss', 'color': 'blue'}]})
            self.assertEqual(path.read_text(), original)
            state.save(path)
            backup = path.with_name('groups.v1.backup.json')
            self.assertEqual(backup.read_text(), original)
            state.rename('oss', 'projects')
            state.color('projects', 'orange')
            state.save(path)
            self.assertEqual(State.load(path).data, state.data)
            self.assertEqual(backup.read_text(), original)
            for invalid in ('', 'a\nb', 'x' * 81):
                with self.assertRaises(ValueError):
                    state.add(invalid)
            with self.assertRaises(ValueError):
                state.color('projects', 'invalid')

    def test_sync_never_reorders_and_noop_has_no_updates(self):
        with tempfile.TemporaryDirectory() as root:
            state = State()
            live = [space('b'), space('a', repo='R')]
            context = SimpleNamespace(directory=Path(root), workspaces=lambda: copy.deepcopy(live))
            context.rpc = Mock(side_effect=AssertionError('sync must not request a move'))
            def report(identifier, tokens):
                target = next(w for w in live if w['workspace_id'] == identifier)
                target['tokens'] = {k: v for k, v in tokens.items() if v is not None}
            context.report = Mock(side_effect=report)
            with patch('headings.reconcile', return_value={}):
                sync(context, state)
                self.assertEqual([w['workspace_id'] for w in live], ['b', 'a'])
                context.report.reset_mock()
                sync(context, state)
                context.report.assert_not_called()
            context.rpc.assert_not_called()

    def test_reserved_prefix_is_not_adopted_as_a_heading(self):
        with tempfile.TemporaryDirectory() as root:
            context = SimpleNamespace(directory=Path(root), workspaces=lambda: [space('foreign', '▪ ordinary')])
            with patch('headings.reconcile') as reconcile:
                with self.assertRaisesRegex(ValueError, 'reserved'):
                    sync(context, State())
                reconcile.assert_not_called()
            self.assertFalse((Path(root) / 'headings.json').exists())

    def test_sidebar_hides_duplicate_names_and_uses_own_tokens(self):
        rows = tomllib.loads(Path(__file__).with_name('sidebar.toml').read_text())['ui']['sidebar']['spaces']['rows']
        self.assertTrue(all(len(row) <= 16 for row in rows))
        self.assertEqual(rows[1], ['$herdr_groups_spacer'])
        names = [cell for cell in rows[0] if isinstance(cell, dict) and cell['token'] not in ['$' + t for t in TOKENS]]
        self.assertEqual(len(names), 1)
        self.assertEqual(names[0]['token'], 'workspace')
        self.assertEqual(names[0]['rules'][0], {'starts_with': '▪ ', 'hide': True})
        self.assertFalse(any('herdr_groups_name' in str(cell) for row in rows for cell in row))


class RuntimeTest(unittest.TestCase):
    def test_native_actions_cover_user_operations_without_source_paths(self):
        manifest = tomllib.loads(Path(__file__).with_name('herdr-plugin.toml').read_text())
        actions = {a['id']: a['command'] for a in manifest['actions']}
        for operation in ('start', 'stop', 'unsetup'):
            self.assertEqual(actions[operation], ['python3', 'plugin.py', operation])
        for operation in ('add', 'rename', 'color', 'delete', 'status', 'setup'):
            self.assertEqual(actions[operation], ['python3', 'plugin.py', 'prompt', operation])

    def test_operation_dialog_passes_names_as_argv_and_defaults_color(self):
        with tempfile.TemporaryDirectory() as root:
            context = SimpleNamespace(state=Path(root) / 'groups.json')
            name = 'project with spaces; literal text'
            with patch('builtins.input', side_effect=[name, '']), patch('plugin.subprocess.run') as run:
                run.return_value = SimpleNamespace(returncode=0, stdout='', stderr='')
                plugin.dialog(context, 'add')
            self.assertEqual(run.call_args.args[0], [sys.executable, str(Path(plugin.__file__).resolve()), 'add', name, 'grey'])
            self.assertFalse(run.call_args.kwargs.get('shell', False))

    def test_status_dialog_is_read_only_and_handles_stale_heading_records(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            context = SimpleNamespace(directory=directory, state=directory / 'groups.json',
                                      enabled=directory / 'enabled', workspaces=lambda: [space('stale')])
            state = State()
            state.add('oss', 'blue')
            state.save(context.state)
            context.enabled.touch()
            headings.save(context, {'stale': {'group': 'removed', 'marker': 'm', 'pane_id': 'p',
                                             'cwd': root, 'label': '▪ removed'}})
            before = context.state.read_text()
            output = StringIO()
            with redirect_stdout(output), patch('builtins.input', return_value=''), patch('plugin.subprocess.run') as run:
                plugin.dialog(context, 'status')
                run.assert_not_called()
            self.assertIn('Updates: running', output.getvalue())
            self.assertIn('▪ oss (blue)', output.getvalue())
            self.assertNotIn('removed', output.getvalue())
            self.assertEqual(context.state.read_text(), before)

    def test_invalid_existing_names_do_not_emit_terminal_controls(self):
        with tempfile.TemporaryDirectory() as root:
            env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
            env.update(HERDR_ENV='1', HERDR_SOCKET_PATH=str(Path(root) / 'absent.sock'),
                       HERDR_PLUGIN_STATE_DIR=str(Path(root) / 'state'))
            for name in ('unknown\x1b]52;c;payload\x07', 'unknown\u009b31m'):
                for args in (('delete', name), ('rename', name, 'new'), ('color', name, 'blue')):
                    result = subprocess.run([sys.executable, str(Path(plugin.__file__).resolve()), *args],
                                            env=env, text=True, capture_output=True, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('\x1b', result.stderr)
                    self.assertNotIn('\u009b', result.stderr)
                    self.assertIn('without outer whitespace or controls', result.stderr)

    def test_list_and_status_do_not_copy_other_provider_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            workspace = dict(space('a', 'ordinary'), number=1,
                             tokens={'external_label': 'private-name', 'another_provider': 'private-value'})
            context = SimpleNamespace(directory=directory, state=directory / 'groups.json',
                                      enabled=directory / 'enabled', workspaces=lambda: [workspace])
            for command in ('list', 'status'):
                output = StringIO()
                with redirect_stdout(output):
                    execute(context, SimpleNamespace(command=command))
                listing = json.loads(output.getvalue())['workspaces']
                self.assertEqual(listing, [{'workspace_id': 'a', 'label': 'ordinary', 'number': 1,
                                            'section': None, 'heading': False}])
                self.assertNotIn('private-', output.getvalue())

    def test_headings_are_owned_idempotent_and_remove_only_the_requested_group(self):
        with tempfile.TemporaryDirectory() as root:
            state = State()
            state.add('oss', 'blue')
            parent, child = space('p', repo='R'), space('c', repo='R', child=True)
            before = copy.deepcopy(state.data)
            live = [space('a'), child, parent]
            context = SimpleNamespace(directory=Path(root), workspaces=lambda: copy.deepcopy(live))
            calls = []
            panes = {}
            def rpc(method, params):
                calls.append((method, params))
                if method == 'workspace.create':
                    identifier = 'h' + str(len(panes))
                    w = dict(space(identifier, params['label']), pane_count=1, tab_count=1)
                    pane = {'pane_id': identifier + ':p1', 'workspace_id': identifier, 'cwd': params['cwd']}
                    panes[pane['pane_id']] = pane
                    live.append(w)
                    return {'workspace': copy.deepcopy(w), 'root_pane': pane}
                if method == 'pane.get':
                    return {'pane': panes[params['pane_id']]}
                if method == 'pane.process_info':
                    return {'process_info': {'shell_pid': 12345, 'foreground_processes': [{'pid': 12345}]}}
                if method == 'workspace.close':
                    live[:] = [w for w in live if w['workspace_id'] != params['workspace_id']]
                    return {}
                raise AssertionError(method)
            context.rpc = rpc
            def report(identifier, tokens):
                next(w for w in live if w['workspace_id'] == identifier).setdefault('tokens', {}).update(tokens)
                calls.append(('metadata', tokens))
            context.report = report
            records = headings.reconcile(context, state)
            self.assertEqual(len(records), 2)
            calls.clear()
            self.assertEqual(headings.reconcile(context, state), records)
            self.assertEqual(calls, [])
            ordered, tokens = plan(state, live, records)
            self.assertEqual([w['workspace_id'] for w in ordered], ['a', 'c', 'p', 'h0', 'h1'])
            self.assertEqual(tokens['h1']['herdr_groups_blue'], '▪ oss')
            self.assertIsNone(tokens['a']['herdr_groups_spacer'])
            self.assertIsNone(tokens['c']['herdr_groups_spacer'])
            self.assertEqual(tokens['h0']['herdr_groups_spacer'], '\u200b')
            empty_default = plan(state, [w for w in live if w['workspace_id'] != 'a'], records)[1]
            self.assertEqual(empty_default['h0']['herdr_groups_spacer'], '\u200b')
            for identifier in ('a', 'p', 'c'):
                self.assertTrue(all(tokens[identifier][k] is None for k in ICON_TOKENS))
            self.assertEqual(state.data, before)
            # A cold restore loses metadata; recover only matching private cwd,
            # saved pane ID and label. A foreign marker must never be adopted.
            live[-1]['tokens'].pop(HEADER_TOKEN)
            self.assertTrue(headings.owned(context, live[-1], records['h1']))
            report('h1', {HEADER_TOKEN: 'foreign'})
            with self.assertRaises(ValueError):
                headings.reconcile(context, state)
            with patch('headings.subprocess.run', return_value=SimpleNamespace(stdout='')):
                self.assertEqual(headings.remove(context, group='oss'), ['h1'])
                self.assertEqual(len(live), 5)
                self.assertEqual(headings.remove(context), ['h1'])
            self.assertEqual([w['workspace_id'] for w in live], ['a', 'c', 'p', 'h1'])
            self.assertEqual(list(headings.load(context)), ['h1'])
            # Even a valid marker never authorizes closing a heading with jobs.
            report('h1', {HEADER_TOKEN: records['h1']['marker']})
            with patch('headings.subprocess.run', return_value=SimpleNamespace(stdout='12346 12345\n')):
                self.assertEqual(headings.remove(context), ['h1'])
            calls.clear()
            self.assertEqual(list(headings.reconcile(context, state, create=False)), ['h1'])
            self.assertFalse(any(method == 'workspace.create' for method, _ in calls))


    def test_stop_does_not_parse_or_overwrite_corrupt_definitions(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            context = SimpleNamespace(directory=directory, state=directory / 'groups.json',
                                      herdr_config=directory / 'config.toml',
                                      enabled=directory / 'enabled', stopped=directory / 'stopped',
                                      workspaces=lambda: [])
            context.state.write_text('{broken')
            context.enabled.touch()
            context.herdr_config.write_text('user_owned = true\n')
            context.workspaces = lambda: [dict(space('h'), tokens={'herdr_groups_grey': '▪ default'})]
            context.report = Mock()
            execute(context, SimpleNamespace(command='stop'))
            context.report.assert_not_called()
            self.assertEqual(context.herdr_config.read_text(), 'user_owned = true\n')
            self.assertEqual(context.state.read_text(), '{broken')
            self.assertFalse(context.enabled.exists())
            self.assertTrue(context.stopped.exists())



if __name__ == '__main__':
    unittest.main()
