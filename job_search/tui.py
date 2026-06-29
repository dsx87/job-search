"""Curses TUI for browsing scraped jobs (a local developer convenience tool).

Run with: python -m job_search.tui
"""
import curses
import threading
import webbrowser

from .sources.fetch import fetch_jobs
from .state.job_store import JobStore


class JobTUI:
    def __init__(self):
        self.store = JobStore()
        self.jobs = []
        self.cursor = 0
        self.offset = 0
        self.loading = False
        self.loading_message = ""
        self.refresh_done = False
        self.refresh_error = None
        self.width = 0
        self.height = 0
        self.table_height = 0
        self.needs_redraw = True

    def run(self):
        curses.wrapper(self._main_loop)

    def _main_loop(self, stdscr):
        curses.curs_set(0)
        stdscr.timeout(100)
        self.stdscr = stdscr

        if curses.has_colors():
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_WHITE, -1)      # normal
            curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)  # selected
            curses.init_pair(3, curses.COLOR_CYAN, -1)       # unseen highlight
            curses.init_pair(4, curses.COLOR_YELLOW, -1)     # header / status
            curses.init_pair(5, curses.COLOR_GREEN, -1)      # success
            curses.init_pair(6, curses.COLOR_RED, -1)        # error
            curses.init_pair(7, curses.COLOR_BLUE, -1)       # seen dimmed
        else:
            for i in range(1, 8):
                curses.init_pair(i, 0, 0)

        self._reload_jobs()

        while True:
            self._update_size()

            if self.refresh_done:
                self.refresh_done = False
                self.loading = False
                if self.refresh_error:
                    self.loading_message = "Error: {}".format(self.refresh_error)
                else:
                    self.loading_message = "Refreshed!"
                self._reload_jobs()
                self.needs_redraw = True

            if self.needs_redraw:
                self.draw()
                self.needs_redraw = False

            key = stdscr.getch()
            if key == -1:
                continue

            if self._handle_input(key):
                break

    def _update_size(self):
        self.height, self.width = self.stdscr.getmaxyx()
        self.table_height = max(0, self.height - 3)  # header + status rows

    def _reload_jobs(self):
        self.jobs = self.store.get_jobs()
        if self.cursor >= len(self.jobs):
            self.cursor = max(0, len(self.jobs) - 1)
        if self.offset > max(0, len(self.jobs) - self.table_height):
            self.offset = max(0, len(self.jobs) - self.table_height)

    def _handle_input(self, key):
        if key in (ord("q"), ord("Q")):
            return True

        if key in (curses.KEY_UP, ord("k")):
            if self.cursor > 0:
                self.cursor -= 1
                if self.cursor < self.offset:
                    self.offset = self.cursor
                self.needs_redraw = True
            return False

        if key in (curses.KEY_DOWN, ord("j")):
            if self.cursor < len(self.jobs) - 1:
                self.cursor += 1
                if self.cursor >= self.offset + self.table_height:
                    self.offset = self.cursor - self.table_height + 1
                self.needs_redraw = True
            return False

        if key == ord(" "):
            self._toggle_seen()
            return False

        if key in (ord("o"), ord("O")):
            self._open_url()
            return False

        if key in (ord("s"), ord("S")):
            self.store.toggle_show_seen()
            self._reload_jobs()
            self.needs_redraw = True
            return False

        if key in (ord("r"), ord("R")):
            self._start_refresh()
            return False

        return False

    def _toggle_seen(self):
        if 0 <= self.cursor < len(self.jobs):
            job = self.jobs[self.cursor]
            self.store.toggle_seen(job["url"])
            self._reload_jobs()
            self.needs_redraw = True

    def _open_url(self):
        if 0 <= self.cursor < len(self.jobs):
            url = self.jobs[self.cursor].get("url")
            if url:
                try:
                    webbrowser.open(url, new=2)
                except Exception:
                    pass

    def _start_refresh(self):
        if self.loading:
            return
        self.loading = True
        self.loading_message = "Refreshing..."
        self.needs_redraw = True

        def worker():
            try:
                jobs = fetch_jobs(verbose=False)
                self.store.merge(jobs)
                self.refresh_error = None
            except Exception as exc:
                self.refresh_error = str(exc)
            self.refresh_done = True

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def draw(self):
        self.stdscr.clear()
        self._draw_header()
        self._draw_table()
        self._draw_status()
        self.stdscr.refresh()

    def _draw_header(self):
        if self.height < 1:
            return
        cols = self._column_specs()
        headers = ["Title", "Company", "Location", "Source", "Date", "Region"]
        line = ""
        for i, (header, spec) in enumerate(zip(headers, cols)):
            width = spec["width"]
            text = header[:width].ljust(width)
            line += text
            if i < len(cols) - 1:
                line += " "
        line = line[: self.width - 1]
        try:
            self.stdscr.addstr(0, 0, line, curses.color_pair(4) | curses.A_BOLD)
        except curses.error:
            pass

    def _draw_table(self):
        if self.height < 2:
            return
        cols = self._column_specs()
        for row_idx in range(self.table_height):
            job_idx = self.offset + row_idx
            y = 1 + row_idx
            if y >= self.height - 1:
                break
            if job_idx >= len(self.jobs):
                break

            job = self.jobs[job_idx]
            is_selected = job_idx == self.cursor
            is_seen = job.get("seen", False)

            parts = [
                job.get("title", ""),
                job.get("company", ""),
                job.get("location", ""),
                job.get("source", ""),
                job.get("date_posted", "") or "n/a",
                job.get("region", ""),
            ]

            line = ""
            for i, (part, spec) in enumerate(zip(parts, cols)):
                width = spec["width"]
                text = str(part)[:width].ljust(width)
                line += text
                if i < len(cols) - 1:
                    line += " "

            line = line[: self.width - 1]

            if is_selected:
                attr = curses.color_pair(2)
            elif is_seen:
                attr = curses.color_pair(7)
            else:
                attr = curses.color_pair(3)

            try:
                self.stdscr.addstr(y, 0, line, attr)
            except curses.error:
                pass

    def _draw_status(self):
        if self.height < 1:
            return
        y = self.height - 1
        total = len(self.store.jobs)
        unseen = sum(1 for j in self.store.jobs.values() if not j.get("seen", False))
        shown = len(self.jobs)
        mode = "showing all" if self.store.show_seen else "hiding seen"

        left = " {} unseen / {} total | {} shown | {} ".format(unseen, total, shown, mode)
        right = " r:refresh | space:toggle | o:open | s:show/hide | q:quit "

        padding = max(0, self.width - len(left) - len(right))
        status = left + " " * padding + right
        status = status[: self.width - 1]

        if self.loading:
            status = " {} ".format(self.loading_message) + status[len(self.loading_message) + 3 :]

        try:
            self.stdscr.addstr(y, 0, status, curses.color_pair(4))
        except curses.error:
            pass

    def _column_specs(self):
        # Distribute width among columns
        w = max(40, self.width)
        return [
            {"width": max(12, int(w * 0.28))},
            {"width": max(10, int(w * 0.18))},
            {"width": max(10, int(w * 0.18))},
            {"width": max(8, int(w * 0.12))},
            {"width": max(8, int(w * 0.12))},
            {"width": max(6, int(w * 0.08))},
        ]


def main():
    app = JobTUI()
    app.run()


if __name__ == "__main__":
    main()
