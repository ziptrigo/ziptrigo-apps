from django.contrib import admin
from django.contrib.auth.models import Group

from .models import User

# Use custom user model only; hide Django's built-in Group model (not used)
admin.site.unregister(Group)
admin.site.register(User)
