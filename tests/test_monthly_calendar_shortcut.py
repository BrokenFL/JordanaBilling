"""Calendar export format and boundary contracts, using fictional events only."""
import importlib.util
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jordana_invoice.monthly_calendar_review import CALENDAR_COLUMNS, parse_calendar_csv

spec = importlib.util.spec_from_file_location("monthly_shortcut_builder", Path(__file__).resolve().parents[1] / "scripts/build_monthly_calendar_shortcut.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class MonthlyCalendarShortcutTests(unittest.TestCase):
    def test_export_headers_agree_with_the_app_parser(self):
        self.assertEqual(builder.COLUMNS, CALENDAR_COLUMNS)

    def test_all_months_have_complete_eastern_boundaries(self):
        bounds = builder.month_bounds()
        self.assertEqual(len(bounds), 1200)
        for month, entry in bounds.items():
            start, end = datetime.fromisoformat(entry["Start"]), datetime.fromisoformat(entry["End"])
            local_start = start.astimezone(ZoneInfo("America/New_York"))
            next_month = (end + timedelta(seconds=1)).astimezone(ZoneInfo("America/New_York"))
            self.assertEqual(local_start.strftime("%Y-%m"), month)
            self.assertEqual((local_start.day, local_start.hour, local_start.minute, local_start.second), (1, 0, 0, 0))
            self.assertEqual((next_month.day, next_month.hour, next_month.minute, next_month.second), (1, 0, 0, 0))
            self.assertNotEqual(next_month.month, local_start.month)

    def test_leap_year_daylight_saving_and_year_end(self):
        bounds = builder.month_bounds()
        self.assertTrue(bounds["2028-02"]["End"].startswith("2028-02-29T23:59:59"))
        self.assertTrue(bounds["2026-03"]["Start"].endswith("-05:00"))
        self.assertTrue(bounds["2026-03"]["End"].endswith("-04:00"))
        self.assertTrue(bounds["2026-11"]["Start"].endswith("-04:00"))
        self.assertTrue(bounds["2026-11"]["End"].endswith("-05:00"))
        self.assertEqual(bounds["2026-12"]["End"], "2026-12-31T23:59:59-05:00")

    def test_native_row_template_preserves_quoted_multiline_text(self):
        actions = builder.build_shortcut()["WFWorkflowActions"]
        row = next(a["WFWorkflowActionParameters"]["WFTextActionText"] for a in actions if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettext" and isinstance(a["WFWorkflowActionParameters"].get("WFTextActionText"), dict))
        values = {"Export Month": "2026-09", "Export Timestamp": "2026-10-01T09:00:00-04:00", "Quoted Calendar": 'Work, ""shared""',
                  "Quoted Title": 'Example, ""quoted"" café\nsecond line', "Start Timestamp": "2026-09-09T14:00:00-04:00",
                  "End Timestamp": "2026-09-09T15:00:00-04:00", "Duration Minutes": "60"}
        text = row["Value"]["string"]
        for position, attachment in sorted(row["Value"]["attachmentsByRange"].items(), key=lambda entry: int(entry[0].strip("{}").split(",")[0]), reverse=True):
            start = int(position.strip("{}").split(",")[0])
            text = text[:start] + values[attachment["VariableName"]] + text[start + 1:]
        events, _ = parse_calendar_csv(",".join(builder.COLUMNS) + "\n" + text + "\n", "2026-09")
        self.assertEqual(events[0]["title"], 'Example, "quoted" café\nsecond line')
        self.assertEqual(events[0]["calendar"], 'Work, "shared"')

    def test_full_month_query_uses_all_calendars_no_limit_and_excludes_all_day(self):
        actions = builder.build_shortcut()["WFWorkflowActions"]
        found = next(a["WFWorkflowActionParameters"] for a in actions if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.filter.calendarevents")
        self.assertFalse(found["WFContentItemLimitEnabled"])
        predicates = found["WFContentItemFilter"]["Value"]["WFActionParameterFilterTemplates"]
        self.assertEqual({p["Property"] for p in predicates}, {"Start Date", "Is All Day"})
        self.assertFalse(next(p for p in predicates if p["Property"] == "Is All Day")["Values"]["Bool"])
        self.assertEqual(next(p for p in predicates if p["Property"] == "Start Date")["Operator"], 1003)

    def test_export_has_no_network_or_calendar_write_actions_or_notes(self):
        shortcut = builder.build_shortcut()
        actions = shortcut["WFWorkflowActions"]
        for action in actions:
            suffix = action["WFWorkflowActionIdentifier"].split("actions.")[1]
            self.assertNotIn(suffix, {"downloadurl", "url", "addnewevent", "removeevents", "setters.calendarevents"})
            if suffix == "properties.calendarevents":
                self.assertIn(action["WFWorkflowActionParameters"]["WFContentItemPropertyName"], {"Title", "Calendar", "Start Date", "End Date"})
        saver = next(a["WFWorkflowActionParameters"] for a in actions if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.documentpicker.save")
        self.assertFalse(saver["WFSaveFileOverwrite"])
        self.assertTrue(saver["WFAskWhereToSave"])
