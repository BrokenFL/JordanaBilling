from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "remix_calendar_shortcut.py"
SPEC = importlib.util.spec_from_file_location("calendar_shortcut_remix", SCRIPT_PATH)
assert SPEC and SPEC.loader
remix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remix)


def text(value: str):
    return {"Value": {"string": value}, "WFSerializationType": "WFTextTokenString"}


def variable(name: str):
    return {
        "Value": {"Type": "Variable", "VariableName": name},
        "WFSerializationType": "WFTextTokenAttachment",
    }


def field(key: str, value, item_type=0):
    return {"WFItemType": item_type, "WFKey": text(key), "WFValue": value}


def dictionary_action():
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.dictionary",
        "WFWorkflowActionParameters": {
            "WFItems": {
                "Value": {
                    "WFDictionaryFieldValueItems": [
                        field("event_title", text("Example")),
                        field("start_at", text("start")),
                        field("end_at", text("end")),
                        field("notes", text("preserve raw notes")),
                        field("calendar", text("Calendar")),
                    ]
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            }
        },
    }


def request_action(record_type: str, capture_window: str | None = None):
    items = [
        field("api_key", variable("API Key")),
        field("payload_version", text("2"), 3),
        field("record_type", text(record_type)),
        field("batch_name", variable("Batch Name")),
    ]
    if capture_window:
        items.append(field("capture_window", text(capture_window)))
        items.append(field("events", variable("Event Dictionaries"), 2))
    else:
        items.extend(
            [
                field("past_events_found", variable("Past Count"), 3),
                field("future_events_found", variable("Future Count"), 3),
            ]
        )
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "WFURL": variable("API URL"),
            "WFJSONValues": {
                "Value": {"WFDictionaryFieldValueItems": items},
                "WFSerializationType": "WFDictionaryFieldValue",
            },
        },
    }


def filter_action(operator: int, number: str):
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.filter.calendarevents",
        "WFWorkflowActionParameters": {
            "WFContentItemFilter": {
                "Value": {
                    "WFActionParameterFilterTemplates": [
                        {"Property": "Start Date", "Operator": operator, "Values": {"Number": number}}
                    ]
                }
            }
        },
    }


def repeat_action(mode: int, variable_name: str = ""):
    params = {"WFControlFlowMode": mode, "GroupingIdentifier": "BFD9D146-87E8-46FD-8FB6-105B497B85D2"}
    if mode == 0:
        params["WFInput"] = variable(variable_name)
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each", "WFWorkflowActionParameters": params}


class CalendarShortcutRemixTests(unittest.TestCase):
    def test_remix_updates_contract_and_preserves_sensitive_inputs(self):
        source = {
            "WFWorkflowActions": [
                {"WFWorkflowActionIdentifier": "is.workflow.actions.date", "WFWorkflowActionParameters": {}},
                {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext", "WFWorkflowActionParameters": {"WFTextActionText": "https://example.test/exec"}},
                {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext", "WFWorkflowActionParameters": {"WFTextActionText": "jb_test_only"}},
                filter_action(1000, "7"),
                {"WFWorkflowActionIdentifier": "is.workflow.actions.count", "WFWorkflowActionParameters": {"Input": variable("Future Calendar Events")}},
                repeat_action(0, "Future Calendar Events"),
                dictionary_action(),
                repeat_action(2),
                request_action("calendar_batch", "next_7_days"),
                filter_action(1001, "3"),
                {"WFWorkflowActionIdentifier": "is.workflow.actions.count", "WFWorkflowActionParameters": {"Input": variable("Past Calendar Events")}},
                repeat_action(0, "Past Calendar Events"),
                dictionary_action(),
                repeat_action(2),
                request_action("calendar_batch", "past_3_days"),
                {"WFWorkflowActionIdentifier": "is.workflow.actions.date", "WFWorkflowActionParameters": {}},
                request_action("run_complete"),
            ],
            "WFWorkflowIcon": {},
            "WFWorkflowInputContentItemClasses": ["WFStringContentItem"],
        }

        result = remix.remix_shortcut(source)

        self.assertEqual(result["WFWorkflowName"], "Jordana Calendar Sync v3")
        self.assertEqual(result["WFWorkflowInputContentItemClasses"], [])
        self.assertFalse(result["WFWorkflowHasShortcutInputVariables"])
        self.assertEqual(result["WFWorkflowIcon"]["WFWorkflowIconGlyphNumber"], 59681)
        self.assertEqual(result["WFWorkflowIcon"]["WFWorkflowIconStartColor"], 4274264319)

        actions = result["WFWorkflowActions"]
        comments = [a for a in actions if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.comment"]
        self.assertGreaterEqual(len(comments), 5)
        for index, action in enumerate(actions):
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.repeat.each" and action["WFWorkflowActionParameters"].get("WFControlFlowMode") == 0:
                self.assertEqual(actions[index - 1]["WFWorkflowActionIdentifier"], "is.workflow.actions.comment")

        text_values = [
            a["WFWorkflowActionParameters"].get("WFTextActionText")
            for a in actions
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettext"
        ]
        self.assertIn("https://example.test/exec", text_values)
        self.assertIn("jb_test_only", text_values)

        filters = [a for a in actions if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.filter.calendarevents"]
        future_values = filters[0]["WFWorkflowActionParameters"]["WFContentItemFilter"]["Value"]["WFActionParameterFilterTemplates"][0]["Values"]
        self.assertEqual(future_values["Number"], "2")

        counts = [a for a in actions if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.count"]
        self.assertTrue(all("Input" in a["WFWorkflowActionParameters"] for a in counts))
        self.assertTrue(all("WFInput" in a["WFWorkflowActionParameters"] for a in counts))

        dictionaries = [a for a in actions if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.dictionary"]
        for action in dictionaries:
            items = remix._dictionary_items(action, "WFItems")
            fields = remix._field_map(items)
            self.assertNotIn("calendar_event_id", fields)
            self.assertEqual(remix._literal_field_value(fields["notes"]), "preserve raw notes")

        requests = [a for a in actions if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"]
        by_type = {remix._request_record_type(a): a for a in requests}
        future_fields = remix._field_map(remix._dictionary_items(requests[0], "WFJSONValues"))
        self.assertEqual(remix._literal_field_value(future_fields["capture_window"]), "next_2_days")
        for action in requests:
            fields = remix._field_map(remix._dictionary_items(action, "WFJSONValues"))
            self.assertEqual(remix._literal_field_value(fields["payload_version"]), "3")
        complete_fields = remix._field_map(remix._dictionary_items(by_type["run_complete"], "WFJSONValues"))
        self.assertEqual(remix._literal_field_value(complete_fields["future_capture_window"]), "next_2_days")
        self.assertIn("past_events_received", complete_fields)
        self.assertIn("future_events_received", complete_fields)


if __name__ == "__main__":
    unittest.main()
