import unittest

from jordana_invoice.parser import parse_event


def event(title, start="2026-06-18T18:30:00-04:00", end="2026-06-18T19:30:00-04:00", duration=60):
    return {
        "event_title": title,
        "start_at": start,
        "end_at": end,
        "duration_minutes": duration,
    }


class Phase2ParserTests(unittest.TestCase):
    def test_leah_duration_overrides_calendar_duration(self):
        result = parse_event(event("Leah Grossman 630 30", duration=60))
        self.assertEqual(result.proposed_client_name, "Leah Grossman")
        self.assertEqual(result.time_shorthand, "630")
        self.assertEqual(result.proposed_duration_minutes, 30)
        self.assertEqual(result.duration_source, "title")
        self.assertIn("duration_discrepancy", result.unresolved_fields)

    def test_rebecca_colon_normalizes_name(self):
        result = parse_event(event("Rebecca colon 630 90"))
        self.assertEqual(result.proposed_client_name, "Rebecca Colon")
        self.assertEqual(result.proposed_duration_minutes, 90)

    def test_brett_am_time_marker(self):
        result = parse_event(
            event(
                "Brett Barakett 11 AM",
                "2026-06-18T11:00:00-04:00",
                "2026-06-18T12:00:00-04:00",
            )
        )
        self.assertEqual(result.proposed_client_name, "Brett Barakett")
        self.assertEqual(result.time_shorthand, "11 AM")
        self.assertTrue(result.title_time_matches_calendar)

    def test_multiple_names_require_relationship_review(self):
        result = parse_event(
            event(
                "Bobsey and Fred 6",
                "2026-06-18T18:00:00-04:00",
                "2026-06-18T19:00:00-04:00",
            )
        )
        self.assertEqual(result.classification, "client_session")
        self.assertTrue(result.relationship_review_required)
        self.assertEqual(result.candidate_person_names, ["Bobsey", "Fred"])
        self.assertIn("participants", result.unresolved_fields)

    def test_for_phrase_requires_relationship_review(self):
        result = parse_event(
            event(
                "Caitlin Schneider 530 for Sage",
                "2026-06-18T17:30:00-04:00",
                "2026-06-18T18:30:00-04:00",
            )
        )
        self.assertEqual(result.classification, "unresolved")
        self.assertIn("classification", result.unresolved_fields)

    def test_admin_extracts_possible_person(self):
        result = parse_event(event("Ask Jenny g for email for invoice"))
        self.assertEqual(result.classification, "administrative")
        self.assertEqual(result.possible_referenced_person, "Jenny G")

    def test_future_pipe_format(self):
        result = parse_event(event("Bonnie Smith | 60 | Phone"))
        self.assertEqual(result.proposed_client_name, "Bonnie Smith")
        self.assertEqual(result.service_mode, "phone")
        self.assertEqual(result.rate_group, "remote")

    def test_evening_and_weekend_categories(self):
        evening = parse_event(event("Bonnie 830", "2026-06-18T20:30:00-04:00", "2026-06-18T21:30:00-04:00"))
        saturday = parse_event(event("Bonnie 11", "2026-06-20T11:00:00-04:00", "2026-06-20T12:00:00-04:00"))
        saturday_evening = parse_event(event("Bonnie 830", "2026-06-20T20:30:00-04:00", "2026-06-20T21:30:00-04:00"))
        self.assertEqual(evening.time_category, "evening")
        self.assertEqual(saturday.time_category, "weekend")
        self.assertEqual(saturday_evening.time_category, "weekend")


if __name__ == "__main__":
    unittest.main()
