"""Owned heading Spaces. Called under state.lock; never moves normal Spaces."""

import subprocess
import uuid

from groups import HEADER_TOKEN, atomic_json, heading_label
import json


def load(context):
    try:
        records = json.loads((context.directory / 'headings.json').read_text())
    except FileNotFoundError:
        return {}
    if (not isinstance(records, dict) or any(
            not isinstance(identifier, str) or not isinstance(record, dict)
            or any(not isinstance(record.get(k), str) or not record[k]
                   for k in ('group', 'marker', 'pane_id', 'cwd', 'label'))
            for identifier, record in records.items())):
        raise ValueError('Invalid heading ownership records; no Spaces were closed')
    return records


def save(context, records):
    atomic_json(context.directory / 'headings.json', records)


def owned(context, workspace, record):
    if workspace.get('worktree'):
        return False
    marker = workspace.get('tokens', {}).get(HEADER_TOKEN)
    if marker is not None:
        return marker == record['marker']
    # Metadata is ephemeral after a cold restore. Require the recorded pane,
    # private cwd and label as well as the Space ID before restoring ownership.
    if workspace['label'] != record['label'] or workspace['pane_count'] != 1 or workspace['tab_count'] != 1:
        return False
    pane = context.rpc('pane.get', {'pane_id': record['pane_id']})['pane']
    return pane['workspace_id'] == workspace['workspace_id'] and pane.get('cwd') == record['cwd']


def reconcile(context, state, create=True):
    records = load(context)
    live = {w['workspace_id']: w for w in context.workspaces()}
    records = {i: r for i, r in records.items() if i in live}
    for identifier, record in records.items():
        if not owned(context, live[identifier], record):
            raise ValueError(f'Heading ownership changed for {identifier}; close or resolve it manually')
    for group in state.names:
        match = next((i for i, r in records.items() if r['group'] == group), None)
        label = heading_label(group)
        if match is None:
            if not create:
                continue
            marker = uuid.uuid4().hex
            cwd = context.directory / 'headings' / marker
            cwd.mkdir(parents=True)
            created = context.rpc('workspace.create', {'cwd': str(cwd), 'label': label, 'focus': False})
            match = created['workspace']['workspace_id']
            records[match] = {'group': group, 'marker': marker, 'pane_id': created['root_pane']['pane_id'],
                              'cwd': str(cwd), 'label': label}
            # Save the creation result before metadata; a failed metadata request
            # must not cause the next sync to create a second heading.
            save(context, records)
            live[match] = created['workspace']
        record = records[match]
        if live[match]['label'] != label:
            context.rpc('workspace.rename', {'workspace_id': match, 'label': label})
        record['label'] = label
        if live[match].get('tokens', {}).get(HEADER_TOKEN) != record['marker']:
            context.report(match, {HEADER_TOKEN: record['marker']})
    if any(r['group'] not in state.names for r in records.values()):
        raise ValueError('Unknown heading group; use the rename command to rename a group')
    if records != load(context):
        save(context, records)
    return records


def remove(context, group=None):
    records = load(context)
    live = {w['workspace_id']: w for w in context.workspaces()}
    retained = []
    for identifier, record in list(records.items()):
        if group is not None and record['group'] != group:
            continue
        workspace = live.get(identifier)
        if workspace is None:
            del records[identifier]
            continue
        if not owned(context, workspace, record):
            retained.append(identifier)
            continue
        # A heading used as a normal terminal must not lose user work. Refuse
        # removal if topology/cwd changed, an agent is present, or shell has jobs.
        pane = context.rpc('pane.get', {'pane_id': record['pane_id']})['pane']
        process = context.rpc('pane.process_info', {'pane_id': record['pane_id']})['process_info']
        shell = process.get('shell_pid')
        foreground = process.get('foreground_processes', [])
        processes = subprocess.run(['ps', '-axo', 'pid=,ppid='], check=True, capture_output=True, text=True)
        has_jobs = any(len(parts := line.split()) == 2 and parts[1] == str(shell)
                       for line in processes.stdout.splitlines())
        if (workspace['pane_count'] != 1 or workspace['tab_count'] != 1 or pane.get('agent')
                or pane['workspace_id'] != identifier or pane.get('cwd') != record['cwd']
                or pane.get('foreground_cwd') not in (None, record['cwd'])
                or not shell or not foreground or any(p['pid'] != shell for p in foreground) or has_jobs):
            retained.append(identifier)
            continue
        context.rpc('workspace.close', {'workspace_id': identifier})
        del records[identifier]
        save(context, records)
    save(context, records)
    if retained:
        print('Retained modified/busy heading Spaces: ' + ', '.join(retained))
    return retained


def rename(context, old, new):
    records = load(context)
    for record in records.values():
        if record['group'] == old:
            record['group'] = new
    if records:
        save(context, records)
