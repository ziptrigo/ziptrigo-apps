from apps.core.services.mask import mask_email


def test_mask_email_keeps_the_first_character_and_the_domain():
    assert mask_email('jane@example.com') == 'j•••@example.com'
    assert mask_email('a@b.co') == 'a•••@b.co'


def test_mask_email_hides_the_length():
    assert mask_email('averyveryverylongname@x.io') == mask_email('a@x.io')


def test_mask_email_copes_without_an_at_sign():
    assert mask_email('nonsense') == 'n•••'
