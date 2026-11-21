"""`{% load filenames %}`: a file name's stem and extension apart, for a rename box that edits the
name and shows the extension it keeps (`apps.core.services.filenames`)."""

from django import template

from apps.core.services.filenames import split_extension

register = template.Library()


@register.filter
def file_stem(name: str) -> str:
    """`report` for `report.pdf`."""
    return split_extension(str(name or ''))[0]


@register.filter
def file_extension(name: str) -> str:
    """`.pdf` for `report.pdf`, `''` for a name without one."""
    return split_extension(str(name or ''))[1]
