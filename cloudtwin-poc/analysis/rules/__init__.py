"""Registre des règles actives. Ajouter une règle = l'importer et l'ajouter ici."""

from analysis.rules.base import Rule
from analysis.rules.resilience.res_001_single_replica import SingleReplicaRule
from analysis.rules.resilience.res_002_rds_multi_az import RdsMultiAzRule
from analysis.rules.resilience.res_003_deleted_dependency import DeletedDependencyRule
from analysis.rules.security.net_002_public_security_group import PublicSecurityGroupRule
from analysis.rules.security.net_003_public_database_port import PublicDatabasePortRule
from analysis.rules.security.net_004_public_service import PublicServiceRule

ALL_RULES: list[Rule] = [
    SingleReplicaRule(),
    RdsMultiAzRule(),
    DeletedDependencyRule(),
    PublicSecurityGroupRule(),
    PublicDatabasePortRule(),
    PublicServiceRule(),
]
