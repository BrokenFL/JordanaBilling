import unittest

from jordana_invoice.parser import parse_event


def event(title, start="2026-06-18T18:30:00-04:00", end="2026-06-18T19:30:00-04:00"):
    return {
        "event_title": title,
        "start_at": start,
        "end_at": end,
        "duration_minutes": 60,
    }


class ParserTests(unittest.TestCase):
    def test_bonnie_time_default_duration(self):
        result = parse_event(
            event(
                "Bonnie 5",
                "2026-06-18T17:00:00-04:00",
                "2026-06-18T18:00:00-04:00",
            )
        )
        self.assertEqual(result.classification, "client_session")
        self.assertEqual(result.proposed_client_name, "Bonnie")
        self.assertEqual(result.proposed_duration_minutes, 60)
        self.assertEqual(result.duration_source, "calendar")
        self.assertIn("client_full_name", result.fields_requiring_review)

    def test_explicit_duration_overrides_calendar_duration(self):
        result = parse_event(event("Leah Grossman 630 30"))
        self.assertEqual(result.classification, "client_session")
        self.assertEqual(result.proposed_client_name, "Leah Grossman")
        self.assertEqual(result.proposed_duration_minutes, 30)
        self.assertEqual(result.duration_source, "title")

    def test_rebecca_colon_typo(self):
        result = parse_event(event("Rebecca colon 630 90"))
        self.assertEqual(result.proposed_client_name, "Rebecca Colon")
        self.assertEqual(result.proposed_duration_minutes, 90)

    def test_question_mark_is_unresolved(self):
        result = parse_event(event("Raisin??"))
        self.assertEqual(result.classification, "unresolved")
        self.assertIn("client", result.fields_requiring_review)

    def test_mani_pedi_goes_to_personal_review(self):
        result = parse_event(
            event(
                "Mani pedi 4",
                "2026-06-18T16:00:00-04:00",
                "2026-06-18T17:00:00-04:00",
            )
        )
        self.assertEqual(result.classification, "personal")
        self.assertIn("exclusion_alias", result.fields_requiring_review)

    def test_for_reference_preserves_participant_and_marks_review(self):
        result = parse_event(
            event(
                "Caitlin Schneider 530 for Sage at 5:30 PM",
                "2026-06-18T17:30:00-04:00",
                "2026-06-18T18:30:00-04:00",
            )
        )
        self.assertEqual(result.classification, "unresolved")
        self.assertEqual(result.proposed_client_name, "Caitlin Schneider")
        self.assertEqual(result.possible_referenced_person, "Sage at 5:30 PM")
        self.assertIn("classification", result.fields_requiring_review)
        self.assertIn("participants", result.fields_requiring_review)
        self.assertIn("relationship_role", result.fields_requiring_review)

    def test_unresolved_title_applies_duration_and_session_type_before_client_match(self):
        result = parse_event(event("Mystery Client 90 House"))
        self.assertEqual(result.classification, "unresolved")
        self.assertEqual(result.proposed_client_name, "Mystery Client")
        self.assertEqual(result.proposed_duration_minutes, 90)
        self.assertEqual(result.duration_source, "title")
        self.assertEqual(result.duration_choice, "90")
        self.assertEqual(result.billing_session_type, "psychotherapy_house_call")
        self.assertEqual(result.appointment_method, "office")

    def test_unresolved_title_applies_weekend_and_evening_categories(self):
        result = parse_event(
            event(
                "Mystery Client 60 Office",
                "2026-06-20T20:00:00-04:00",
                "2026-06-20T21:00:00-04:00",
            )
        )
        self.assertEqual(result.classification, "unresolved")
        self.assertTrue(result.is_weekend)
        self.assertTrue(result.is_evening)
        self.assertEqual(result.time_category, "weekend")
        self.assertEqual(result.billing_session_type, "psychotherapy_weekend")

    def test_status_title_applies_title_rules_without_client_match(self):
        result = parse_event(event("Mystery Client 90 Office late cancel"))
        self.assertEqual(result.classification, "late_cancellation")
        self.assertEqual(result.appointment_status, "late_cancellation")
        self.assertEqual(result.proposed_duration_minutes, 90)
        self.assertEqual(result.duration_source, "title")
        self.assertEqual(result.duration_choice, "90")
        self.assertEqual(result.billing_session_type, "psychotherapy")


if __name__ == "__main__":
    unittest.main()
