"""The app's registration (product, costs, rate limits, deploy step) and the admin."""

from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.products import get_products

from ..models import Tab, WorldTimeSettings
from ..services import list_tabs
from ..services.costs import cost_section
from .factories import make_city, make_location, make_tab, make_user

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[3]


class TestWiring:
    def test_the_product_is_registered(self):
        product = {p.label: p for p in get_products()}['world_time']
        assert (product.name, product.url_name, product.icon) == (
            'World Time',
            'world_time:home',
            'fas fa-earth-americas',
        )
        assert reverse(product.url_name) == '/time/'

    def test_the_landing_page_and_nav_link_to_it(self, web):
        assert reverse('world_time:home') in web.get('/').content.decode()

    def test_it_is_free_on_the_costs_page(self, user, web):
        section = cost_section(user)
        assert [(line.action, line.cost, line.free) for line in section.lines] == [
            ('Tabs, locations and the time grid', 'Free', True)
        ]
        body = web.get(reverse('billing:costs')).content.decode()
        assert 'World Time' in body and 'World Time is free.' in body

    def test_the_rate_limit_rules_exist(self, settings):
        assert {'WT_EDIT_USER', 'WT_SEARCH_USER'} <= set(settings.RATELIMIT_RULES)

    def test_the_container_seeds_the_cities_after_migrating(self):
        entrypoint = (ROOT / 'docker-entrypoint.sh').read_text()
        assert entrypoint.index('manage.py migrate') < entrypoint.index(
            'manage.py world_time_seed_cities'
        )

    def test_the_city_file_ships_in_the_image(self):
        ignored = (ROOT / '.dockerignore').read_text() if (ROOT / '.dockerignore').exists() else ''
        assert 'apps/world_time/data' not in ignored and '*.gz' not in ignored

    def test_the_settings_defaults(self):
        config = WorldTimeSettings.load()
        assert (config.max_tabs_per_user, config.max_locations_per_tab) == (10, 20)
        assert str(config) == 'World Time settings'

    def test_deleting_a_user_takes_their_tabs(self):
        user = make_user()
        tab = list_tabs(user)[0]
        user.delete()
        assert not Tab.objects.filter(pk=tab.pk).exists()


class TestAdmin:
    @pytest.fixture
    def admin_client(self):
        admin = User.objects.create_superuser(email='admin@example.com', password=None)
        client = Client()
        client.force_login(admin)
        return client

    def test_the_lists_and_pages_open(self, admin_client, user):
        tab = make_tab(user, 'Team')
        make_location(tab, 'Lisbon', label='Head office')
        make_city(1, 'Lisbon', population=517802)
        for model in ('tab', 'worldtimeaccount', 'city', 'citydata'):
            response = admin_client.get(f'/admin/world_time/{model}/')
            assert response.status_code == 200, model
        body = admin_client.get(f'/admin/world_time/tab/{tab.pk}/change/').content.decode()
        assert 'Head office' in body and 'Europe/Lisbon' in body
        assert admin_client.get('/admin/world_time/city/?q=lisb').status_code == 200
        assert admin_client.get('/admin/world_time/city/1/change/').status_code == 200

    def test_nothing_is_added_or_deleted_here(self, admin_client, user):
        tab = make_tab(user, 'Team')
        assert admin_client.get('/admin/world_time/tab/add/').status_code == 403
        assert (
            admin_client.post(
                f'/admin/world_time/tab/{tab.pk}/delete/', {'post': 'yes'}
            ).status_code
            == 403
        )
        assert admin_client.get('/admin/world_time/city/add/').status_code == 403

    def test_the_settings_page_saves(self, admin_client):
        response = admin_client.get('/admin/world_time/worldtimesettings/', follow=True)
        assert response.status_code == 200
        response = admin_client.post(
            '/admin/world_time/worldtimesettings/1/change/',
            {'max_tabs_per_user': 4, 'max_locations_per_tab': 9},
        )
        assert response.status_code == 302
        assert WorldTimeSettings.load().max_locations_per_tab == 9

    def test_a_user_with_tabs_can_be_deleted_from_the_admin(self, admin_client):
        user = make_user()
        list_tabs(user)
        response = admin_client.post(f'/admin/accounts/user/{user.pk}/delete/', {'post': 'yes'})
        assert response.status_code == 302
        assert not User.objects.filter(pk=user.pk).exists()
