"""`ScheduledJobAdmin`: each job's page says what the job does (its `JobSpec.description`)."""

import pytest
from django.apps import apps as django_apps
from django.urls import reverse

from apps.core.models import ScheduledJob
from apps.core.scheduler import clear_registry, get_jobs

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture(autouse=True)
def real_registry():
    """The jobs every app registers in production. Other tests clear the registry, so rebuild it
    from each app's `_register_jobs()` rather than trusting what `ready()` left there."""
    clear_registry()
    for config in django_apps.get_app_configs():
        register_jobs = getattr(config, '_register_jobs', None)
        if register_jobs is not None:
            register_jobs()
    yield
    clear_registry()


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


def change_url(name: str) -> str:
    return reverse('custom_admin:core_scheduledjob_change', args=[name])


def test_every_registered_job_has_a_description():
    jobs = get_jobs()
    assert jobs
    assert [job.name for job in jobs if not job.description.strip()] == []


def test_change_page_shows_the_description(admin_client):
    ScheduledJob.objects.create(name='billing.release_promo_reservations', interval_seconds=86400)

    response = admin_client.get(change_url('billing.release_promo_reservations'))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-job-description' in content
    assert 'Gives back the uses of promo codes reserved at sign-up' in content


def test_change_page_of_a_job_no_longer_registered(admin_client):
    ScheduledJob.objects.create(name='old.removed_job', interval_seconds=60)

    response = admin_client.get(change_url('old.removed_job'))

    assert response.status_code == 200
    assert 'This job is no longer registered' in response.content.decode()
