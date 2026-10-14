"""Pydantic models for each stage's saved output. Later stages add their own models here."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class CompanyInfo(BaseModel):
    input_name: str
    name: str
    ticker: str
    cik: int


class FilingInfo(BaseModel):
    form: str
    accession: str
    fiscal_year: str
    period_end: date
    filing_date: date
    url: str
    cover_fiscal_year_end: date
    fiscal_year_check: Literal["passed"]


class SectionInfo(BaseModel):
    item: str
    title: str
    word_count: int
    chunk_count: int
    first_chunk_id: str
    last_chunk_id: str


class EightKInfo(BaseModel):
    accession: str
    filing_date: date
    event_date: date | None  # EDGAR report date: date of the earliest event reported
    items: list[str]
    item_labels: list[str]
    url: str
    exhibit_99_1_url: str | None = None
    chunk_count: int


class EightKSet(BaseModel):
    events_window_from: date
    events_window_to: date
    events: list[EightKInfo]
    earnings_releases: list[EightKInfo]


class HeadlineInfo(BaseModel):
    chunk_id: str
    headline: str
    url: str
    date: date
    domain: str
    matched_aliases: list[str]
    low_signal: bool  # from a configured low-signal domain (config/news_sources.yaml)
    eligible_for_extract: bool  # False: no company/alias match, or low_signal; kept for review only


class NewsInfo(BaseModel):
    provider: Literal["gdelt"]
    status: Literal["ok", "empty", "failed", "skipped"]
    query: str
    window_from: date
    window_to: date
    error: str | None = None
    aliases: list[str]  # names, brands, and ticker used for eligibility
    low_signal_domains: list[str]
    from_cache: bool  # True if the GDELT response came from the per-as-of-date cache
    returned_count: int  # unique headlines GDELT returned before ranking and the cap
    eligible_count: int
    low_signal_count: int  # headlines kept that match an alias but come from a low-signal domain
    headlines: list[HeadlineInfo]


class SourceInfo(BaseModel):
    source_id: str
    source_type: Literal["filing", "earnings_call", "news", "company_site", "product_docs"]
    title: str
    url: str
    date: date  # filing or publication date
    event_date: date | None = None  # 8-Ks only: EDGAR report date
    location: str | None = None
    signal: Literal["primary", "weak"]
    chunk_count: int


class RetrieveManifest(BaseModel):
    stage: Literal["retrieve"] = "retrieve"
    generated_at: datetime
    as_of: date
    company: CompanyInfo
    filing: FilingInfo
    sections: list[SectionInfo]
    eight_ks: EightKSet
    news: NewsInfo
    sources: list[SourceInfo]
    total_chunks: int
    request_count: int
    warnings: list[str]


# ---------------------------------------------------------------- Extract stage
# Two layers: what the model returns (LLM*), and what is stored after the guards run.
# The model never sets provenance, labels, dates, URLs, titles of sources, or flags.

InitiativeTheme = Literal[
    "growth", "efficiency_cost", "technology_ai", "product", "market_expansion",
    "b2b_partnerships", "m_and_a_integration", "financing_capital_return", "other",
]  # financing_capital_return is excluded by code: financing and capital return are not initiatives
ChallengeCategory = Literal[
    "competition", "regulatory_legal", "security_technology", "demand_macro", "organizational", "other",
]
ChangeType = Literal[
    "leadership_change", "reorganization", "restructuring_layoffs", "acquisition", "divestiture",
    "financing", "other",
]


class LLMCitation(BaseModel):
    chunk_id: str
    evidence: str  # copied word for word from the chunk: 6-14 words, or the whole chunk if shorter


# Legacy item-level format (one free-text block per item). Read-only: used only to replay the saved
# dev-2 and dev-3 responses until the sentence-level format has a baseline run.

class LegacyLLMInitiative(BaseModel):
    title: str
    text: str
    theme: InitiativeTheme
    citations: list[LLMCitation]
    model_inference: bool
    validation_question: str


class LegacyLLMChallenge(BaseModel):
    title: str
    text: str
    category: ChallengeCategory
    citations: list[LLMCitation]
    model_inference: bool
    validation_question: str


class LegacyLLMChange(BaseModel):
    title: str
    text: str
    change_type: ChangeType
    citations: list[LLMCitation]
    model_inference: bool
    validation_question: str


class LegacyLLMInitiativesOutput(BaseModel):
    initiatives: list[LegacyLLMInitiative]


class LegacyLLMChallengesChangesOutput(BaseModel):
    challenges: list[LegacyLLMChallenge]
    changes: list[LegacyLLMChange]


# Current model output format: every sentence carries its own citations (1-2), verified by the guards.
# Code builds the item text from the sentences that keep at least one verified citation.

class LLMSentence(BaseModel):
    text: str  # one claim, one short sentence
    citations: list[LLMCitation]  # 1-2; the evidence comes from the paragraph that states this claim


class LLMInitiative(BaseModel):
    title: str  # a label; scored against the kept sentences, never dropped
    theme: InitiativeTheme
    sentences: list[LLMSentence]
    validation_question: str


class LLMChallenge(BaseModel):
    title: str
    category: ChallengeCategory
    sentences: list[LLMSentence]
    validation_question: str


class LLMChange(BaseModel):
    title: str
    change_type: ChangeType
    sentences: list[LLMSentence]
    validation_question: str


class LLMInitiativesOutput(BaseModel):
    initiatives: list[LLMInitiative]


class LLMChallengesChangesOutput(BaseModel):
    challenges: list[LLMChallenge]
    changes: list[LLMChange]


class VerifiedCitation(BaseModel):
    chunk_id: str
    source_id: str
    evidence: str
    source_type: str
    title: str
    location: str | None = None
    url: str
    date: date
    event_date: date | None = None
    uncorroborated: bool = False  # news headline that no cited filing corroborates (kept on Risk Factors challenges)
    evidence_truncated: bool = False  # phrase was over 14 words but found whole; only its first 14 words are stored


class RejectedCitation(BaseModel):
    chunk_id: str
    evidence: str
    reason: str


FlagType = Literal[
    "price_review", "evidence_not_found", "long_quote", "personal_title", "personal_name", "source_rule",
    "partial_support", "biography", "unsupported_term", "unsupported_statement", "results_review",
    "outcome_promise", "implies_customer", "rationale_adds_facts", "adds_facts", "generic_criterion", "goal_clause",
]


class ItemFlag(BaseModel):
    type: FlagType
    detail: str


class SentenceRecord(BaseModel):
    text: str
    citations: list[VerifiedCitation]  # this sentence's own verified citations (1-2)
    rejected_citations: list[RejectedCitation] = []  # a second citation that failed; the sentence kept another
    support_score: float | None = None  # statement support against this sentence's own citations only


class DroppedSentence(BaseModel):
    text: str
    rejected_citations: list[RejectedCitation]
    reason: str


class ExtractItem(BaseModel):
    id: str
    kind: Literal["initiative", "challenge", "change"]
    title: str
    text: str
    provenance: Literal["sourced", "inferred"]
    labels: list[str]
    flags: list[ItemFlag]
    citations: list[VerifiedCitation]  # newest source first
    rejected_citations: list[RejectedCitation]
    model_inference: bool
    validation_question: str
    extra_validation_questions: list[str] = []  # added by code, e.g. to confirm an uncorroborated headline
    theme: InitiativeTheme | None = None
    category: ChallengeCategory | None = None
    origin: Literal["risk_factor", "public_signal"] | None = None  # challenges; set by code from citations
    change_type: ChangeType | None = None
    event_date: date | None = None  # changes; earliest 8-K event date among verified citations
    sources_note: str | None = None  # e.g. newer earnings release preferred over the 10-K
    sentences: list[SentenceRecord] = []  # sentence format: text == " ".join(s.text for s in sentences)
    dropped_sentences: list[DroppedSentence] = []  # sentence format: never part of text
    legacy: bool = False  # True when produced from a legacy item-level response (replay only)
    title_support_score: float | None = None  # title (a label) scored against the kept sentences; shown, not flagged


class UsageRecord(BaseModel):
    call: str
    model: str
    effort: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float
    estimated_input_tokens: int
    estimated_cost_usd: float


class ExtractOutput(BaseModel):
    stage: Literal["extract"] = "extract"
    generated_at: datetime
    company: str
    profile: str
    retrieve_generated_at: datetime
    prices_as_of: str
    initiatives: list[ExtractItem]
    challenges: list[ExtractItem]
    changes: list[ExtractItem]
    usage: list[UsageRecord]
    total_cost_usd: float
    warnings: list[str]
    replayed_from: str | None = None  # set when guards were re-applied to saved responses (no API call)
    response_format: Literal["sentences", "legacy"] = "sentences"


class GuardEntry(BaseModel):
    item_id: str | None = None
    kind: str
    title: str
    reason: str


class GuardReport(BaseModel):
    stage: Literal["extract"] = "extract"
    generated_at: datetime
    rejected_citations: list[GuardEntry]
    flagged: list[GuardEntry]
    excluded: list[GuardEntry]  # removed by a rule (analyst ratings, stock moves, source rules)
    overflow: list[GuardEntry]  # valid items beyond the per-kind cap
    dropped_sentences: list[GuardEntry] = []  # sentences left out of item text because no citation verified


# ---------------------------------------------------------------- Map stage
# Each Extract initiative maps to config capabilities (full / partial) or no_fit. Benefit sentences cite
# capability claims (clauses of the config's own wording); code verifies every citation.

Fit = Literal["full", "partial", "no_fit"]
PLAN_CONFIRMATION_BLANK = "[CSM to confirm which plan includes these capabilities]"


class LLMClaimCitation(BaseModel):
    claim_id: str  # e.g. "JIRA-AUTOMATION#d1"
    evidence: str  # copied word for word from the claim text


class LLMBenefitSentence(BaseModel):
    text: str
    claims: list[LLMClaimCitation]  # 1-2


class LLMMapping(BaseModel):
    initiative_id: str
    fit: Fit
    sentences: list[LLMBenefitSentence]  # empty for no_fit
    gap_ids: list[str]  # GAP-* ids from the config's not_covered list (no_fit)
    no_fit_reason: str  # required for no_fit unless gap_ids explain it; empty otherwise
    fit_rationale: str  # one sentence; an inference, scored against the initiative and the cited claims
    validation_question: str


class LLMMapOutput(BaseModel):
    mappings: list[LLMMapping]


class ProductCitation(BaseModel):
    claim_id: str
    capability_id: str
    capability_name: str
    evidence: str
    source_urls: list[str]
    verified_on: date | None = None
    verified_by: str | None = None
    unconfirmed: bool = False  # cites a claim the config marks unconfirmed or inferred
    evidence_truncated: bool = False


class BenefitSentence(BaseModel):
    text: str
    citations: list[ProductCitation]
    rejected_claims: list[RejectedCitation] = []
    support_score: float | None = None


class DroppedBenefitSentence(BaseModel):
    text: str
    rejected_claims: list[RejectedCitation]
    reason: str


class KeyBenefit(BaseModel):
    id: str
    initiative_id: str
    initiative_title: str
    initiative_provenance: Literal["sourced", "inferred"]
    owner: Literal["claude"] = "claude"
    provenance: Literal["inferred"] = "inferred"  # the fit is a judgment
    fit: Fit
    capability_ids: list[str]
    text: str  # " ".join of kept sentences
    sentences: list[BenefitSentence]
    dropped_sentences: list[DroppedBenefitSentence]
    no_fit_reason: str | None = None
    gap_ids: list[str] = []
    fit_rationale: str
    fit_rationale_label: Literal["inference"] = "inference"
    fit_rationale_support: float | None = None
    labels: list[str]  # carried from the initiative, plus Map labels
    flags: list[ItemFlag]
    validation_question: str
    plan_confirmation: str = PLAN_CONFIRMATION_BLANK


class WithheldClaim(BaseModel):
    capability_id: str
    text: str
    reason: str


class MapGuardReport(BaseModel):
    stage: Literal["map"] = "map"
    generated_at: datetime
    rejected_claims: list[GuardEntry]
    dropped_sentences: list[GuardEntry]
    flagged: list[GuardEntry]
    excluded: list[GuardEntry]  # mappings removed by a rule
    downgraded: list[GuardEntry]  # full/partial with no verified claim, recorded as no_fit
    unmapped_initiatives: list[str]  # Extract initiatives with no mapping in the response


class MapOutput(BaseModel):
    stage: Literal["map"] = "map"
    generated_at: datetime
    company: str
    profile: str
    extract_generated_at: datetime
    config_file: str
    withheld_claims: list[WithheldClaim]  # plan-tier lines and clauses never shown to the model
    key_benefits: list[KeyBenefit]
    fit_distribution: dict[str, int]
    usage: list[UsageRecord]
    total_cost_usd: float
    prices_as_of: str
    warnings: list[str]
    replayed_from: str | None = None


# ---------------------------------------------------------------- Draft stage
# Objectives for initiatives Map fitted (full or partial). The model writes only success criteria, each tied to
# Extract or Map sentences by reference id; code builds objective titles, tasks (from config/draft_tasks.yaml),
# targets, and the reference table, and checks every reference.

Window = Literal["Day 0-30", "Day 31-60", "Day 61-90"]
WINDOWS: tuple[str, ...] = ("Day 0-30", "Day 31-60", "Day 61-90")
CRITERION_BLANK = "[CSM to complete: success criteria for this objective]"
OBJECTIVES_BLANK = "[CSM to complete: add objectives for initiatives the tool did not map]"
TARGET_BLANK = "[CSM to complete: set after baseline]"
COMPANY_MEASURE_LABEL = "company measure; not attributable to the product"


class LLMCriterion(BaseModel):
    initiative_id: str
    text: str
    refs: list[str]  # I-01.s2 (Extract sentence) or B-01.s1 (Map benefit sentence), same initiative only
    criterion_type: Literal["business_outcome", "product_usage"]
    indicator_type: Literal["leading", "lagging"]
    measure: str  # what is counted
    direction: Literal["increase", "decrease", "reach"]
    baseline_question: str
    validation_question: str


class LLMTaskPick(BaseModel):
    initiative_id: str
    task_id: str
    capability_id: str  # "" when the task names no capability
    responsible_role: str
    window: Window


class LLMSkipped(BaseModel):
    initiative_id: str
    reason: str


class LLMDraftOutput(BaseModel):
    """Flat form used by assembly. The model returns one entry per initiative (see stages/draft.py flatten)."""
    criteria: list[LLMCriterion]
    tasks: list[LLMTaskPick]
    skipped: list[LLMSkipped] = []  # entries with no criteria, with the model's reason


class SuccessCriterion(BaseModel):
    id: str
    text: str
    owner: Literal["shared", "csm"] = "shared"
    provenance: Literal["inferred", "csm-input"] = "inferred"
    refs: list[str] = []
    citations: list[VerifiedCitation] = []  # from referenced Extract sentences
    product_citations: list[ProductCitation] = []  # from referenced Map benefit sentences
    criterion_type: Literal["business_outcome", "product_usage"] | None = None
    indicator_type: Literal["leading", "lagging"] | None = None
    measure: str | None = None
    direction: Literal["increase", "decrease", "reach"] | None = None
    proposed_target: str | None = None  # built by code; the number is always a CSM blank
    needs_baseline: bool
    baseline_question: str | None = None
    validation_question: str
    support_score: float | None = None
    labels: list[str] = []
    flags: list[ItemFlag] = []


class DraftTask(BaseModel):
    id: str
    task_id: str  # catalog id
    text: str  # rendered from the catalog template
    responsible_role: str
    window: Window
    is_proposed: Literal[True] = True
    provenance: Literal["inferred"] = "inferred"
    validation_question: str


class Objective(BaseModel):
    id: str
    title: str  # template, neutral wording
    owner: Literal["shared"] = "shared"
    provenance: Literal["inferred"] = "inferred"
    linked_initiative_ids: list[str]
    benefit_id: str
    capability_ids: list[str]
    fit: Literal["full", "partial"]
    labels: list[str]
    success_criteria: list[SuccessCriterion]
    tasks: list[DraftTask]


class DraftGuardReport(BaseModel):
    stage: Literal["draft"] = "draft"
    generated_at: datetime
    dropped_criteria: list[GuardEntry]
    rejected_refs: list[GuardEntry]
    flagged: list[GuardEntry]
    rejected_tasks: list[GuardEntry]
    fallbacks: list[GuardEntry]  # objectives given the CSM blank criterion or the default task
    skipped_initiatives: list[GuardEntry] = []  # eligible initiatives the model returned no criteria for


class DraftOutput(BaseModel):
    stage: Literal["draft"] = "draft"
    generated_at: datetime
    company: str
    profile: str
    extract_generated_at: datetime
    map_generated_at: datetime
    map_profile: str
    map_replayed_from: str | None = None
    objectives: list[Objective]
    objectives_blank: str = OBJECTIVES_BLANK
    not_drafted: list[str]  # initiative ids with no_fit: no objective
    no_objectives_reason: str | None = None  # set when Draft made no model call because Map found no fit
    usage: list[UsageRecord]
    total_cost_usd: float
    prices_as_of: str
    warnings: list[str]
    replayed_from: str | None = None
