"""`GET /api/notes/settings`: what notes cost and how many free slots the caller has used."""

from .. import services
from ..models import NotesSettings
from ..schemas import NoteSettingsSchema
from .router import router


@router.get('/settings', response=NoteSettingsSchema)
def get_settings(request):
    """The prices and limits that apply to the caller, and what the next note would cost
    (`next_note_cost`: 0 while a free slot is open) -- the number to send as `expected_cost`."""
    user = request.auth
    config = NotesSettings.load()
    return {
        'free_notes': config.free_notes,
        'free_notes_used': services.free_slots_used(user),
        'creation_price': config.creation_price,
        'renewal_price': config.renewal_price,
        'renewal_days': config.renewal_days,
        'next_note_cost': services.quote_create(user),
        'trash_days': config.trash_days,
    }
