"""
Regression tests for the frontend layout structure.

These tests parse HTML, CSS, and JS as text to catch structural regressions
that caused the 5 layout bugs fixed in the settings-as-full-tab refactor:
  1. Active tab not visually apparent
  2. Job listing showing behind settings view
  3. Settings pane size jumping when switching tabs
  4. Log tab pane expanding based on content instead of scrolling
  5. No vertical scroll in log file content area
"""
import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "web" / "static"
HTML = (STATIC / "index.html").read_text()
CSS  = (STATIC / "style.css").read_text()
JS   = (STATIC / "app.js").read_text()


# ── HTML structure ────────────────────────────────────────────────────────────

class TestHtmlStructure:
    def test_main_tabs_nav_exists(self):
        assert 'id="main-tabs"' in HTML

    def test_jobs_tab_button_exists(self):
        assert 'data-view="jobs"' in HTML

    def test_settings_tab_button_exists(self):
        assert 'data-view="settings"' in HTML

    def test_jobs_view_has_main_view_class(self):
        assert re.search(r'id="jobs-view"[^>]*class="[^"]*main-view', HTML) or \
               re.search(r'class="[^"]*main-view[^"]*"[^>]*id="jobs-view"', HTML)

    def test_settings_view_has_main_view_class(self):
        assert re.search(r'id="settings-view"[^>]*class="[^"]*main-view', HTML) or \
               re.search(r'class="[^"]*main-view[^"]*"[^>]*id="settings-view"', HTML)

    def test_jobs_view_active_by_default(self):
        # jobs-view should start with active class, settings-view should not
        jobs_match = re.search(r'id="jobs-view"[^>]*class="([^"]*)"', HTML)
        settings_match = re.search(r'id="settings-view"[^>]*class="([^"]*)"', HTML)
        assert jobs_match and "active" in jobs_match.group(1)
        assert settings_match and "active" not in settings_match.group(1)

    def test_only_one_settings_body_active_by_default(self):
        # Exactly one settings-body should have settings-body--active on load
        active_count = HTML.count("settings-body--active")
        assert active_count == 1, f"Expected 1 settings-body--active, found {active_count}"

    def test_companies_tab_is_default_active(self):
        assert 'id="settings-tab-companies"' in HTML
        # companies tab should have settings-body--active
        match = re.search(r'id="settings-tab-companies"[^>]*class="([^"]*)"', HTML)
        assert match and "settings-body--active" in match.group(1)

    def test_settings_tabs_use_css_class_not_inline_display(self):
        # No settings tab body should have style="display:none" (use CSS class instead)
        for tab in ["preferences", "runs", "logs"]:
            pattern = rf'id="settings-tab-{tab}"[^>]*style="[^"]*display\s*:\s*none'
            assert not re.search(pattern, HTML), \
                f"settings-tab-{tab} uses inline display:none — use CSS class instead"

    def test_list_pane_inside_jobs_view(self):
        jobs_pos = HTML.index('id="jobs-view"')
        list_pos = HTML.index('id="list-pane"')
        settings_pos = HTML.index('id="settings-view"')
        assert jobs_pos < list_pos < settings_pos, \
            "#list-pane must be inside #jobs-view, not outside it"

    def test_settings_tabs_inside_settings_view(self):
        settings_pos = HTML.index('id="settings-view"')
        tabs_pos = HTML.index('class="settings-tabs"')
        assert tabs_pos > settings_pos, \
            ".settings-tabs must be inside #settings-view"


# ── CSS layout correctness ────────────────────────────────────────────────────

class TestCssLayout:
    def test_main_view_hidden_by_default(self):
        # .main-view must set display:none so inactive views are hidden
        match = re.search(r'\.main-view\s*\{([^}]+)\}', CSS)
        assert match, ".main-view rule not found in CSS"
        assert "display: none" in match.group(1), \
            ".main-view must have display:none to hide inactive views"

    def test_main_view_active_shows(self):
        match = re.search(r'\.main-view\.active\s*\{([^}]+)\}', CSS)
        assert match, ".main-view.active rule not found"
        assert "display: flex" in match.group(1)

    def test_main_view_has_min_height_zero(self):
        # Without min-height:0, flex children can expand beyond container
        match = re.search(r'\.main-view\s*\{([^}]+)\}', CSS)
        assert match
        assert "min-height: 0" in match.group(1), \
            ".main-view needs min-height:0 to prevent content overflow"

    def test_active_main_tab_has_background(self):
        # Active tab must have a visible background (not just color change)
        match = re.search(r'\.main-tab\.active\s*\{([^}]+)\}', CSS)
        assert match, ".main-tab.active rule not found"
        assert "background" in match.group(1), \
            ".main-tab.active needs a background to be visually distinct"

    def test_active_main_tab_has_accent_border(self):
        match = re.search(r'\.main-tab\.active\s*\{([^}]+)\}', CSS)
        assert match
        assert "border-bottom" in match.group(1)

    def test_settings_body_hidden_by_default(self):
        match = re.search(r'\.settings-body\s*\{([^}]+)\}', CSS)
        assert match, ".settings-body rule not found"
        assert "display: none" in match.group(1), \
            ".settings-body must default to display:none; use .settings-body--active to show"

    def test_runs_tab_not_max_width_constrained(self):
        # Runs table must not be capped — it needs full width to show all columns without
        # horizontal scroll. Check that settings-tab-runs is not in a max-width:640px rule.
        assert not re.search(r'#settings-tab-runs[^}]*max-width\s*:\s*640px', CSS), \
            "#settings-tab-runs must not have max-width:640px"
        # The base .settings-body must not impose max-width either
        base_match = re.search(r'\.settings-body\s*\{([^}]+)\}', CSS)
        assert base_match and "max-width" not in base_match.group(1), \
            ".settings-body base rule must not set max-width (apply it only to specific tabs)"

    def test_settings_body_active_shows(self):
        match = re.search(r'\.settings-body--active\s*\{([^}]+)\}', CSS)
        assert match, ".settings-body--active rule not found"
        assert "display" in match.group(1)

    def test_logs_tab_active_is_flex(self):
        # Logs tab needs display:flex to manage internal scroll layout
        match = re.search(r'#settings-tab-logs\.settings-body--active\s*\{([^}]+)\}', CSS)
        assert match, "#settings-tab-logs.settings-body--active rule not found"
        assert "display: flex" in match.group(1)

    def test_logs_pane_has_min_height_zero(self):
        match = re.search(r'#logs-pane\s*\{([^}]+)\}', CSS)
        assert match
        assert "min-height: 0" in match.group(1), \
            "#logs-pane needs min-height:0 or content will expand instead of scroll"

    def test_logs_pane_overflow_hidden(self):
        match = re.search(r'#logs-pane\s*\{([^}]+)\}', CSS)
        assert match
        assert "overflow: hidden" in match.group(1)

    def test_logs_file_list_has_min_height_zero(self):
        match = re.search(r'#logs-file-list\s*\{([^}]+)\}', CSS)
        assert match
        assert "min-height: 0" in match.group(1), \
            "#logs-file-list needs min-height:0 to enable scrolling"

    def test_logs_file_list_scrollable(self):
        match = re.search(r'#logs-file-list\s*\{([^}]+)\}', CSS)
        assert match
        assert "overflow-y: auto" in match.group(1)

    def test_logs_content_body_has_min_height_zero(self):
        match = re.search(r'#logs-content-body\s*\{([^}]+)\}', CSS)
        assert match
        assert "min-height: 0" in match.group(1), \
            "#logs-content-body needs min-height:0 — without it content expands container"

    def test_logs_content_body_scrollable(self):
        match = re.search(r'#logs-content-body\s*\{([^}]+)\}', CSS)
        assert match
        assert "overflow-y: auto" in match.group(1)

    def test_logs_content_body_prewrap(self):
        match = re.search(r'#logs-content-body\s*\{([^}]+)\}', CSS)
        assert match
        assert "pre-wrap" in match.group(1), \
            "#logs-content-body needs white-space:pre-wrap to preserve log formatting"

    def test_logs_file_content_has_min_height_zero(self):
        match = re.search(r'#logs-file-content\s*\{([^}]+)\}', CSS)
        assert match
        assert "min-height: 0" in match.group(1)


# ── JS behaviour ──────────────────────────────────────────────────────────────

class TestJsBehaviour:
    def test_switch_view_function_exists(self):
        assert "function switchView(" in JS

    def test_switch_view_toggles_main_view_class(self):
        # switchView must toggle the 'active' class on the view elements
        switch_fn = re.search(
            r'function switchView\(([^)]+)\)\s*\{([\s\S]+?)(?=\n  function |\n  return )',
            JS
        )
        assert switch_fn, "switchView function body not found"
        body = switch_fn.group(2)
        assert "classList.toggle" in body, "switchView must use classList.toggle to switch views"
        assert "active" in body

    def test_switch_view_closes_detail_pane_on_settings(self):
        # Navigating to settings must close any open detail pane overlay
        switch_fn = re.search(
            r'function switchView\(([^)]+)\)\s*\{([\s\S]+?)(?=\n  function |\n  return )',
            JS
        )
        assert switch_fn
        body = switch_fn.group(2)
        assert 'detail-pane' in body and 'remove' in body, \
            "switchView must remove 'open' from #detail-pane when switching to settings"

    def test_settings_tab_uses_classlist_not_inline_style(self):
        # settingsTab must use classList.toggle, not style.display
        tab_fn = re.search(
            r'function settingsTab\(([^)]+)\)\s*\{([\s\S]+?)(?=\n  function |\n  async function )',
            JS
        )
        assert tab_fn, "settingsTab function body not found"
        body = tab_fn.group(2)
        assert "classList.toggle" in body, \
            "settingsTab must use classList.toggle('settings-body--active') not style.display"
        assert "style.display" not in body, \
            "settingsTab must not set style.display — use CSS class instead"

    def test_settings_tab_activates_correct_class(self):
        tab_fn = re.search(
            r'function settingsTab\(([^)]+)\)\s*\{([\s\S]+?)(?=\n  function |\n  async function )',
            JS
        )
        assert tab_fn
        body = tab_fn.group(2)
        assert "settings-body--active" in body

    def test_switch_view_exported(self):
        # The module's public return object is the last "return {" in the file
        assert "switchView" in JS.split("return {")[-1]
