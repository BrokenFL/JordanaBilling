"""Raw-first appointment coverage; all records are fictional."""
from unittest.mock import patch
from test_historical_review import HistoricalReviewTests, event
from jordana_invoice.parser import parse_event
from jordana_invoice.review_services import list_review_candidates, reparse_candidate_only_duration_suffixes, mark_candidate
from jordana_invoice.historical_review import reconcile_historical_review


class ZoomReviewTests(HistoricalReviewTests):
    def test_zoom_time_duration_and_method(self):
        for title, minutes in [('Alex Example 1130 zoom',60), ('Alex Example 1130 30 Zoom',30), ('Alex Example | 60 | Zoom',60)]:
            parsed = parse_event({**event('a', title), 'start_at':'2026-08-10T11:30:00-04:00'})
            self.assertEqual(parsed.classification,'client_session')
            self.assertEqual(parsed.appointment_method,'zoom')
            self.assertEqual(parsed.proposed_duration_minutes,minutes)
            self.assertEqual(parsed.proposed_start_at,'2026-08-10T11:30:00-04:00')

    def test_existing_candidate_without_session_is_repaired_once(self):
        # Reproduce the old parser, including the stored raw title.
        from jordana_invoice import parser
        aliases = dict(parser.SERVICE_MODE_ALIASES); aliases.pop('zoom')
        with patch.dict(parser.SERVICE_MODE_ALIASES, aliases, clear=True):
            self.load(event('old','Alex Example 2 zoom'))
        self.assertEqual(self.conn.execute('select count(*) from sessions').fetchone()[0],0)
        raw = [tuple(r) for r in self.conn.execute('select * from raw_calendar_snapshots')]
        self.assertEqual(list_review_candidates(self.conn)['total'],1) # routing fallback works before repair
        repaired = reparse_candidate_only_duration_suffixes(self.conn)
        self.assertEqual(repaired['sessions_created'],1)
        reconcile_historical_review(self.conn)
        result = list_review_candidates(self.conn)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['service_mode'],'zoom')
        self.assertNotEqual(result['items'][0]['status'],'approved')
        self.assertEqual(reparse_candidate_only_duration_suffixes(self.conn)['sessions_created'],0)
        self.assertEqual(raw,[tuple(r) for r in self.conn.execute('select * from raw_calendar_snapshots')])
        self.assertEqual(self.conn.execute('select count(*) from people').fetchone()[0],0)

    def test_unrecognized_suffix_stays_visible_but_reminders_do_not(self):
        self.load(event('a','Alex Example 2 mystery'),event('b','Book Flight Tomorrow'),event('c','Reunion zoom 2'))
        items=list_review_candidates(self.conn)['items']
        self.assertEqual([r['raw_title'] for r in items],['Alex Example 2 mystery'])
        self.assertEqual(items[0]['status'],'needs_classification')
        mark_candidate(self.conn,items[0]['candidate_id'],classification='nonbillable')
        self.assertEqual(list_review_candidates(self.conn)['total'],0)

    def test_mixed_candidate_and_session_pagination(self):
        self.load(event('a','Alex Example 2 mystery'),event('b','Casey Example 4',start='2026-08-10T16:00:00-04:00',end='2026-08-10T17:00:00-04:00'))
        first=list_review_candidates(self.conn,limit=1)
        second=list_review_candidates(self.conn,limit=1,offset=1)
        self.assertEqual(first['total'],2)
        self.assertEqual(first['items'][0]['raw_title'],'Casey Example 4')
        self.assertEqual(second['items'][0]['raw_title'],'Alex Example 2 mystery')

    def test_reminder_with_year_and_party_are_not_appointments(self):
        self.load(event('a','Received 2026 package today'),event('b','Casey 11 birthday party'))
        self.assertEqual(list_review_candidates(self.conn)['total'],0)

    def test_review_count_includes_unresolved_appointment_not_obsolete_schedule(self):
        self.load(event('a','Alex Example 2 mystery'),event('b','Casey Example 4',window='next_7_days',version='2'))
        result=list_review_candidates(self.conn)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['status']['needs_review'],1)
