import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, model_validator


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=80)
    category: str = Field(default="", max_length=80)
    handler: str = Field(default="", max_length=80)
    unit: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=4000)
    keywords: str = Field(default="", max_length=500)


class CaseOut(CaseCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    pinned: bool
    archived: bool
    section: str
    notes: str = ""
    created_at: datetime


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None
    section: Literal["默认", "研判中", "待复核", "已归档"] | None = None
    category: str | None = Field(default=None, max_length=80)
    handler: str | None = Field(default=None, max_length=80)
    unit: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=4000)
    keywords: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=20000)


class CaseStats(BaseModel):
    source_files: int
    case_source_files: int = 0
    messages: int
    analyses: int
    reviews: int


class ProjectFileOut(BaseModel):
    path: str
    size: int
    modified_at: datetime


class ProjectFolderOut(BaseModel):
    path: str
    files: list[ProjectFileOut]


class ConversationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationOut(ConversationCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    case_id: uuid.UUID
    pinned: bool
    unread: bool
    archived: bool
    section: str
    analysis_enabled: bool = False
    created_at: datetime


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    unread: bool | None = None
    archived: bool | None = None
    section: Literal["默认", "研判中", "待复核", "已完成"] | None = None
    case_id: uuid.UUID | None = None
    analysis_enabled: bool | None = None


class ConversationFork(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)


class ConversationTurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    question: str
    result: dict
    created_at: datetime


class ImportOut(BaseModel):
    source_file_id: uuid.UUID
    sha256: str
    imported_messages: int
    imported_persons: int = 0
    imported_friends: int = 0
    duplicate: bool = False
    conversation_id: uuid.UUID | None = None
    scope: Literal["case", "conversation"] = "case"


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    filename: str
    sha256: str
    imported_at: datetime
    conversation_id: uuid.UUID | None = None


class SourceOption(BaseModel):
    id: uuid.UUID
    filename: str


class FilterOptions(BaseModel):
    start_date: date | None
    end_date: date | None
    senders: list[str]
    groups: list[str]
    ip_addresses: list[str]
    sources: list[SourceOption]


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    action: str
    target_type: str
    target_id: str
    details: dict
    created_at: datetime


class SearchHit(BaseModel):
    message_id: str
    source_file_id: uuid.UUID
    source_row: int
    sent_at: datetime | None
    group_id: str | None
    sender_id: str | None
    content: str
    ip_address: str | None
    score: float


class ContextMessage(BaseModel):
    message_id: str
    source_file_id: uuid.UUID
    source_row: int
    sent_at: datetime | None
    group_id: str | None
    sender_id: str | None
    content: str
    ip_address: str | None
    relation: Literal["before", "anchor", "after"]
    distance: int


class TimeNeighbor(BaseModel):
    message_id: str
    source_file_id: uuid.UUID
    source_row: int
    sent_at: datetime | None
    group_id: str | None
    sender_id: str | None
    content: str
    ip_address: str | None
    relation: Literal["before", "anchor", "after"]
    seconds_from_anchor: int


class ApiModelConfig(BaseModel):
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = SecretStr("")


class ModelEndpointConfig(BaseModel):
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=200)
    enabled: bool = True


class ApiEndpointConfig(BaseModel):
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    api_key: SecretStr = SecretStr("")


class ApiPresetConfig(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=200)
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    api_key: SecretStr = SecretStr("")


class ModelRuntimeConfig(BaseModel):
    mode: Literal["local", "api"] = "local"
    local: ModelEndpointConfig
    api: ApiEndpointConfig
    api_presets: list[ApiPresetConfig] = Field(default_factory=list, max_length=30)
    temperature: float = Field(default=0.1, ge=0, le=2)
    top_p: float = Field(default=0.9, gt=0, le=1)
    max_tokens: int = Field(default=2048, ge=128, le=32768)


class PromptRuntimeConfig(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    content: str = Field(default="", max_length=20000)
    builtin: bool = False


class RuntimeSettingsIn(BaseModel):
    skills: list[str] = Field(default_factory=list, max_length=100)
    mcp: list[dict] = Field(default_factory=list, max_length=50)
    system_prompts: list[PromptRuntimeConfig] = Field(min_length=1, max_length=50)
    model: ModelRuntimeConfig


class ModelListIn(BaseModel):
    base_url: AnyHttpUrl
    api_key: SecretStr = SecretStr("")


class ChatGroupLabel(BaseModel):
    type: str = Field(default="", max_length=120)
    type_source: Literal["analysis", "manual"] = "analysis"
    hidden: bool = False


class ChatSenderLabel(BaseModel):
    name: str = Field(default="", max_length=80)
    name_source: Literal["analysis", "manual"] = "analysis"


class ChatLabelsIn(BaseModel):
    groups: dict[str, ChatGroupLabel] = Field(default_factory=dict)
    senders: dict[str, ChatSenderLabel] = Field(default_factory=dict)


class AnalysisIn(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    conversation_id: uuid.UUID | None = None
    model_mode: Literal["local", "api"] = "local"
    api_config: ApiModelConfig | None = None
    limit: int = Field(default=10, ge=1, le=50)
    context_before: int = Field(default=2, ge=0, le=10)
    context_after: int = Field(default=2, ge=0, le=10)
    start_date: date | None = None
    end_date: date | None = None
    sender_id: str | None = Field(default=None, max_length=120)
    group_id: str | None = Field(default=None, max_length=120)
    ip_address: str | None = Field(default=None, max_length=80)
    source_file_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")
        return self


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["fact", "inference"]
    statement: str
    evidence_refs: list[str]


class Alternative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis: str
    required_checks: list[str]


class ModelAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    judgement_level: Literal["明确证据", "较强迹象", "存在迹象但歧义", "证据不足", "存在相反证据"]
    claims: list[Claim]
    counter_evidence_refs: list[str]
    alternatives: list[Alternative]
    confidence: Literal["低", "中", "高"]


class AnalysisOut(BaseModel):
    id: uuid.UUID
    subjects: list[str] = Field(default_factory=list)
    judgement_level: Literal["明确证据", "较强迹象", "存在迹象但歧义", "证据不足", "存在相反证据"]
    claims: list[Claim]
    evidence: list[SearchHit]
    counter_evidence: list[SearchHit]
    alternatives: list[Alternative]
    confidence: Literal["低", "中", "高"]
    review_status: Literal["待复核"] = "待复核"
    engine: str


class AnalysisTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    case_id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    analysis_id: uuid.UUID | None
    error: str | None
    result: AnalysisOut | None = None
    created_at: datetime
    updated_at: datetime


class ReviewCreate(BaseModel):
    decision: Literal["采纳", "修改", "驳回"]
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str = Field(default="", max_length=4000)
    revised_result: ModelAnalysis | None = None

    @model_validator(mode="after")
    def validate_revision(self):
        if self.decision == "修改" and self.revised_result is None:
            raise ValueError("修改研判时必须提交 revised_result")
        if self.decision != "修改" and self.revised_result is not None:
            raise ValueError("只有修改研判时才能提交 revised_result")
        return self


class ReviewOut(ReviewCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    analysis_id: uuid.UUID
    created_at: datetime


class FriendScreenIn(BaseModel):
    names: list[str] = Field(default_factory=list, min_length=2)


class PersonUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    gender: str | None = Field(default=None, max_length=20)
    id_card: str | None = Field(default=None, max_length=80)
    delivery_address: str | None = Field(default=None, max_length=2000)
    takeout_address: str | None = Field(default=None, max_length=2000)
    wechat_id: str | None = Field(default=None, max_length=120)
    wechat_alias: str | None = Field(default=None, max_length=120)
    original_wechat_id: str | None = Field(default=None, max_length=120)
    wx_nickname: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    remark: str | None = Field(default=None, max_length=200)
    as_subject: bool | None = None


class PersonCreateIn(PersonUpdateIn):
    name: str = Field(min_length=1, max_length=120)
    owner_name: str = ""
    relation: str = Field(default="关系人", max_length=80)


class RelationUpdateIn(BaseModel):
    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=120)
    relation: str = Field(default="关系人", min_length=1, max_length=80)
    removed: bool = False
