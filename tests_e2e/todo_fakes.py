"""The To-do AI the e2e server uses: the unit tests' `FakeTitleParser` with two scripted replies.

The constants are imported by the test module, which has no configured Django; everything that
touches models is imported inside `install_title_parser` (called from `receipts_e2e.E2EConfig.
ready`, when the apps are loaded)."""

#: The two titles the fake understands; every other title has no date. The first carries a date, a
#: time and a repeat, the second only a date.
DATED_TITLE = 'pay rent every month on the 1st at 9am'
DATE_ONLY_TITLE = 'call the bank tomorrow'
RULE = 'FREQ=MONTHLY;BYMONTHDAY=1'


def install_title_parser():
    from datetime import time, timedelta

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

    fake = FakeTitleParser(replies={DATED_TITLE: rent, DATE_ONLY_TITLE: bank})
    parsing.set_client(fake)
    return fake
