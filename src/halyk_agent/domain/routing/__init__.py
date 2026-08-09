"""Stage 5B scenario and entity routing domain package."""

from halyk_agent.domain.routing.accounts import (
    AccountVocabulary,
    build_account_vocabulary,
)
from halyk_agent.domain.routing.engine import run_routing
from halyk_agent.domain.routing.models import (
    AccountIdentity,
    BorrowerIdentity,
    CompanyAlias,
    CounterpartyIdentity,
    DocumentEntityLink,
    EntityResolutionConflict,
    LedgerRow,
    ResolutionConfidence,
    ResolutionMethod,
    RoutingManifest,
    RoutingReport,
    ScenarioIdentity,
    ScenarioRoutingRecord,
    TransactionEntityLink,
    TxnIdParserConfig,
)
from halyk_agent.domain.routing.normalize import names_match_exact, normalize_legal_name
from halyk_agent.domain.routing.scenarios import discover_scenarios

__all__ = [
    "AccountIdentity",
    "AccountVocabulary",
    "BorrowerIdentity",
    "CompanyAlias",
    "CounterpartyIdentity",
    "DocumentEntityLink",
    "EntityResolutionConflict",
    "LedgerRow",
    "ResolutionConfidence",
    "ResolutionMethod",
    "RoutingManifest",
    "RoutingReport",
    "ScenarioIdentity",
    "ScenarioRoutingRecord",
    "TransactionEntityLink",
    "TxnIdParserConfig",
    "build_account_vocabulary",
    "discover_scenarios",
    "names_match_exact",
    "normalize_legal_name",
    "run_routing",
]
