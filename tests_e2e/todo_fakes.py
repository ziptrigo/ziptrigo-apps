"""The To-do AI the e2e server uses: the unit tests' `FakeTitleParser` with one scripted reply.

The constants are imported by the test module, which has no configured Django; everything that
touches models is imported inside `install_title_parser` (called from `receipts_e2e.E2EConfig.
ready`, when the apps are loaded)."""

#: The one title the fake understands; every other title has no date.
DATED_TITLE = 'pay rent every month on the 1st at 9am'
RULE = 'FREQ=MONTHLY;BYMONTHDAY=1'


def install_title_parser():
    from datetime import time

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

    fake = FakeTitleParser(replies={DATED_TITLE: rent})
    parsing.set_client(fake)
    return fake
