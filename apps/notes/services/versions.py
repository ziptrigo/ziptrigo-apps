"""A note's version history: recording a save, and listing and finding versions."""

from dataclasses import dataclass

from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import User

from ..limits import VERSION_COALESCE
from ..models import EditorKind, Note, NotesSettings, NoteVersion, ShareLink


@dataclass(frozen=True)
class Editor:
    """Who is changing a note: `kind` is `owner` when the acting user is the note's owner, `user`
    for another signed-in user and `anonymous` otherwise (an editable share link). `link` is the
    share link used, if any."""

    kind: str
    user: User | None = None
    link: ShareLink | None = None

    @classmethod
    def for_user(cls, note: Note, user: User | None, link: ShareLink | None = None) -> 'Editor':
        if user is None or not getattr(user, 'is_authenticated', False):
            return cls(EditorKind.ANONYMOUS, None, link)
        kind = EditorKind.OWNER if user.pk == note.user_id else EditorKind.USER
        return cls(kind, user, link)


def record(
    note: Note,
    editor: Editor,
    *,
    coalesce: bool = True,
    before: tuple[str, str] | None = None,
) -> NoteVersion:
    """Record the state `note` is in now (the caller has just saved it, at `note.version`) as a
    version made by `editor`.

    If the newest version has the same editor kind, account **and share link** and was last written
    within `VERSION_COALESCE`, it is overwritten in place (title, content and time): a burst of saves
    or ticked boxes doesn't use up the cap, while the state before another person's editing
    session always survives. Otherwise a row is inserted with the next number and the oldest rows
    beyond `max_versions_per_note` are deleted. `coalesce=False` always inserts (a restore of an earlier
    version, or "Save mine anyway" over someone's newer text, must keep the state it replaced).

    `before` is the `(title, content)` the note held right before this write. A version is
    overwritten **only if it still holds exactly that**: then nothing but this editor's own last
    save is lost. If the newest row differs (the note changed some other way) the state it holds
    must survive, so a row is inserted; without `before` nothing is coalesced. Call inside the
    note's transaction."""
    now = timezone.now()
    editor_id = editor.user.pk if editor.user is not None else None
    link_id = editor.link.pk if editor.link is not None else None
    newest = NoteVersion.objects.filter(note=note).order_by('-number').first()
    if (
        coalesce
        and before is not None
        and newest is not None
        and (newest.title, newest.content) == before
        and newest.editor_kind == editor.kind
        and newest.editor_id == editor_id
        and newest.link_id == link_id
        and now - newest.updated_at <= VERSION_COALESCE
    ):
        newest.title = note.title
        newest.content = note.content
        newest.updated_at = now
        newest.save(update_fields=['title', 'content', 'updated_at'])
        return newest
    version = NoteVersion.objects.create(
        note=note,
        number=(newest.number + 1) if newest is not None else 1,
        title=note.title,
        content=note.content,
        editor=editor.user,
        editor_kind=editor.kind,
        link=editor.link,
        updated_at=now,
    )
    keep = NotesSettings.load().max_versions_per_note
    surplus = list(
        NoteVersion.objects.filter(note=note)
        .order_by('-number')
        .values_list('pk', flat=True)[keep:]
    )
    if surplus:
        NoteVersion.objects.filter(pk__in=surplus).delete()
    return version


def list_versions(note: Note) -> QuerySet:
    """The note's versions, newest first, with their editors loaded."""
    return NoteVersion.objects.filter(note=note).select_related('editor').order_by('-number')
