from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, ProgressBar, Static
from textual.worker import get_current_worker

from .indexer import ChildEntry, FileIndex, format_size

BAR_WIDTH = 20
_PARENT_NAME = "\x00__parent__"  # sentinel that can't appear in real filenames


class EntryItem(ListItem):
    """One row in the navigator list."""

    def __init__(self, name: str, markup: str, is_dir: bool, is_parent: bool = False) -> None:
        super().__init__()
        self.entry_name = name
        self.is_entry_dir = is_dir
        self.is_parent = is_parent
        self._markup = markup

    def compose(self) -> ComposeResult:
        yield Label(self._markup, markup=True)


class TreesizeApp(App[None]):
    TITLE = "treesize-navigator"

    CSS = """
    Screen {
        background: $surface;
        layout: vertical;
    }

    #loading-container {
        align: center middle;
        height: 1fr;
    }

    #loading-label {
        text-align: center;
        margin-bottom: 1;
        color: $text-muted;
    }

    #progress {
        width: 50%;
    }

    #path-bar {
        background: $primary;
        color: $text;
        text-style: bold;
        height: 1;
        padding: 0 1;
        dock: top;
    }

    #nav-list {
        height: 1fr;
        border: none;
        scrollbar-gutter: stable;
    }

    EntryItem {
        height: 1;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("enter,right", "open_selected", "Open", show=True),
        Binding("backspace,left", "go_up", "Up", show=True),
        Binding("q", "quit", "Quit", show=True, priority=True),
    ]

    current_path: reactive[str] = reactive("", init=False)

    def __init__(self, input_file: str) -> None:
        super().__init__()
        self.input_file = input_file
        self._index: FileIndex | None = None
        self._cursor_history: dict[str, int] = {}
        self._loader = None
        self._quitting = False

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="loading-container"):
            yield Label("Indexing — please wait…", id="loading-label")
            yield ProgressBar(id="progress", total=100, show_eta=False)
        yield Static("", id="path-bar")
        yield ListView(id="nav-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#path-bar").display = False
        self.query_one("#nav-list").display = False
        self._loader = self._load_file()

    def on_unmount(self) -> None:
        if self._loader is not None:
            self._loader.cancel()

    # ------------------------------------------------------------------ loading

    @work(thread=True, exit_on_error=False)
    def _load_file(self) -> None:
        worker = get_current_worker()

        def progress_cb(bytes_read: int, total: int) -> None:
            if worker.is_cancelled:
                return
            pct = round(bytes_read / total * 100) if total > 0 else 0
            try:
                self.call_from_thread(self._set_progress, pct)
            except Exception:
                pass

        index = FileIndex.from_file(
            self.input_file,
            progress_cb=progress_cb,
            cancelled=lambda: worker.is_cancelled,
        )
        if not worker.is_cancelled and index is not None:
            try:
                self.call_from_thread(self._on_index_ready, index)
            except Exception:
                pass

    def _set_progress(self, pct: int) -> None:
        self.query_one("#progress", ProgressBar).update(progress=pct, total=100)

    def action_quit(self) -> None:
        self._quitting = True
        self.exit()

    def _on_index_ready(self, index: FileIndex) -> None:
        if self._quitting or not self.is_running:
            return
        self._index = index
        self.query_one("#loading-container").display = False
        self.query_one("#path-bar").display = True
        self.query_one("#nav-list").display = True
        self.current_path = index.root

    # ------------------------------------------------------------------ navigation

    def watch_current_path(self, path: str) -> None:
        if not self._index or not path:
            return
        self._refresh_list(path)

    def _refresh_list(self, path: str) -> None:
        assert self._index is not None
        list_view = self.query_one("#nav-list", ListView)
        path_bar = self.query_one("#path-bar", Static)

        total = self._index.get_total(path)
        path_bar.update(f" {path}   {format_size(total)} total")

        entries = self._index.get_children(path)
        max_bytes = entries[0].total_bytes if entries else 1

        items: list[EntryItem] = []

        if path != self._index.root:
            items.append(EntryItem(_PARENT_NAME, "[dim]  ↑  ..[/dim]", is_dir=True, is_parent=True))

        for entry in entries:
            fraction = entry.total_bytes / max_bytes if max_bytes > 0 else 0
            filled = round(fraction * BAR_WIDTH)
            bar = "█" * filled + "░" * (BAR_WIDTH - filled)

            if entry.is_dir:
                type_char = "[bold yellow]▶[/bold yellow]"
                name_part = f"[bold]{entry.name}[/bold]"
            else:
                type_char = " "
                name_part = entry.name

            size_str = format_size(entry.total_bytes)
            markup = f"{type_char} {bar} [cyan]{size_str:>12}[/cyan]  {name_part}"
            items.append(EntryItem(entry.name, markup, entry.is_dir))

        list_view.clear()
        for item in items:
            list_view.append(item)

        target = min(self._cursor_history.get(path, 0), len(items) - 1) if items else 0

        def _set_cursor() -> None:
            list_view.index = target

        self.call_after_refresh(_set_cursor)

    def action_open_selected(self) -> None:
        if not self._index:
            return
        list_view = self.query_one("#nav-list", ListView)
        item = list_view.highlighted_child
        if not isinstance(item, EntryItem):
            return
        if item.is_parent:
            self._navigate_up()
            return
        if item.is_entry_dir:
            self._cursor_history[self.current_path] = list_view.index or 0
            child_path = self.current_path.rstrip("/") + "/" + item.entry_name
            self.current_path = child_path

    def action_go_up(self) -> None:
        self._navigate_up()

    def _navigate_up(self) -> None:
        if not self._index or not self.current_path:
            return
        if self.current_path == self._index.root:
            return
        list_view = self.query_one("#nav-list", ListView)
        self._cursor_history[self.current_path] = list_view.index or 0
        parts = self.current_path.rstrip("/").split("/")
        parent = "/".join(parts[:-1]) or "/"
        self.current_path = parent
