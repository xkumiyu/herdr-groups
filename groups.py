"""Group definitions and display derived only from the current Space order."""

import copy
import json
import os
import tempfile
import unicodedata
from pathlib import Path

COLORS = {'grey': '#A0A0A0', 'blue': '#61AFEF', 'orange': '#E5A067',
          'green': '#98C379', 'red': '#E06C75', 'purple': '#C678DD',
          'yellow': '#E5C07B', 'cyan': '#56B6C2', 'white': '#FFFFFF'}
ICON_TOKENS = tuple('herdr_groups_' + color for color in COLORS)
HEADER_TOKEN = 'herdr_groups_header'
TOKENS = ICON_TOKENS + ('herdr_groups_spacer',)
PREFIX = '▪ '


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def name_checked(name):
    if (not isinstance(name, str) or not name.strip() or name != name.strip()
            or len(name) > 80 or any(unicodedata.category(c) == 'Cc' for c in name)):
        raise ValueError('Group names must be 1–80 characters, without outer whitespace or controls')


def heading_label(name):
    return (PREFIX + name)[:80]


class State:
    def __init__(self, data=None):
        self.data = copy.deepcopy(data) if data is not None else {
            'version': 2, 'groups': [{'name': 'default', 'color': 'grey'}]}
        self.validate()

    @property
    def names(self):
        return [group['name'] for group in self.data['groups']]

    def validate(self):
        if (not isinstance(self.data, dict) or self.data.get('version') != 2
                or set(self.data) != {'version', 'groups'} or not isinstance(self.data['groups'], list)):
            raise ValueError('Invalid group definitions')
        for group in self.data['groups']:
            if not isinstance(group, dict) or set(group) != {'name', 'color'}:
                raise ValueError('Groups contain only a name and color')
            name_checked(group['name'])
            if group['color'] not in COLORS:
                raise ValueError('Unknown group color')
        if len(set(self.names)) != len(self.names):
            raise ValueError('Duplicate groups')

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text())
        except FileNotFoundError:
            return cls()
        if isinstance(data, dict) and data.get('version') == 1:
            data = {'version': 2, 'groups': [{'name': g['name'], 'color': g['color']} for g in data['groups']]}
        return cls(data)

    def save(self, path):
        self.validate()
        path = Path(path)
        if path.exists():
            original = path.read_text()
            if json.loads(original).get('version') == 1:
                backup = path.with_name('groups.v1.backup.json')
                if not backup.exists():
                    with backup.open('x') as stream:
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
        atomic_json(path, self.data)

    def require(self, name):
        name_checked(name)
        if name not in self.names:
            raise ValueError(f'Unknown group: {name}')

    def add(self, name, color='grey'):
        name_checked(name)
        if name in self.names or color not in COLORS:
            raise ValueError('Group already exists or color is unknown')
        self.data['groups'].append({'name': name, 'color': color})

    def remove(self, name):
        self.require(name)
        self.data['groups'] = [g for g in self.data['groups'] if g['name'] != name]

    def rename(self, old, new):
        self.require(old)
        name_checked(new)
        if new != old and new in self.names:
            raise ValueError('Group already exists')
        next(g for g in self.data['groups'] if g['name'] == old)['name'] = new

    def color(self, name, color):
        self.require(name)
        if color not in COLORS:
            raise ValueError('Unknown color: ' + color)
        next(g for g in self.data['groups'] if g['name'] == name)['color'] = color


def plan(state, workspaces, headings=None):
    """Return the exact input order; no membership or provenance inference."""
    headings = headings or {}
    by_name = {g['name']: g for g in state.data['groups']}
    tokens = {w['workspace_id']: dict.fromkeys(TOKENS) for w in workspaces}
    seen_heading = False
    for index, workspace in enumerate(workspaces):
        identifier = workspace['workspace_id']
        if identifier in headings:
            if seen_heading:
                # ponytail: folding the preceding worktree child hides its gap;
                # native client-aware spacing requires support in Herdr.
                tokens[workspaces[index - 1]['workspace_id']]['herdr_groups_spacer'] = '\u200b'
            seen_heading = True
            group = by_name[headings[identifier]['group']]
            tokens[identifier]['herdr_groups_' + group['color']] = heading_label(group['name'])
    return workspaces, tokens
