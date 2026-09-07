import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    url: str
    type: str
    status: str
    polling_schedule: str
    adapter_key: str | None
    adapter_status: str
    note: str | None
    last_polled_at: datetime | None
    availability_status: str | None
    availability_checked_at: datetime | None
    availability_error: str | None
    created_at: datetime
    updated_at: datetime


class SourcePollResultOut(BaseModel):
    source_key: str
    found: int
    created: int
    updated: int
    errors: int


class SourcesPollRequest(BaseModel):
    source_keys: list[str]
