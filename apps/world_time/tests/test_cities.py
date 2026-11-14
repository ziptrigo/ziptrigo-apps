"""The city file, seeding the tables from it, and the search over them."""

from zoneinfo import available_timezones

import pytest
from django.core.management import call_command

from ..models import City, CityData, CityName, Location
from ..services import cities, search
from ..services.cities import DATA_FILE, read_file, search_cities, seed
from .factories import make_city, make_location, make_tab, write_city_file

pytestmark = pytest.mark.django_db

ROWS = [
    (1, 'Lisbon', 'PT', 'Portugal', '', 'Europe/Lisbon', 517802, ['Lisboa', 'Lissabon']),
    (2, 'Porto', 'PT', 'Portugal', '', 'Europe/Lisbon', 252687, ['Oporto']),
    (3, 'São Paulo', 'BR', 'Brazil', 'São Paulo', 'America/Sao_Paulo', 12400232, []),
]


class TestTheRepositoryFile:
    def test_every_line_is_a_city_with_a_known_zone(self):
        rows = list(read_file(DATA_FILE))
        zones = available_timezones()
        assert len(rows) > 30_000
        assert all(row.timezone in zones for row in rows)
        assert all(row.name and row.country_code for row in rows)
        ids = [row.geoname_id for row in rows]
        assert ids == sorted(set(ids)), 'ids are unique and the file is sorted by them'

    def test_names_fit_the_columns(self):
        for row in read_file(DATA_FILE):
            assert len(row.name) <= 200 and len(row.country) <= 100 and len(row.region) <= 100
            assert all(len(name) <= 200 for name in row.other_names)

    def test_it_knows_cities_by_their_local_names(self):
        by_name = {row.name: row for row in read_file(DATA_FILE) if row.population > 400_000}
        assert 'Roma' in by_name['Rome'].other_names
        assert 'New York City' in by_name
        assert 'Lisboa' in by_name['Lisbon'].other_names
        assert 'München' in by_name['Munich'].other_names
        assert by_name['Lisbon'].timezone == 'Europe/Lisbon'

    def test_sections_of_cities_are_left_out(self):
        # GeoNames lists districts (feature code PPLX) that answer to their city's names.
        names = {row.name for row in read_file(DATA_FILE)}
        assert not any(name.startswith('Circoscrizione') for name in names)
        assert 'Turin' in names

    def test_a_malformed_line_is_refused(self, tmp_path):
        import gzip

        path = tmp_path / 'bad.tsv.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as handle:
            handle.write('1\tLisbon\tPT\n')
        with pytest.raises(ValueError, match='line 1'):
            list(read_file(path))


class TestSeed:
    def test_it_fills_the_tables(self, tmp_path):
        result = seed(write_city_file(tmp_path / 'c.tsv.gz', ROWS))
        assert (result.changed, result.cities, result.names) == (True, 3, 3)
        lisbon = City.objects.get(pk=1)
        assert (lisbon.name, lisbon.country, lisbon.timezone) == (
            'Lisbon',
            'Portugal',
            'Europe/Lisbon',
        )
        assert City.objects.get(pk=3).search_name == 'sao paulo'
        assert set(CityName.objects.filter(city=lisbon).values_list('name', flat=True)) == {
            'Lisboa',
            'Lissabon',
        }
        state = CityData.objects.get(pk=1)
        assert (state.city_count, state.name_count) == (3, 3)
        assert state.sha256 == cities.file_sha256(tmp_path / 'c.tsv.gz')

    def test_a_second_run_with_the_same_file_does_nothing(
        self, tmp_path, django_assert_num_queries
    ):
        path = write_city_file(tmp_path / 'c.tsv.gz', ROWS)
        seed(path)
        seeded_at = CityData.objects.get(pk=1).seeded_at
        with django_assert_num_queries(1):
            result = seed(path)
        assert (result.changed, result.cities) == (False, 3)
        assert CityData.objects.get(pk=1).seeded_at == seeded_at

    def test_a_changed_file_updates_adds_and_removes(self, tmp_path):
        seed(write_city_file(tmp_path / 'c.tsv.gz', ROWS))
        changed = [
            (1, 'Lisbon', 'PT', 'Portugal', 'Lisboa', 'Europe/Lisbon', 600000, ['Lisboa']),
            (3, 'São Paulo', 'BR', 'Brazil', 'São Paulo', 'America/Sao_Paulo', 12400232, []),
            (4, 'Faro', 'PT', 'Portugal', '', 'Europe/Lisbon', 41000, []),
        ]
        result = seed(write_city_file(tmp_path / 'c.tsv.gz', changed))
        assert (result.changed, result.cities, result.names) == (True, 3, 1)
        assert set(City.objects.values_list('pk', flat=True)) == {1, 3, 4}
        assert City.objects.get(pk=1).population == 600000
        assert list(CityName.objects.values_list('name', flat=True)) == ['Lisboa']

    def test_force_seeds_an_unchanged_file_again(self, tmp_path):
        path = write_city_file(tmp_path / 'c.tsv.gz', ROWS)
        seed(path)
        City.objects.filter(pk=2).delete()
        assert seed(path).changed is False
        assert seed(path, force=True).changed is True
        assert City.objects.filter(pk=2).exists()

    def test_a_users_locations_survive_a_city_leaving_the_file(self, tmp_path, user):
        seed(write_city_file(tmp_path / 'c.tsv.gz', ROWS))
        location = make_location(make_tab(user), 'Porto', geoname_id=2)
        seed(write_city_file(tmp_path / 'c.tsv.gz', ROWS[:1]))
        assert not City.objects.filter(pk=2).exists()
        assert Location.objects.get(pk=location.pk).name == 'Porto'

    def test_a_city_whose_zone_this_system_lacks_is_left_out(self, tmp_path, caplog):
        rows = [*ROWS, (9, 'Olympus', 'XX', 'Mars', '', 'Mars/Olympus', 5, ['Mons'])]
        with caplog.at_level('WARNING'):
            result = seed(write_city_file(tmp_path / 'c.tsv.gz', rows))
        assert (result.cities, result.names) == (3, 3)
        assert not City.objects.filter(pk=9).exists()
        assert 'Skipped 1 cities whose time zone this system does not know' in caplog.text

    def test_is_seeded(self, tmp_path):
        assert cities.is_seeded() is False
        seed(write_city_file(tmp_path / 'c.tsv.gz', ROWS))
        assert cities.is_seeded() is True

    def test_the_command(self, tmp_path, capsys):
        path = write_city_file(tmp_path / 'c.tsv.gz', ROWS)
        call_command('world_time_seed_cities', file=path)
        assert 'Seeded 3 cities and 3 other names.' in capsys.readouterr().out
        call_command('world_time_seed_cities', file=path)
        assert 'City data is up to date (3 cities).' in capsys.readouterr().out
        call_command('world_time_seed_cities', file=path, force=True)
        assert 'Seeded 3 cities' in capsys.readouterr().out


class TestSearchCities:
    @pytest.fixture(autouse=True)
    def _cities(self):
        make_city(1, 'Lisbon', population=517802, other_names=['Lisboa'])
        make_city(2, 'Porto', population=252687, other_names=['Oporto'])
        make_city(3, 'São Paulo', 'America/Sao_Paulo', population=12400232, country='Brazil')
        make_city(4, 'Portland', 'America/Los_Angeles', population=652503, region='Oregon')
        make_city(5, 'Portland', 'America/New_York', population=68408, region='Maine')
        make_city(6, 'Port Elizabeth', 'Africa/Johannesburg', population=967677)

    def names(self, query, limit=8):
        return [match.city.name for match in search_cities(query, limit)]

    def test_a_prefix_finds_cities_most_populous_first(self):
        assert self.names('port') == ['Port Elizabeth', 'Portland', 'Porto', 'Portland']

    def test_accents_and_case_are_ignored(self):
        assert self.names('SAO PAU') == ['São Paulo']
        assert self.names('são') == ['São Paulo']

    def test_a_later_word_matches_too(self):
        assert self.names('paulo') == ['São Paulo']
        assert self.names('elizabeth') == ['Port Elizabeth']

    def test_another_name_finds_the_city_and_says_which(self):
        (match,) = search_cities('lisboa', 8)
        assert (match.city.name, match.matched_name) == ('Lisbon', 'Lisboa')
        assert search_cities('lisb', 8)[0].matched_name == ''

    def test_one_letter_and_nothing_find_nothing(self, django_assert_num_queries):
        with django_assert_num_queries(0):
            assert self.names('p') == [] and self.names('  ') == [] and self.names('!') == []

    def test_a_big_city_by_another_name_is_not_hidden_by_small_ones(self):
        # More own-name matches than the limit, all smaller than the city known as "Roma".
        for index in range(10):
            make_city(100 + index, f'Roman{index}', population=1000 + index)
        make_city(200, 'Rome', 'Europe/Rome', population=2318895, other_names=['Roma'])
        matches = search_cities('roma', 8)
        assert (matches[0].city.name, matches[0].matched_name) == ('Rome', 'Roma')
        assert len(matches) == 8

    def test_of_two_spellings_the_one_typed_is_shown(self):
        make_city(400, 'Rome', 'Europe/Rome', population=2318895, other_names=['Roma', 'Róma'])
        assert search_cities('Róma', 8)[0].matched_name == 'Róma'
        assert search_cities('roma', 8)[0].matched_name == 'Roma'

    def test_a_whole_name_comes_before_bigger_cities_that_only_start_with_it(self):
        make_city(300, 'Portobello', population=9_000_000)
        assert self.names('porto')[:2] == ['Porto', 'Portobello']

    def test_it_is_two_queries(self, django_assert_num_queries):
        with django_assert_num_queries(2):
            search_cities('port', 8)

    def test_the_limit(self):
        assert len(self.names('port', limit=2)) == 2

    def test_like_wildcards_are_literal(self):
        assert self.names('%') == [] and self.names('po_to') == []


class TestSearch:
    def test_cities_then_zones(self):
        make_city(1, 'Lisbon', population=517802, region='', country='Portugal')
        results = search('lisb')
        assert [(r.kind, r.value, r.name, r.detail) for r in results] == [
            ('city', '1', 'Lisbon', 'Portugal'),
            ('zone', 'Europe/Lisbon', 'Lisbon', 'Europe/Lisbon'),
        ]

    def test_an_abbreviation_comes_first(self):
        make_city(1, 'Pstovo', population=10)
        results = search('pst')
        assert (results[0].kind, results[0].value) == ('zone', 'America/Los_Angeles')
        assert results[0].detail == 'Pacific Time (PST)'
        assert results[1].name == 'Pstovo'

    def test_the_other_name_is_the_hint(self):
        make_city(1, 'Lisbon', other_names=['Lisboa'])
        assert search('lisboa')[0].hint == 'also Lisboa'

    def test_empty(self):
        assert search('') == [] and search('   ') == []
