"""Unit tests for `admin/api.py` (`inv api generate`): the version rules and what gets written.
Like the other CLI tests this module never touches Django: `build_spec` is replaced, and both
files live in a temporary directory. That the committed `docs/openapi.yml` matches the backend is
checked by `apps/core/tests/test_openapi_doc.py`.
"""

import pytest
import yaml
from typer.testing import CliRunner

from admin import api
from admin.api import Bump

pytestmark = pytest.mark.unit

runner = CliRunner()


def spec(description='One endpoint.', version='1.0.0'):
    return {
        'openapi': '3.1.0',
        'info': {'title': 'ZipTrigo API', 'version': version, 'description': 'API.'},
        'paths': {'/api/x': {'get': {'summary': 'X', 'description': description}}},
    }


class TestBumpVersion:
    @pytest.mark.parametrize(
        ('part', 'expected'),
        [(Bump.PATCH, '1.2.4'), (Bump.MINOR, '1.3.0'), (Bump.MAJOR, '2.0.0')],
    )
    def test_raises_one_part_and_resets_the_rest(self, part, expected):
        assert api.bump_version('1.2.3', part) == expected

    @pytest.mark.parametrize('version', ['1.2', 'v1.2.3', '1.2.3-beta', ''])
    def test_refuses_a_version_that_is_not_major_minor_patch(self, version):
        with pytest.raises(ValueError, match='major.minor.patch'):
            api.bump_version(version, Bump.MINOR)


class TestStripVersion:
    def test_drops_only_the_version_and_leaves_the_original_alone(self):
        original = spec(version='3.1.4')

        stripped = api.strip_version(original)

        assert 'version' not in stripped['info']
        assert stripped['info']['title'] == 'ZipTrigo API'
        assert original['info']['version'] == '3.1.4'

    def test_specs_differing_only_in_version_compare_equal(self):
        assert api.strip_version(spec(version='1.0.0')) == api.strip_version(spec(version='9.9.9'))


class TestPlanGeneration:
    def test_first_file_keeps_the_current_version(self):
        assert api.plan_generation(spec(), None, '1.0.0', Bump.MINOR) == ('1.0.0', True)

    def test_unchanged_spec_writes_nothing(self):
        committed = spec(version='1.4.0')

        assert api.plan_generation(spec(), committed, '1.4.0', Bump.MAJOR) == ('1.4.0', False)

    @pytest.mark.parametrize(
        ('part', 'expected'),
        [(Bump.PATCH, '1.4.1'), (Bump.MINOR, '1.5.0'), (Bump.MAJOR, '2.0.0')],
    )
    def test_changed_spec_bumps(self, part, expected):
        committed = spec(description='Old.', version='1.4.0')

        assert api.plan_generation(spec(), committed, '1.4.0', part) == (expected, True)

    def test_unchanged_spec_with_a_stale_version_is_rewritten_without_a_bump(self):
        committed = spec(version='1.3.0')

        assert api.plan_generation(spec(), committed, '1.4.0', Bump.MINOR) == ('1.4.0', True)


class TestVersionConstant:
    def test_reads_and_rewrites_only_the_version_line(self, tmp_path):
        path = tmp_path / 'api_version.py'
        path.write_text('"""Doc: API_VERSION is here."""\n\nAPI_VERSION = \'1.0.0\'\n')

        api.write_version_constant(path, '1.1.0')

        assert api.read_version_constant(path) == '1.1.0'
        assert path.read_text() == '"""Doc: API_VERSION is here."""\n\nAPI_VERSION = \'1.1.0\'\n'

    @pytest.mark.parametrize(
        'text', ['VERSION = 1\n', "API_VERSION = '1.0.0'\nAPI_VERSION = '2.0.0'\n"]
    )
    def test_refuses_a_file_without_exactly_one_version_line(self, tmp_path, text):
        path = tmp_path / 'api_version.py'
        path.write_text(text)

        with pytest.raises(ValueError, match='API_VERSION'):
            api.write_version_constant(path, '1.1.0')
        assert path.read_text() == text

    def test_the_real_file_has_a_version(self):
        api.bump_version(api.read_version_constant(api.VERSION_PATH), Bump.PATCH)


class TestRenderYaml:
    def test_round_trips_and_starts_with_the_header(self):
        data = spec(description='Ünïcode and a long line ' + 'x' * 300)

        text = api.render_yaml(data)

        assert text.startswith(api.HEADER)
        assert yaml.safe_load(text) == data
        assert 'Ünïcode' in text
        # Long values are not wrapped, so a change is a one-line diff.
        assert any(line.endswith('x' * 300) for line in text.splitlines())

    def test_keeps_the_key_order(self):
        text = api.render_yaml(spec())

        assert text.index('openapi:') < text.index('info:') < text.index('paths:')


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Both files in a temporary directory, the backend's spec under the test's control."""
    spec_path = tmp_path / 'docs' / 'openapi.yml'
    version_path = tmp_path / 'api_version.py'
    version_path.write_text("API_VERSION = '1.0.0'\n")
    monkeypatch.setattr(api, 'SPEC_PATH', spec_path)
    monkeypatch.setattr(api, 'VERSION_PATH', version_path)
    backend = {'spec': spec()}
    monkeypatch.setattr(api, 'build_spec', lambda: backend['spec'])

    class Files:
        def set_backend(self, new_spec):
            backend['spec'] = new_spec

        @property
        def written(self):
            return yaml.safe_load(spec_path.read_text())

        @property
        def version(self):
            return api.read_version_constant(version_path)

        spec_file = spec_path

    return Files()


def invoke(*args):
    # A Typer app with one command collapses into it, so no `generate` here (`inv` keeps it).
    return runner.invoke(api.app, list(args))


class TestGenerateCommand:
    def test_first_run_writes_the_file_at_the_current_version(self, files):
        result = invoke()

        assert result.exit_code == 0, result.output
        assert files.written == spec(version='1.0.0')
        assert files.spec_file.read_text().startswith(api.HEADER)
        assert files.version == '1.0.0'

    def test_unchanged_spec_touches_nothing(self, files):
        invoke()
        before = files.spec_file.stat().st_mtime_ns

        result = invoke()

        assert result.exit_code == 0, result.output
        assert files.spec_file.stat().st_mtime_ns == before
        assert files.version == '1.0.0'

    def test_changed_spec_bumps_minor_by_default(self, files):
        invoke()
        files.set_backend(spec(description='Changed.'))

        result = invoke()

        assert result.exit_code == 0, result.output
        assert files.version == '1.1.0'
        assert files.written == spec(description='Changed.', version='1.1.0')

    def test_bump_option(self, files):
        invoke()
        files.set_backend(spec(description='Breaking.'))

        result = invoke('--bump', 'major')

        assert result.exit_code == 0, result.output
        assert files.version == '2.0.0'
        assert files.written['info']['version'] == '2.0.0'

    def test_check_passes_when_current(self, files):
        invoke()

        result = invoke('--check')

        assert result.exit_code == 0, result.output

    def test_check_fails_without_writing_when_the_spec_changed(self, files):
        invoke()
        files.set_backend(spec(description='Changed.'))

        result = invoke('--check')

        assert result.exit_code == 1
        assert files.version == '1.0.0'
        assert files.written == spec(version='1.0.0')

    def test_check_fails_when_there_is_no_file(self, files):
        result = invoke('--check')

        assert result.exit_code == 1
        assert not files.spec_file.exists()

    def test_check_fails_when_the_file_version_disagrees_with_the_constant(self, files):
        invoke()
        api.write_version_constant(api.VERSION_PATH, '1.2.0')

        result = invoke('--check')

        assert result.exit_code == 1
