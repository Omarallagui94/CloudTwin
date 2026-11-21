"""Registre des règles actives. Ajouter une règle = l'importer et l'ajouter ici."""

from analysis.rules.base import Rule
from analysis.rules.resilience.res_001_single_replica import SingleReplicaRule
from analysis.rules.resilience.res_002_rds_multi_az import RdsMultiAzRule
from analysis.rules.resilience.res_003_deleted_dependency import DeletedDependencyRule
from analysis.rules.security.iam_001_full_admin import FullAdminPolicyRule
from analysis.rules.security.iam_002_sensitive_permission import SensitivePermissionRule
from analysis.rules.security.iam_003_escalation_path import EscalationPathRule
from analysis.rules.security.net_001_public_admin_port import PublicAdminPortRule
from analysis.rules.security.net_002_public_security_group import PublicSecurityGroupRule
from analysis.rules.security.net_003_public_database_port import PublicDatabasePortRule
from analysis.rules.security.net_004_public_service import PublicServiceRule

ALL_RULES: list[Rule] = [
    SingleReplicaRule(),
    RdsMultiAzRule(),
    DeletedDependencyRule(),
    PublicAdminPortRule(),
    PublicSecurityGroupRule(),
    PublicDatabasePortRule(),
    PublicServiceRule(),
    FullAdminPolicyRule(),
    SensitivePermissionRule(),
    EscalationPathRule(),
]
