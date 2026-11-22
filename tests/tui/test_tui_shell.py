"""T9.1: TUI shell (Textual pilot, headless, fake model, no network)."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("textual")

from fake_model import FakeModel  # noqa: E402
from textual.widgets import TabbedContent, TabPane  # noqa: E402

from champion.core.config import load_config  # noqa: E402
from champion.core.runtime import ChampionRuntime  # noqa: E402
from champion.tui import resources  # noqa: E402
from champion.tui.__main__ import build_parser  # noqa: E402
from champion.tui.app import MIN_HEIGHT, MIN_WIDTH, TABS, ChampionApp  # noqa: E402
from champion.tui.logo import LOGO_CELLS, LOGO_GIF, LOGO_PNG, LogoState, image_lines, text_lines  # noqa: E402
from champion.tui.state import build_state, files_affected, path_counts  # noqa: E402
from champion.tui.widgets import AgentUsage, ContextSummary, Panel  # noqa: E402

FIXED = datetime(2026, 11, 21, 22, 40, 12)


def make_runtime(tmp_path: Path) -> ChampionRuntime:
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text("llm:\n  model_id: test-model\n")
    return ChampionRuntime(load_config(folder / "config.yaml", env={}, cwd=tmp_path), model=FakeModel([]))


def run_app(app, size, check):
    async def go():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await check(app, pilot)
    asyncio.run(go())


def text_of(app, selector) -> str:
    return app.query_one(selector, Panel).plain


def test_tui_state_counts_paths_and_unique_files(tmp_path):
    runtime = make_runtime(tmp_path)
    context = runtime.context
    read0, edit0 = path_counts(context.level)
    models = tmp_path / "models"
    models.mkdir()
    context.set_access(context.level.with_folder(models.resolve(), "edit"), "test")
    assert path_counts(context.level) == (read0 + 1, edit0 + 1)

    audit = runtime.audit
    a, b, c = (str(models / name) for name in ("a.k", "b.k", "c.k"))
    audit.record_change(tool="file_edit", action="modified", path=a)
    audit.record_change(tool="file_edit", action="modified", path=a)
    assert files_affected(audit) == 1  # two events on one file
    audit.record_change(tool="files_organize", action="renamed", path=a, new_path=b)
    assert files_affected(audit) == 1  # the rename follows the file
    audit.record_change(tool="file_create", action="created", path=c)
    state = build_state(runtime)
    assert (state.files_affected, state.read_path_count, state.edit_path_count) == (2, read0 + 1, edit0 + 1)
    assert state.llm_model == "test-model" and state.total_tokens == 0
    assert state.agents[0].name == "Champion"


def test_tui_shell_layout_tabs_footer_and_right_panel(tmp_path):
    app = ChampionApp(make_runtime(tmp_path), clock=lambda: FIXED, logo_path=None)

    async def check(app, pilot):
        assert [pane.id for pane in app.query(TabPane)] == [tab for tab, _ in TABS]
        tabs = app.query_one(TabbedContent)
        assert tabs.active == "conversation"
        tabs.active = "hpc"
        await pilot.pause()
        assert tabs.active == "hpc" and "track T2" in text_of(app, "#hpc-note")
        tabs.active = "conversation"
        await pilot.pause()
        assert app.query_one("#chat-input") is not None  # conversation tab kept after switching
        assert text_of(app, "#llm") == "LLM: test-model"
        assert text_of(app, "#tokens") == "Total Tokens: 0"
        assert text_of(app, "#clock") == "Current Time: 22:40:12"
        assert app.query_one("#right").display
        assert "Champion" in text_of(app, "#agents")
        assert "none started by agents" in text_of(app, "#software")
        assert "Files affected" in text_of(app, "#context")
        assert "RAM" in text_of(app, "#resources") and "Champion working folder" in text_of(app, "#resources")
        assert "not selected" in text_of(app, "#working-row") and "not selected" in text_of(app, "#base-row")
        assert not app.query_one("#too-small").display

    run_app(app, (140, 40), check)


def test_tui_too_small_terminal_shows_message(tmp_path):
    app = ChampionApp(make_runtime(tmp_path), clock=lambda: FIXED, logo_path=None)

    async def check(app, pilot):
        assert not app.query_one("#main").display
        assert app.query_one("#too-small").display
        assert f"60x20" in text_of(app, "#too-small") and f"{MIN_WIDTH}x{MIN_HEIGHT}" in text_of(app, "#too-small")

    run_app(app, (60, 20), check)


def test_tui_narrow_terminal_hides_right_panel_f6_toggles(tmp_path):
    app = ChampionApp(make_runtime(tmp_path), clock=lambda: FIXED, logo_path=None)

    async def check(app, pilot):
        right = app.query_one("#right")
        assert not right.display
        await pilot.press("f6")
        assert right.display
        await pilot.press("f6")
        assert not right.display

    run_app(app, (100, 40), check)


def test_tui_clock_tick_redraws_only_the_clock(tmp_path):
    times = iter([FIXED, FIXED.replace(second=13), FIXED.replace(second=14)])
    last = [FIXED]

    def clock():
        last[0] = next(times, last[0])
        return last[0]

    app = ChampionApp(make_runtime(tmp_path), clock=clock, logo_path=None)

    async def check(app, pilot):
        agents, context = app.query_one(AgentUsage), app.query_one(ContextSummary)
        before = (agents.updates, context.updates, app.query_one("#tokens", Panel).updates)
        old = text_of(app, "#clock")
        app.query_one("#footer").tick()
        assert text_of(app, "#clock") != old
        app.refresh_state()  # nothing changed in the core
        assert (agents.updates, context.updates, app.query_one("#tokens", Panel).updates) == before

    run_app(app, (140, 40), check)


def test_tui_context_panel_follows_core_changes(tmp_path):
    runtime = make_runtime(tmp_path)
    app = ChampionApp(runtime, clock=lambda: FIXED, logo_path=None)

    async def check(app, pilot):
        assert "Files affected     0" in text_of(app, "#context")
        runtime.audit.record_change(tool="file_edit", action="modified", path=str(tmp_path / "x.k"))
        app.refresh_state()
        assert "Files affected     1" in text_of(app, "#context")

    run_app(app, (140, 40), check)


def test_tui_logo_text_fallback_and_error_colour(tmp_path):
    assert image_lines(tmp_path / "missing.png") is None
    assert image_lines(None) is None
    lines = text_lines()
    assert len(lines) == 4 and all(len(line.plain) == 8 for line in lines)
    logo = LogoState(None)
    assert not logo.uses_image
    logo.state = "error"
    assert all("#ff4040" in str(span.style) for line in logo.lines() for span in line.spans)


def test_tui_logo_png_drawn_with_half_blocks(tmp_path):
    image_module = pytest.importorskip("PIL.Image")
    image = image_module.new("RGB", (60, 60), (255, 255, 255))
    for x in range(10, 50):
        for y in range(15, 45):
            image.putpixel((x, y), (11, 61, 255))
    path = tmp_path / "m_logo.png"
    image.save(path)
    lines = image_lines(path, 10, 4)
    assert lines is not None and len(lines) == 4
    assert all(len(line.plain) == 10 for line in lines)
    assert "▀" in "".join(line.plain for line in lines)  # white margin cropped, blue drawn
    assert LogoState(path).uses_image


def test_tui_shipped_logo_files_are_used():
    pytest.importorskip("PIL.Image")
    assert LOGO_PNG.is_file() and LOGO_GIF.is_file()
    logo = LogoState(LOGO_PNG)
    assert logo.uses_image
    lines = logo.lines()
    assert len(lines) == LOGO_CELLS[1] and all(len(line.plain) == LOGO_CELLS[0] for line in lines)
    assert " " in lines[-1].plain  # the gap between the M's legs is kept


def test_tui_resources_parse_and_thresholds(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       33554432 kB\nMemFree:  1 kB\nMemAvailable:   13421772 kB\n")
    ram = resources.memory_usage(meminfo)
    assert ram.total == 33554432 * 1024 and round(ram.percent) == 60
    assert resources.memory_usage(tmp_path / "none") is None
    assert resources.disk_usage(tmp_path) is not None
    assert resources.ram_level(resources.Usage(50, 100)) == "ok"
    assert resources.ram_level(resources.Usage(90, 100)) == "warning"
    assert resources.ram_level(resources.Usage(96, 100)) == "critical"
    assert resources.disk_level(resources.Usage(92, 100)) == "warning"  # 8% free
    assert resources.disk_level(resources.Usage(96, 100)) == "critical"  # 4% free
    assert resources.human(32 * 1024 ** 3) == "32.0 GB"


def test_tui_main_parser_same_start_options_as_chat():
    args = build_parser().parse_args(["--level", "1.2", "--read", "/a", "--edit", "/b", "--outside", "none"])
    assert (args.level, args.read, args.edit, args.outside) == ("1.2", ["/a"], ["/b"], "none")
