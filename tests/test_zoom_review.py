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

    def test_repair_old_automatic_exclusion_preserves_saved_values(self):
        from jordana_invoice.review_services import repair_automatic_parser_exclusions
        from jordana_invoice.util import new_id, now_iso
        self.load(event('a','Alex Example 2 zoom'))
        cid=self.candidate('Alex Example 2 zoom')['id']
        sid=self.conn.execute('select id from sessions where candidate_id=?',(cid,)).fetchone()[0]
        self.conn.execute("update sessions set review_status='excluded',billable_status='excluded',hidden_from_review=1,approved_rate_cents=17500,approved_duration_minutes=90 where id=?",(sid,))
        self.conn.execute("update calendar_event_candidates set classification='unresolved',review_status='needs_classification' where id=?",(cid,))
        self.conn.execute("insert into audit_log(id,entity_type,entity_id,action,details,created_at) values (?,'session',?,'excluded_from_latest_calendar_snapshot',?,?)",(new_id(),sid,'{"latest_classification":"unresolved"}',now_iso()))
        self.assertEqual(repair_automatic_parser_exclusions(self.conn),1)
        row=self.conn.execute('select * from sessions where id=?',(sid,)).fetchone()
        self.assertEqual(row['billable_status'],'proposed');self.assertEqual(row['approved_rate_cents'],17500)
        self.assertEqual(row['duration_minutes'],90);self.assertEqual(row['service_mode'],'zoom')
        self.assertNotIn(row['review_status'],('approved','excluded'))
        self.assertEqual(repair_automatic_parser_exclusions(self.conn),0)

    def test_unknown_parser_result_keeps_promoted_session_and_participants(self):
        from jordana_invoice.importer import maybe_exclude_pending_session
        self.load(event('a','Alex Example 2'))
        cid=self.candidate()['id'];raw=self.conn.execute('select * from raw_calendar_snapshots').fetchone()
        before=[tuple(r) for r in self.conn.execute('select * from session_participants')]
        result=parse_event({**dict(raw),'event_title':'Alex Example 2 unexpected'})
        self.assertEqual(result.classification,'unresolved')
        maybe_exclude_pending_session(self.conn,cid,raw,result)
        self.assertNotEqual(self.conn.execute('select review_status from sessions').fetchone()[0],'excluded')
        self.assertEqual(before,[tuple(r) for r in self.conn.execute('select * from session_participants')])

    def test_manual_exclusion_is_not_repaired_as_parser_error(self):
        from jordana_invoice.review_services import repair_automatic_parser_exclusions
        from jordana_invoice.util import new_id, now_iso
        self.load(event('a','Alex Example 2 zoom'))
        cid=self.candidate('Alex Example 2 zoom')['id'];sid=self.conn.execute('select id from sessions where candidate_id=?',(cid,)).fetchone()[0]
        mark_candidate(self.conn,cid,classification='nonbillable')
        # Older syncs could reopen the candidate while leaving a human decision in audit.
        self.conn.execute("update calendar_event_candidates set review_status='needs_classification',calendar_review_state='eligible' where id=?",(cid,))
        self.conn.execute("insert into audit_log(id,entity_type,entity_id,action,details,created_at) values (?,'session',?,'excluded_from_latest_calendar_snapshot',?,?)",(new_id(),sid,'{"latest_classification":"unresolved"}',now_iso()))
        self.assertEqual(repair_automatic_parser_exclusions(self.conn),0)
        self.assertEqual(self.conn.execute('select review_status from sessions where id=?',(sid,)).fetchone()[0],'excluded')
