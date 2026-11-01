"""File transfer's send page: a network error during the upload is shown on the file's row.

It used to be a blocking `alert()` with the row saying only "Paused" (#224). The PUT of the part
is aborted the way a dropped connection does, then released for the resume. Runs on the server of
`receipts_server`, where file transfer's S3 is an in-memory fake.
"""

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer


def test_a_network_error_is_shown_on_the_row_and_the_file_can_be_resumed(
    page: Page, receipts_server: ReceiptsServer, tmp_path
):
    dialogs = []
    page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.route('**/__e2e__/ft-s3/**', lambda route: route.abort())
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/transfer/send/')

    path = tmp_path / 'report.txt'
    path.write_text('quarterly numbers')
    page.set_input_files('#file-input', str(path))

    row = page.locator('#file-list li')
    message = row.get_by_role('alert')
    expect(message).to_contain_text('interrupted by a network error')
    expect(message).to_contain_text('Select the same file again to resume')
    expect(row).to_contain_text('Paused')
    expect(message.get_by_role('button', name='Choose the file')).to_be_visible()
    assert dialogs == []

    # Same file again, with the network back: the row resumes and the message goes away.
    page.unroute('**/__e2e__/ft-s3/**')
    page.set_input_files('#file-input', str(path))
    expect(row.get_by_role('alert')).to_have_count(0)
    expect(row.locator('.fa-check')).to_be_visible()
