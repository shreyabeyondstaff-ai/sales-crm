from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class LeadIn(BaseModel):
    first_name: str
    last_name: str = ""
    title: str = ""
    company: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    website: str = ""
    location: str = ""
    country: str = ""
    source: str = "Manual"
    source_detail: str = ""
    owner_id: Optional[str] = None
    campaign_id: Optional[str] = None
    tags: List[str] = []

class LeadPatch(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    campaign_id: Optional[str] = None
    tags: Optional[List[str]] = None
    verified_fields: Optional[List[str]] = None

class StageChange(BaseModel):
    stage: str
    reason: str = ""

class BulkAction(BaseModel):
    ids: List[str]
    action: str            # assign | stage | archive
    value: str = ""
    reason: str = ""

class ActivityIn(BaseModel):
    lead_id: str
    type: str = "phone_call"
    outcome: str
    notes: str = ""
    occurred_at: Optional[str] = None
    follow_up_due: Optional[str] = None
    next_action: str = ""
    duration_min: Optional[int] = None
    external_ref: str = ""

class TaskIn(BaseModel):
    lead_id: str
    title: str
    type: str = "follow_up"
    due_at: str
    priority: str = "medium"
    owner_id: Optional[str] = None
    notes: str = ""

class TaskPatch(BaseModel):
    due_at: Optional[str] = None
    owner_id: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[str] = None

class ImportOptions(BaseModel):
    mapping: Dict[str, str]          # source column -> CRM field
    policy: str = "skip"             # skip | update | separate
    source: str = "File Upload"
    campaign_id: Optional[str] = None
    confirm_separate: bool = False

class ApolloSearch(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    keywords: str = ""
    page: int = 1

class ApolloImport(BaseModel):
    records: List[Dict[str, Any]]
    policy: str = "skip"
    campaign_id: Optional[str] = None

class EnrichIn(BaseModel):
    lead_ids: List[str]
    confirm: bool = False

class OppIn(BaseModel):
    lead_id: str
    name: str
    value: float = 0
    stage: str = "Discovery"

class OppPatch(BaseModel):
    stage: Optional[str] = None
    value: Optional[float] = None
    name: Optional[str] = None

class ChatIn(BaseModel):
    message: str
    lead_id: Optional[str] = None

class SettingsIn(BaseModel):
    stages: Optional[List[str]] = None
    outcomes: Optional[List[str]] = None
    stale_days: Optional[int] = None
    require_lost_reason: Optional[bool] = None
    pitch: Optional[str] = None
    goals: Optional[Dict[str, int]] = None

class UserPatch(BaseModel):
    role: Optional[str] = None
    active: Optional[bool] = None

class SuggestionEdit(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    due_at: Optional[str] = None

class DismissIn(BaseModel):
    reason: str = ""

class CampaignIn(BaseModel):
    name: str
    channel: str = "call"
    color: Optional[str] = None

class CampaignPatch(BaseModel):
    name: Optional[str] = None
    channel: Optional[str] = None
    color: Optional[str] = None
    archived: Optional[bool] = None
