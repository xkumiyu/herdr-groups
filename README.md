# Herdr Groups

Organize Herdr Spaces with colored sidebar headings. Each group extends from its heading to the next; arrange headings and Spaces with Herdr's reorder controls. Group definitions are saved per Herdr session.

## Requirements

Herdr 0.9.5+, Python 3.11+, Linux or macOS, and Git for installation.

## Installation

```sh
herdr plugin install xkumiyu/herdr-groups
```

## Sidebar configuration

Merge [sidebar.toml](sidebar.toml) into your Herdr configuration, usually `~/.config/herdr/config.toml`. If `[ui.sidebar.spaces]` exists, edit its `rows` without duplicating the table or removing other settings.

Run inside Herdr:

```sh
herdr config check
herdr server reload-config
herdr plugin action invoke herdr-groups.start
```

## Operation

Run inside Herdr. Add, rename, color, and delete open input popups.

| Action | Command |
| --- | --- |
| Add group | `herdr plugin action invoke herdr-groups.add` |
| Rename group | `herdr plugin action invoke herdr-groups.rename` |
| Change color | `herdr plugin action invoke herdr-groups.color` |
| Delete group | `herdr plugin action invoke herdr-groups.delete` |
| Show groups | `herdr plugin action invoke herdr-groups.status` |

Colors: `grey` (default), `blue`, `orange`, `green`, `red`, `purple`, `yellow`, `cyan`, `white`.

Use the actions to rename or delete headings; deleting a group preserves its other Spaces. Keep headings free of terminal work, and reserve the `▪ ` name prefix for headings.

## Stopping and resuming

Stop updates, keeping headings and the current display, then resume with `start`:

```sh
herdr plugin action invoke herdr-groups.stop
herdr plugin action invoke herdr-groups.start
```

## Uninstalling

Clean up in **each Herdr session** where you used the plugin:

```sh
herdr plugin action invoke herdr-groups.stop
herdr plugin action invoke herdr-groups.unsetup
```

Modified or busy headings are retained. Inspect them and finish or move your work before retrying cleanup. After all sessions are cleaned up:

```sh
herdr plugin uninstall herdr-groups
```

Remove the plugin's sidebar configuration manually. Saved group definitions remain.
