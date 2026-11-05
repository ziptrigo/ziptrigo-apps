"""The To-do AI the e2e server uses: the unit tests' `FakeTitleParser` with three scripted replies.

The constants are imported by the test module, which has no configured Django; everything that
touches models is imported inside `install_title_parser` (called from `receipts_e2e.E2EConfig.
ready`, when the apps are loaded)."""

#: The three titles the fake understands; every other title has no date. The first carries a date, a
#: time and a repeat, the second only a date, the third a numeric date whose reading depends on the
#: order the user writes dates in (issue #245): `5/11` is May 11 month first and 5 November day first,
#: both written `05/11` in the user's format, in the year after the current one so no year is left out.
DATED_TITLE = 'pay rent every month on the 1st at 9am'
DATE_ONLY_TITLE = 'call the bank tomorrow'
NUMERIC_TITLE = 'pay rent 5/11'
RULE = 'FREQ=MONTHLY;BYMONTHDAY=1'


def install_title_parser():
    from datetime import date, time, timedelta

    from apps.todo.services import parsing
    from apps.todo.services.recurrence import first_date
    from apps.todo.tests.fakes import FakeTitleParser

    def rent(request: parsing.ParseRequest) -> parsing.ParsedTitle:
        return parsing.ParsedTitle(
            has_date=True,
            clean_title='pay rent',
            due_date=first_date(RULE, request.today),
            due_time=time(9, 0),
            rrule=RULE,
            matched_text='every month on the 1st at 9am',
            confidence=0.95,
        )

    def bank(request: parsing.ParseRequest) -> parsing.ParsedTitle:
        return parsing.ParsedTitle(
            has_date=True,
            clean_title='call the bank',
            due_date=request.today + timedelta(days=1),
            matched_text='tomorrow',
            confidence=0.9,
        )

    def numeric(request: parsing.ParseRequest) -> parsing.ParsedTitle:
        month, day = (5, 11) if request.date_order == 'mdy' else (11, 5)
        return parsing.ParsedTitle(
            has_date=True,
            clean_title='pay rent',
            due_date=date(request.today.year + 1, month, day),
            matched_text='5/11',
            confidence=0.9,
        )

    fake = FakeTitleParser(
        replies={DATED_TITLE: rent, DATE_ONLY_TITLE: bank, NUMERIC_TITLE: numeric}
    )
    parsing.set_client(fake)
    return fake
