#!/usr/bin/env python3
"""Build the validated v3 calendar Shortcut from an exported v2 plist.

The input and output contain the live endpoint and ingest key.  This script
never prints either value and writes the output with owner-only permissions.
"""

from __future__ import annotations

import argparse
import copy
import os
import plistlib
from pathlib import Path
from typing import Any


FUTURE_DAYS = "2"
FUTURE_CAPTURE_WINDOW = "next_2_days"
PAST_CAPTURE_WINDOW = "past_3_days"
PAYLOAD_VERSION = "3"
OUTPUT_NAME = "Jordana Calendar Sync v3"
ICON_GLYPH = 59681
ICON_COLOR = 4274264319


def _text_value(value: str) -> dict[str, Any]:
    return {
        "Value": {"string": value},
        "WFSerializationType": "WFTextTokenString",
    }


def _variable_text_value(
    variable_name: str,
    *,
    property_name: str | None = None,
) -> dict[str, Any]:
    attachment: dict[str, Any] = {
        "Type": "Variable",
        "VariableName": variable_name,
    }
    if property_name:
        attachment["Aggrandizements"] = [
            {
                "PropertyName": property_name,
                "Type": "WFPropertyVariableAggrandizement",
            }
        ]
    return {
        "Value": {
            "attachmentsByRange": {"{0, 1}": attachment},
            "string": "\ufffc",
        },
        "WFSerializationType": "WFTextTokenString",
    }


def _field_item(
    key: str,
    value: dict[str, Any],
    *,
    item_type: int = 0,
) -> dict[str, Any]:
    return {
        "WFItemType": item_type,
        "WFKey": _text_value(key),
        "WFValue": value,
    }


def _comment(text: str) -> dict[str, Any]:
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.comment",
        "WFWorkflowActionParameters": {"WFCommentActionText": text},
    }


def _dictionary_items(action: dict[str, Any], parameter: str) -> list[dict[str, Any]]:
    try:
        return action["WFWorkflowActionParameters"][parameter]["Value"][
            "WFDictionaryFieldValueItems"
        ]
    except (KeyError, TypeError) as error:
        raise ValueError(f"Malformed {parameter} action.") from error


def _field_key(item: dict[str, Any]) -> str:
    try:
        return str(item["WFKey"]["Value"]["string"])
    except (KeyError, TypeError) as error:
        raise ValueError("Malformed Shortcut dictionary field.") from error


def _field_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_field_key(item): item for item in items}


def _literal_field_value(item: dict[str, Any]) -> str:
    try:
        return str(item["WFValue"]["Value"]["string"])
    except (KeyError, TypeError) as error:
        raise ValueError(f"Shortcut field {_field_key(item)!r} is not a literal value.") from error


def _set_literal_field(item: dict[str, Any], value: str) -> None:
    item["WFValue"] = _text_value(value)


def _request_record_type(action: dict[str, Any]) -> str:
    fields = _field_map(_dictionary_items(action, "WFJSONValues"))
    record_type = fields.get("record_type")
    return _literal_field_value(record_type) if record_type else ""


def _insert_field_once(
    items: list[dict[str, Any]],
    field: dict[str, Any],
    *,
    after: str | None = None,
) -> None:
    key = _field_key(field)
    if key in _field_map(items):
        raise ValueError(f"Shortcut field {key!r} already exists.")
    if after:
        for index, item in enumerate(items):
            if _field_key(item) == after:
                items.insert(index + 1, field)
                return
    items.append(field)


def _update_future_filter(actions: list[dict[str, Any]]) -> None:
    updated = 0
    for action in actions:
        if action.get("WFWorkflowActionIdentifier") != "is.workflow.actions.filter.calendarevents":
            continue
        params = action.get("WFWorkflowActionParameters", {})
        templates = (
            params.get("WFContentItemFilter", {})
            .get("Value", {})
            .get("WFActionParameterFilterTemplates", [])
        )
        for template in templates:
            if template.get("Property") == "Start Date" and template.get("Operator") == 1000:
                values = template.setdefault("Values", {})
                if str(values.get("Number", "")) not in {"7", FUTURE_DAYS}:
                    raise ValueError("Unexpected future-calendar window in the base Shortcut.")
                values["Number"] = FUTURE_DAYS
                updated += 1
    if updated != 1:
        raise ValueError(f"Expected one future-calendar filter; found {updated}.")


def _update_count_actions(actions: list[dict[str, Any]]) -> None:
    updated = 0
    for action in actions:
        if action.get("WFWorkflowActionIdentifier") != "is.workflow.actions.count":
            continue
        params = action.get("WFWorkflowActionParameters", {})
        input_value = params.get("Input") or params.get("WFInput")
        if not input_value:
            raise ValueError("Count action has no input.")
        params["Input"] = copy.deepcopy(input_value)
        params["WFInput"] = copy.deepcopy(input_value)
        updated += 1
    if updated != 2:
        raise ValueError(f"Expected two Count actions; found {updated}.")


def _remove_unsafe_calendar_event_urls(actions: list[dict[str, Any]]) -> None:
    """Do not mistake an event's optional URL field for its Calendar identity."""

    dictionaries_found = 0
    for action in actions:
        if action.get("WFWorkflowActionIdentifier") != "is.workflow.actions.dictionary":
            continue
        items = _dictionary_items(action, "WFItems")
        fields = _field_map(items)
        if not {"event_title", "start_at", "end_at", "calendar"}.issubset(fields):
            continue
        items[:] = [item for item in items if _field_key(item) != "calendar_event_id"]
        dictionaries_found += 1
    if dictionaries_found != 2:
        raise ValueError(
            f"Expected two calendar event dictionaries; found {dictionaries_found}."
        )


def _update_requests(actions: list[dict[str, Any]]) -> None:
    batches = 0
    run_completions = 0
    for action in actions:
        if action.get("WFWorkflowActionIdentifier") != "is.workflow.actions.downloadurl":
            continue
        items = _dictionary_items(action, "WFJSONValues")
        fields = _field_map(items)
        record_type = _request_record_type(action)
        if "payload_version" not in fields:
            raise ValueError("Request is missing payload_version.")
        _set_literal_field(fields["payload_version"], PAYLOAD_VERSION)

        if record_type == "calendar_batch":
            capture_window = _literal_field_value(fields["capture_window"])
            if capture_window == "next_7_days":
                _set_literal_field(fields["capture_window"], FUTURE_CAPTURE_WINDOW)
            elif capture_window != PAST_CAPTURE_WINDOW:
                raise ValueError(f"Unexpected calendar batch window: {capture_window!r}.")
            batches += 1
            continue

        if record_type == "run_complete":
            _insert_field_once(
                items,
                _field_item("future_capture_window", _text_value(FUTURE_CAPTURE_WINDOW)),
                after="batch_name",
            )
            _insert_field_once(
                items,
                _field_item(
                    "past_events_received",
                    _variable_text_value("Past Count"),
                    item_type=3,
                ),
                after="past_events_found",
            )
            _insert_field_once(
                items,
                _field_item(
                    "future_events_received",
                    _variable_text_value("Future Count"),
                    item_type=3,
                ),
                after="future_events_found",
            )
            run_completions += 1
            continue

        raise ValueError(f"Unexpected POST request type: {record_type!r}.")

    if batches != 2 or run_completions != 1:
        raise ValueError(
            f"Expected two calendar batches and one run completion; found {batches} and {run_completions}."
        )


def _add_comments(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if any(action.get("WFWorkflowActionIdentifier") == "is.workflow.actions.comment" for action in actions):
        raise ValueError("The base Shortcut already contains comments; refusing a duplicate remix.")

    output = [
        _comment(
            "Capture non-all-day Apple Calendar events for billing reconciliation. "
            "The next two days are raw scheduling evidence only. A completed event becomes "
            "billing evidence only through the past-three-days capture. This Shortcut never "
            "approves a session or creates an invoice."
        ),
        _comment(
            "Shortcuts generated by Shortcuts Playground. May contain mistakes. Always check "
            "the shortcut's actions first.\n\n"
            "This shortcut was created via the following user prompt:\n\n"
            "> Proceed with the implementation plan using the current Jordana Calendar Sync "
            "Shortcut as the base, and double-check it before moving to the next step."
        ),
    ]

    for action in actions:
        identifier = action.get("WFWorkflowActionIdentifier")
        params = action.get("WFWorkflowActionParameters", {})
        if identifier == "is.workflow.actions.filter.calendarevents":
            templates = (
                params.get("WFContentItemFilter", {})
                .get("Value", {})
                .get("WFActionParameterFilterTemplates", [])
            )
            future = any(
                template.get("Property") == "Start Date" and template.get("Operator") == 1000
                for template in templates
            )
            output.append(
                _comment(
                    "--- CAPTURE FUTURE SCHEDULE ---\n"
                    "Keep the next two days as raw scheduling evidence for later comparison."
                    if future
                    else "--- CAPTURE COMPLETED SESSIONS ---\n"
                    "Capture the past three days so completed appointments can enter Review."
                )
            )
        if identifier == "is.workflow.actions.repeat.each" and params.get("WFControlFlowMode") == 0:
            input_state = params.get("WFInput", {}).get("Value", {})
            variable_name = input_state.get("VariableName", "Calendar Events")
            output.append(
                _comment(
                    f"- Input uses {variable_name} from Find Calendar Events above\n"
                    "- Each Repeat Item becomes one raw event dictionary"
                )
            )
        if identifier == "is.workflow.actions.date" and len(output) > 2:
            # The second Date action begins the final run-completion block.
            date_actions_seen = sum(
                1
                for existing in output
                if existing.get("WFWorkflowActionIdentifier") == "is.workflow.actions.date"
            )
            if date_actions_seen == 1:
                output.append(
                    _comment(
                        "--- COMPLETE RUN ---\n"
                        "Record the exact past and future counts so the audit log can verify the run."
                    )
                )
        output.append(action)
    return output


def remix_shortcut(shortcut: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(shortcut)
    actions = result.get("WFWorkflowActions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("Shortcut has no actions.")

    _update_future_filter(actions)
    _update_count_actions(actions)
    _remove_unsafe_calendar_event_urls(actions)
    _update_requests(actions)
    result["WFWorkflowActions"] = _add_comments(actions)
    result["WFWorkflowName"] = OUTPUT_NAME
    result["WFWorkflowInputContentItemClasses"] = []
    result["WFWorkflowHasShortcutInputVariables"] = False
    result["WFWorkflowIcon"] = {
        "WFWorkflowIconGlyphNumber": ICON_GLYPH,
        "WFWorkflowIconStartColor": ICON_COLOR,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    with args.input.open("rb") as handle:
        source = plistlib.load(handle)
    result = remix_shortcut(source)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(args.output.parent, 0o700)
    with args.output.open("wb") as handle:
        plistlib.dump(result, handle, fmt=plistlib.FMT_XML, sort_keys=False)
    os.chmod(args.output, 0o600)

    print(f"Wrote private Shortcut remix with {len(result['WFWorkflowActions'])} actions.")
    print("Endpoint and key were preserved without being printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
