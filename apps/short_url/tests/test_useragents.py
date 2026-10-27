import pytest

from ..services.useragents import MAX_UA_LENGTH, parse_user_agent

IPHONE = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
)
IPAD = (
    'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
)
WINDOWS_CHROME = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)
MAC_SAFARI = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.0 Safari/605.1.15'
)
ANDROID_PHONE = (
    'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.6099.43 Mobile Safari/537.36'
)
ANDROID_TABLET = (
    'Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.6099.43 Safari/537.36'
)
LINUX_FIREFOX = 'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0'


IN_APP_BROWSERS = [
    # The DuckDuckGo browser, and the Pinterest, Flipboard and Tumblr apps' own browsers, carry
    # their brand in the agent; they are people, not crawlers.
    'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Version/4.0 Chrome/120.0.6099.43 Mobile DuckDuckGo/5 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.0 Mobile/15E148 DuckDuckGo/7 Safari/605.1.15',
    'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.6099.43 Mobile Safari/537.36 [Pinterest/Android]',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Mobile/15E148 [Pinterest/iOS]',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Mobile/15E148 Flipboard/4.3.20',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Mobile/15E148 Tumblr/iPhone/33.1',
    'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.6099.43 Mobile Safari/537.36 YandexSearch/23.1',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 YaBrowser/23.11.0 Yowser/2.5 Safari/537.36',
]


@pytest.mark.parametrize('header', IN_APP_BROWSERS)
def test_in_app_browsers_with_a_brand_name_are_not_bots(header):
    parsed = parse_user_agent(header)
    assert not parsed.is_bot, header
    assert parsed.device in ('desktop', 'mobile', 'tablet')


@pytest.mark.parametrize(
    ('header', 'device', 'browser', 'os'),
    [
        (IPHONE, 'mobile', 'Safari', 'iOS'),
        (IPAD, 'tablet', 'Safari', 'iOS'),
        (WINDOWS_CHROME, 'desktop', 'Chrome', 'Windows'),
        (MAC_SAFARI, 'desktop', 'Safari', 'Mac OS X'),
        (LINUX_FIREFOX, 'desktop', 'Firefox', 'Linux'),
        (ANDROID_PHONE, 'mobile', 'Chrome', 'Android'),
        (ANDROID_TABLET, 'tablet', 'Chrome', 'Android'),
    ],
)
def test_a_person_gets_a_device_browser_and_os(header, device, browser, os):
    parsed = parse_user_agent(header)
    assert (parsed.device, parsed.browser, parsed.os) == (device, browser, os)
    assert not parsed.is_bot


@pytest.mark.parametrize(
    'header',
    [
        'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        'Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)',
        'Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)',
        'Slack-ImgProxy (+https://api.slack.com/robots)',
        'Twitterbot/1.0',
        'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
        'WhatsApp/2.23.20.0 A',
        'Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)',
        'TelegramBot (like TwitterBot)',
        'LinkedInBot/1.0 (compatible; Mozilla/5.0)',
        'SkypeUriPreview Preview/0.5',
        'Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Google-Safety',
        'Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/120.0.0.0 Safari/537.36',
        'python-requests/2.31.0',
        'curl/8.1.2',
        'Wget/1.21',
        'Go-http-client/2.0',
        'Java/17.0.1',
        'Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)',
        'Mozilla/5.0 (compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)',
        'Mozilla/5.0 (compatible; DuckDuckBot-Https/1.1; https://duckduckgo.com/duckduckbot)',
        'DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)',
        'Pinterest/0.2 (+http://www.pinterest.com/bot.html)',
        'Pinterestbot/1.0 (+http://www.pinterest.com/bot.html)',
        'FlipboardProxy/1.2 (+http://flipboard.com/browserproxy)',
        'FlipboardRSS/1.2 (+http://flipboard.com/browserproxy)',
        'Tumblr/14.0.835.186',
        'Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)',
        'Mozilla/5.0 (compatible; YandexImages/3.0; +http://yandex.com/bots)',
    ],
)
def test_crawlers_and_link_preview_fetchers_are_bots(header):
    parsed = parse_user_agent(header)
    assert parsed.is_bot, header
    assert parsed.device == 'bot'


@pytest.mark.parametrize('header', [None, '', '   '])
def test_a_missing_user_agent_is_a_bot(header):
    parsed = parse_user_agent(header)
    assert parsed.is_bot and parsed.device == 'bot'


def test_a_phone_brand_that_ends_in_bot_is_still_a_person():
    header = (
        'Mozilla/5.0 (Linux; Android 11; CUBOT X30) AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Mobile Safari/537.36'
    )
    assert not parse_user_agent(header).is_bot


def test_a_huge_header_is_cut_before_parsing():
    parsed = parse_user_agent(WINDOWS_CHROME + ' ' * 10 + 'x' * (MAX_UA_LENGTH * 4))
    assert parsed.browser  # parsed without trouble, from its first MAX_UA_LENGTH characters
    assert not parsed.is_bot


def test_unknown_agents_are_other():
    parsed = parse_user_agent('SomeUnheardOfBrowser/1.0')
    assert parsed.browser and parsed.os
    assert parsed.device == 'desktop'
