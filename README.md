# herdr-groups

Herdr の Spaces を、色付きの見出しと並び順で整理するプラグインです。

```text
▪ default
web-app
notes

▪ oss
library-a
library-b
```

Space ごとの所属設定は不要です。見出しから次の見出しまでがグループの範囲になります。見出しや Space は Herdr 標準の並べ替え操作で移動してください。並び順を自動で変更することはありません。現在は Spaces のみ対応しています。

## 必要環境

Herdr 0.9.5 以上、Python 3.11 以上、Linux または macOS。インストールには Git を使用します。

## インストール

```sh
herdr plugin install xkumiyu/herdr-groups
```

## 表示設定

Herdr の設定ファイル（通常は `~/.config/herdr/config.toml`）に、[sidebar.toml](sidebar.toml) の設定例を反映します。

既に `[ui.sidebar.spaces]` がある場合は、テーブルを重複して追加せず、その `rows` を設定例に合わせて編集してください。他の設定は残します。

設定例は、Herdr 内で次のコマンドを実行して確認することもできます。設定ファイルは自動で変更されません。

```sh
herdr plugin action invoke herdr-groups.setup
```

編集後、設定を検証・再読み込みして開始します。

```sh
herdr config check
herdr server reload-config
herdr plugin action invoke herdr-groups.start
```

## 使い方

Herdr 内で次のコマンドを実行します。追加・改名・色変更・削除では、名前や色の入力画面が開きます。

| 操作 | コマンド |
| --- | --- |
| グループを追加 | `herdr plugin action invoke herdr-groups.add` |
| 名前を変更 | `herdr plugin action invoke herdr-groups.rename` |
| 色を変更 | `herdr plugin action invoke herdr-groups.color` |
| グループを削除 | `herdr plugin action invoke herdr-groups.delete` |
| グループ一覧 | `herdr plugin action invoke herdr-groups.status` |

色は `grey`、`blue`、`orange`、`green`、`red`、`purple`、`yellow`、`cyan`、`white` から選べます。追加時に色を省略すると `grey` になります。

追加した見出しは通常、Space 一覧の末尾に作成されます。好きな位置へ移動し、その下に Space を並べてください。最初の見出しより前はグループなしとして扱います。グループ設定は Herdr セッションごとに保存されます。

### 見出しについて

- 見出しも通常の Space なので、番号と選択対象を持ちます。グループ単位の折りたたみはありません。
- 見出しを直接閉じると再作成されます。削除には delete アクションを使ってください。グループ内の Space は削除されません。
- 見出しは作業用端末として使わないでください。使用中・変更済みの見出しは削除を保留します。
- `▪ ` は見出し専用です。通常の Space 名の先頭には使わないでください。
- 長いグループ名は省略表示されます。名前は80文字まで保存でき、見出しの名前部分は最大78文字まで表示します。
- worktree の親子表示は Herdr 標準に従います。グループ末尾の worktree 子を折りたたむと、境界の空行も隠れることがあります。

## 停止と再開

```sh
herdr plugin action invoke herdr-groups.stop
herdr plugin action invoke herdr-groups.start
```

停止しても見出しと現在の表示は残ります。

## アンインストール

使用した **各 Herdr セッションで**、次の両方を実行します。

```sh
herdr plugin action invoke herdr-groups.stop
herdr plugin action invoke herdr-groups.unsetup
```

全セッションの片付けが済んだら、プラグインを削除します。

```sh
herdr plugin uninstall herdr-groups
```

不要になったサイドバー設定は、Herdr の設定ファイルから手動で戻してください。削除が保留された見出しは、内容を確認してから片付けてください。保存済みのグループ設定は残ります。
