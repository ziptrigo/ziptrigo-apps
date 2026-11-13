from apps.core.redirects import redirect_to_page


def test_appends_the_query():
    response = redirect_to_page('/todo/all/', 'sort=title&page=2')
    assert response.status_code == 302
    assert response['Location'] == '/todo/all/?sort=title&page=2'


def test_no_query_is_the_bare_path():
    assert redirect_to_page('/todo/all/')['Location'] == '/todo/all/'


def test_an_offsite_target_goes_home():
    assert redirect_to_page('//evil.example/x', 'a=1')['Location'] == '/'
    assert redirect_to_page('/\\evil.example')['Location'] == '/'
