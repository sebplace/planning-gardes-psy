"""Modèles de persistance."""

from .base import (  # noqa: F401
    AssignmentOrigin,
    AvailabilitySource,
    Base,
    CampaignState,
    CandidacyState,
    Color,
    CoverageMode,
    Enforcement,
    EngineRunStatus,
    HandoverState,
    HolidayRequirement,
    Line,
    Module,
    ScheduleState,
    Status,
    SubmissionState,
    SwapState,
    WaveKind,
    WaveState,
    utcnow,
)
from .accounts import (  # noqa: F401
    ActivityPeriod,
    Eligibility,
    GardeWeightHistory,
    ProfessionalProfile,
    QuotiteHistory,
    User,
)
from .catalog import (  # noqa: F401
    CoveragePost,
    ExchangeClass,
    GardeOccurrence,
    GardeType,
    HolidayPair,
    HolidayPairMember,
    Quarter,
    QuotaCategory,
    Year,
)
from .quotas import (  # noqa: F401
    Exemption,
    MonthlyCap,
    QuotaAdjustment,
    QuotaTarget,
    QuotaTargetHistory,
    RestRule,
)
from .campaign import Availability, Campaign, Submission  # noqa: F401
from .rest import (  # noqa: F401
    DUREE_CONTINUE_MAX_HEURES,
    DUREE_RECUPERATION_HEURES,
    SEUIL_RECUPERATION_HEURES,
    OnSiteReport,
    RecoveryProposal,
    WeekendBlockRequest,
)
from .permissions import (  # noqa: F401
    CHEF_SERVICE,
    CONSULTATION_AUDIT,
    GESTION_COMPTES,
    LIBELLES,
    PERMISSIONS,
    PUBLICATION,
    RESP_L1,
    RESP_L2,
    PermissionGrant,
)
from .planning import (  # noqa: F401
    Assignment,
    EngineRun,
    ManualCorrection,
    Proposal,
    ProposalAssignment,
    RuleProfileRow,
    ScheduleVersion,
    UrgencyProfile,
)
from .handover import (  # noqa: F401
    Candidacy,
    Draw,
    HandoverRequest,
    HandoverWave,
    SwapProposal,
    WaveSolicitation,
)
from .common import AuditEvent, Notification, Scenario, ScenarioResult  # noqa: F401

__all__ = [name for name in dir() if not name.startswith("_")]
