from __future__ import annotations

from . import errors
from .approval_policy import ApprovalDecision, ApprovalPolicy
from .approval_types import ApprovalEvent, ApprovalResult, ApprovalRound
from .auth import DEFAULT_AUTH_FILE, load_auth_data
from .auth_browser import BrowserLoginResult, browser_login, default_browser_profile_dir
from .auth_refresh import AuthRefreshResult
from .auth_status import AuthStatus, get_auth_status
from .browser_native_install import (
    BrowserNativeInstallResult,
    EXTENSION_ID as BROWSER_NATIVE_EXTENSION_ID,
    browser_native_extension_dir,
    install_native_messaging_host,
)
from .browser_native_provider import (
    BrowserNativeBridgeStatus,
    BrowserNativeTurnProvider,
    BrowserNativeTurnResult,
)
from .browser_sentinel import ZendriverSentinelBundleProvider
from .client import ChatGPTWebClient, _original_send as _original_send
from .conversation_prepare import PrepareResult, prepare_text_turn
from .exceptions import (
    AuthError,
    ConversationTimeoutError,
    MediaError,
    PayloadValidationError,
    RequestError,
    WebChatAdapterError,
)
from .model_registry import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_MODEL as DEFAULT_THINKING_MODEL,
    MODEL_ALIASES as MODEL_ALIASES,
)
from .payload_builder import PayloadBuilder
from .payload_validation import validate_payload
from .policy_approval import ApprovalDeniedError
from .product_capabilities import (
    ORDINARY_CHATGPT_PRODUCT_SEMANTICS,
    PRODUCT_CAPABILITY_NAMES,
    CapabilityOwner,
    CapabilityState,
    ProductCapabilities,
    ProductCapability,
)
from .product_contract import ProductRuntimeContract, product_runtime_contract
from .product_observations import (
    ProductActivityObservation,
    ProductCitationObservation,
    ProductObservationKind,
    ProductObservationPhase,
    ProductRequiredActionObservation,
    ProductSourceObservation,
    StructuredProductObservation,
)
from .product_provenance import (
    CompletionSource,
    ProductCompletionProvenance,
    ProductExecutionProvenance,
    ProductIdentityProvenance,
)
from .product_runtime import (
    BROWSER_OWNED_PRODUCT_TRANSPORT,
    DEFAULT_PRODUCT_TRANSPORT,
    SUPPORTED_PRODUCT_TRANSPORTS,
    ChatGPTProductRuntime,
    ProductRuntimeExecution,
    ProductRuntimeHealth,
    assemble_product_runtime,
)
from .product_submission import (
    ProductSubmissionAck,
    ProductSubmissionProvenance,
    SubmissionEvidenceSource,
)
from .product_support import (
    PRODUCT_RUNTIME_CONTRACT_SCHEMA,
    ProductTransportSupportTier,
    product_transport_support_tier,
)
from .product_transport import CanonicalConversationClient, ProductWriteTransport
from .product_ui_liveness import BrowserUILivenessObservation, BrowserUILivenessState
from .public_surface import (
    PRIMARY_PRODUCT_RUNTIME_EXPORTS,
    PUBLIC_SURFACE_CLASSIFICATION,
    PUBLIC_SURFACE_TIERS,
    PublicSurfaceTier,
    public_surface_tier,
)
from .required_action import RequiredAction, find_required_action
from .sentinel_requirements import (
    OBSERVED_FINALIZE_REQUEST_KEYS,
    OBSERVED_FINALIZE_RESPONSE_KEYS,
    SentinelPrepareProbeResult,
    probe_sentinel_requirements_prepare,
)
from .sentinel_transaction import (
    FinalizedSentinelBundle,
    SentinelBundleProvider,
    SentinelChallengeContext,
    SentinelChallengeEvidence,
    SentinelChallengeProvider,
)
from .types import (
    AttachedConversation,
    AuthData,
    ChatConversation,
    ChatMessage,
    ChatMetrics,
    ChatRequestDiagnostics,
    ChatResponse,
    ConversationRef,
    ConversationStatus,
    MediaItem,
    MediaSource,
    PendingApproval,
    WaitResult,
)

WebChatClient = ChatGPTWebClient

# The historical core prefix remains import-compatible. The forward-looking
# production API is PRODUCT_RUNTIME_EXPORTS below.
CORE_PUBLIC_API = [
    "ChatGPTWebClient",
    "WebChatClient",
    "ChatConversation",
    "AttachedConversation",
    "ChatMessage",
    "ConversationStatus",
    "PendingApproval",
    "ChatResponse",
    "ChatMetrics",
    "ChatRequestDiagnostics",
    "AuthData",
    "errors",
]

PRODUCT_RUNTIME_EXPORTS = list(PRIMARY_PRODUCT_RUNTIME_EXPORTS)

ERROR_EXPORTS = [
    "WebChatAdapterError",
    "AuthError",
    "ConversationTimeoutError",
    "MediaError",
    "PayloadValidationError",
    "RequestError",
]

ADVANCED_HELPERS = [
    "ConversationRef",
    "WaitResult",
]

MEDIA_EXPORTS = [
    "MediaItem",
    "MediaSource",
]

SUPPORT_EXPORTS = [
    "AuthStatus",
    "AuthRefreshResult",
    "BrowserLoginResult",
    "DEFAULT_AUTH_FILE",
    "DEFAULT_MODEL",
    "browser_login",
    "default_browser_profile_dir",
    "get_auth_status",
    "load_auth_data",
]

PUBLIC_SURFACE_METADATA_EXPORTS = [
    "PublicSurfaceTier",
    "PUBLIC_SURFACE_TIERS",
    "PUBLIC_SURFACE_CLASSIFICATION",
    "public_surface_tier",
]

EXPERIMENTAL_APPROVAL_EXPORTS = [
    "ApprovalDecision",
    "ApprovalDeniedError",
    "ApprovalEvent",
    "ApprovalPolicy",
    "ApprovalResult",
    "ApprovalRound",
]

EXPERIMENTAL_REQUIRED_ACTION_EXPORTS = [
    "RequiredAction",
    "find_required_action",
]

EXPERIMENTAL_RAW_PAYLOAD_EXPORTS = [
    "PayloadBuilder",
    "validate_payload",
]

EXPERIMENTAL_PREPARE_EXPORTS = [
    "PrepareResult",
    "prepare_text_turn",
]

# Kept import-compatible, but classified as research/diagnostic.
EXPERIMENTAL_SENTINEL_EXPORTS = [
    "FinalizedSentinelBundle",
    "OBSERVED_FINALIZE_REQUEST_KEYS",
    "OBSERVED_FINALIZE_RESPONSE_KEYS",
    "SentinelBundleProvider",
    "SentinelChallengeContext",
    "SentinelChallengeEvidence",
    "SentinelChallengeProvider",
    "SentinelPrepareProbeResult",
    "ZendriverSentinelBundleProvider",
    "probe_sentinel_requirements_prepare",
]

# Kept import-compatible, but direct low-level use is research/diagnostic. The
# production runtime consumes the browser-native implementation behind its
# ProductWriteTransport boundary.
EXPERIMENTAL_BROWSER_NATIVE_EXPORTS = [
    "BROWSER_NATIVE_EXTENSION_ID",
    "BrowserNativeBridgeStatus",
    "BrowserNativeInstallResult",
    "BrowserNativeTurnProvider",
    "BrowserNativeTurnResult",
    "browser_native_extension_dir",
    "install_native_messaging_host",
]

# Keep this list literal. Besides making the supported root surface obvious to
# readers, a literal __all__ lets static tooling verify that imports are deliberate
# re-exports rather than accidental unused dependencies.
__all__ = [
    # Historical prefix retained for compatibility.
    "ChatGPTWebClient",
    "WebChatClient",
    "ChatConversation",
    "AttachedConversation",
    "ChatMessage",
    "ConversationStatus",
    "PendingApproval",
    "ChatResponse",
    "ChatMetrics",
    "ChatRequestDiagnostics",
    "AuthData",
    "errors",
    # Primary production surface is intentionally promoted before legacy extras.
    "BROWSER_OWNED_PRODUCT_TRANSPORT",
    "DEFAULT_PRODUCT_TRANSPORT",
    "SUPPORTED_PRODUCT_TRANSPORTS",
    "PRODUCT_RUNTIME_CONTRACT_SCHEMA",
    "ProductTransportSupportTier",
    "product_transport_support_tier",
    "ProductRuntimeContract",
    "product_runtime_contract",
    "ORDINARY_CHATGPT_PRODUCT_SEMANTICS",
    "PRODUCT_CAPABILITY_NAMES",
    "CapabilityState",
    "CapabilityOwner",
    "ProductCapability",
    "ProductCapabilities",
    "CompletionSource",
    "ProductCompletionProvenance",
    "ProductIdentityProvenance",
    "ProductExecutionProvenance",
    "SubmissionEvidenceSource",
    "ProductSubmissionProvenance",
    "ProductSubmissionAck",
    "BrowserUILivenessState",
    "BrowserUILivenessObservation",
    "ProductObservationKind",
    "ProductObservationPhase",
    "ProductActivityObservation",
    "ProductSourceObservation",
    "ProductCitationObservation",
    "ProductRequiredActionObservation",
    "StructuredProductObservation",
    "CanonicalConversationClient",
    "ProductWriteTransport",
    "ChatGPTProductRuntime",
    "ProductRuntimeExecution",
    "ProductRuntimeHealth",
    "assemble_product_runtime",
    "WebChatAdapterError",
    "AuthError",
    "ConversationTimeoutError",
    "MediaError",
    "PayloadValidationError",
    "RequestError",
    "ConversationRef",
    "WaitResult",
    "MediaItem",
    "MediaSource",
    "AuthStatus",
    "AuthRefreshResult",
    "BrowserLoginResult",
    "DEFAULT_AUTH_FILE",
    "DEFAULT_MODEL",
    "browser_login",
    "default_browser_profile_dir",
    "get_auth_status",
    "load_auth_data",
    "PublicSurfaceTier",
    "PUBLIC_SURFACE_TIERS",
    "PUBLIC_SURFACE_CLASSIFICATION",
    "public_surface_tier",
    # Lower-support-level compatibility exports remain available.
    "ApprovalDecision",
    "ApprovalDeniedError",
    "ApprovalEvent",
    "ApprovalPolicy",
    "ApprovalResult",
    "ApprovalRound",
    "RequiredAction",
    "find_required_action",
    "PayloadBuilder",
    "validate_payload",
    "PrepareResult",
    "prepare_text_turn",
    "FinalizedSentinelBundle",
    "OBSERVED_FINALIZE_REQUEST_KEYS",
    "OBSERVED_FINALIZE_RESPONSE_KEYS",
    "SentinelBundleProvider",
    "SentinelChallengeContext",
    "SentinelChallengeEvidence",
    "SentinelChallengeProvider",
    "SentinelPrepareProbeResult",
    "ZendriverSentinelBundleProvider",
    "probe_sentinel_requirements_prepare",
    "BROWSER_NATIVE_EXTENSION_ID",
    "BrowserNativeBridgeStatus",
    "BrowserNativeInstallResult",
    "BrowserNativeTurnProvider",
    "BrowserNativeTurnResult",
    "browser_native_extension_dir",
    "install_native_messaging_host",
]
