"""Pydantic/ninja schemas for the To-do JWT API (`/api/todo/`)."""

from .attachment import (
    AttachmentSchema,
    FileUrlSchema,
    UploadStartSchema,
    UploadTicketSchema,
)
from .comment import CommentBodySchema, CommentSchema, RevisionSchema
from .filter import (
    FilterCreateSchema,
    FilterOrderSchema,
    FilterParamsSchema,
    FilterSchema,
    FilterUpdateSchema,
)
from .label import (
    LabelCreateSchema,
    LabelOrderSchema,
    LabelRefSchema,
    LabelSchema,
    LabelUpdateSchema,
)
from .parse import ParseAnswerSchema, ParseSchema
from .reminder import (
    DeliveryListSchema,
    DeliverySchema,
    ReminderCreateSchema,
    ReminderSchema,
    ReminderUpdateSchema,
)
from .settings import InboxSchema, SettingsSchema, SettingsUpdateSchema
from .task import (
    ChecklistSchema,
    CountSchema,
    ParsedInfoSchema,
    PostponeSchema,
    RecurrenceSchema,
    ReferencedBySchema,
    ReorderSchema,
    TaskCreatedSchema,
    TaskCreateSchema,
    TaskDetailSchema,
    TaskListSchema,
    TaskSchema,
    TaskUpdateSchema,
    UnpaidBlockSchema,
)

__all__ = [
    'AttachmentSchema',
    'ChecklistSchema',
    'CommentBodySchema',
    'CommentSchema',
    'CountSchema',
    'DeliveryListSchema',
    'DeliverySchema',
    'FileUrlSchema',
    'FilterCreateSchema',
    'FilterOrderSchema',
    'FilterParamsSchema',
    'FilterSchema',
    'FilterUpdateSchema',
    'InboxSchema',
    'LabelCreateSchema',
    'LabelOrderSchema',
    'LabelRefSchema',
    'LabelSchema',
    'LabelUpdateSchema',
    'ParseAnswerSchema',
    'ParseSchema',
    'ParsedInfoSchema',
    'PostponeSchema',
    'RecurrenceSchema',
    'ReferencedBySchema',
    'ReminderCreateSchema',
    'ReminderSchema',
    'ReminderUpdateSchema',
    'ReorderSchema',
    'RevisionSchema',
    'SettingsSchema',
    'SettingsUpdateSchema',
    'TaskCreateSchema',
    'TaskCreatedSchema',
    'TaskDetailSchema',
    'TaskListSchema',
    'TaskSchema',
    'TaskUpdateSchema',
    'UnpaidBlockSchema',
    'UploadStartSchema',
    'UploadTicketSchema',
]
