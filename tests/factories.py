import factory
from factory import Faker
from factory.django import DjangoModelFactory


class UserFactory(DjangoModelFactory):
    class Meta:
        model = 'user.User'

    email = factory.Sequence(lambda n: f'user{n}@example.com')
    name = Faker('name')
    status = 'ACTIVE'
    is_staff = False
    is_superuser = False

    @factory.post_generation
    def password(self, create: bool, extracted: str | None, **kwargs):
        if not create:
            return
        raw_password = extracted or 'password123'
        self.set_password(raw_password)  # type: ignore
        self.save(update_fields=['password'])  # type: ignore
